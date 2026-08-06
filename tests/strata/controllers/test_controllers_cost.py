#!/usr/bin/env python3
"""Unit tests for CostController."""

from unittest.mock import MagicMock, patch

from strata.controllers.cost_controller import CostController

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_estimator(available: bool = True, breakdown_result=None, diff_result=None, error: str = ""):
    """Return a mock ICostEstimator integration."""
    estimator = MagicMock()
    estimator.ensure_available.return_value = (available, error if not available else "")
    if breakdown_result is not None:
        estimator.breakdown.return_value = breakdown_result
    if diff_result is not None:
        estimator.diff.return_value = diff_result
    return estimator


def _make_iac(name: str = "terraform", source_path: str = "terraform"):
    """Return a mock WorkspaceIacModel."""
    iac = MagicMock()
    iac.name = name
    iac.provisioner = "terraform"
    iac.source = MagicMock()
    iac.source.source_path = source_path
    iac.source.target_path = None
    return iac


def _make_deployment_service(provisioners=None, deployment_name="myapp", version="1.0.0"):
    """Return a mock DeploymentService with workspace service."""
    ds = MagicMock()
    ds.get_name.return_value = deployment_name
    ds.get_version.return_value = version
    ds.get_build_path.side_effect = lambda bp: bp / f"{deployment_name}-{version}"

    ws_service = MagicMock()
    ws_model = MagicMock()
    ws_model.spec.provisioners = provisioners or []
    ws_service.model = ws_model
    ds.get_workspace_service.return_value = ws_service
    return ds


_SAMPLE_BREAKDOWN = {
    "version": "0.1",
    "breakdown": {
        "resources": [{"name": "azurerm_mssql_database.main", "monthlyCost": "1202.40"}],
        "totalMonthlyCost": "1202.40",
    },
}

_SAMPLE_DIFF = {
    "version": "0.1",
    "diff": {
        "resources": [],
        "totalMonthlyCost": "200.00",
        "pastTotalMonthlyCost": "0.00",
    },
}


# ---------------------------------------------------------------------------
# _get_terraform_provisioners
# ---------------------------------------------------------------------------


class TestGetTerraformProvisioners:
    def test_returns_empty_when_no_workspace(self):
        ctrl = CostController()
        ds = MagicMock()
        ds.get_workspace_service.return_value = None
        assert ctrl._get_terraform_provisioners(ds) == []

    def test_returns_only_terraform_provisioners(self):
        tf_iac = _make_iac("infra")
        helm_iac = MagicMock()
        helm_iac.name = "charts"
        helm_iac.provisioner = "helm"
        ds = _make_deployment_service(provisioners=[tf_iac, helm_iac])
        ctrl = CostController()
        result = ctrl._get_terraform_provisioners(ds)
        assert len(result) == 1
        assert result[0].name == "infra"

    def test_filter_by_name(self):
        iac1 = _make_iac("infra", "terraform/infra")
        iac2 = _make_iac("platform", "terraform/platform")
        ds = _make_deployment_service(provisioners=[iac1, iac2])
        ctrl = CostController()
        result = ctrl._get_terraform_provisioners(ds, provisioner_filter="platform")
        assert len(result) == 1
        assert result[0].name == "platform"


# ---------------------------------------------------------------------------
# _resolve_provisioner_path
# ---------------------------------------------------------------------------


class TestResolveProvisionerPath:
    def test_returns_none_when_path_does_not_exist(self, tmp_path):
        iac = _make_iac("terraform", "terraform")
        ds = MagicMock()
        ds.get_build_path.return_value = tmp_path / "nonexistent"
        ctrl = CostController()
        result = ctrl._resolve_provisioner_path(iac, ds, tmp_path)
        assert result is None

    def test_resolves_via_source_path(self, tmp_path):
        build_dir = tmp_path / "myapp-1.0.0" / "terraform"
        build_dir.mkdir(parents=True)
        iac = _make_iac("terraform", "terraform")
        ds = MagicMock()
        ds.get_build_path.return_value = tmp_path / "myapp-1.0.0"
        ctrl = CostController()
        result = ctrl._resolve_provisioner_path(iac, ds, tmp_path)
        assert result == build_dir

    def test_uses_solution_controller_when_provided(self, tmp_path):
        build_dir = tmp_path / "resolved"
        build_dir.mkdir(parents=True)
        iac = _make_iac("terraform", "terraform")
        ds = MagicMock()
        sc = MagicMock()
        sc.get_provisioner_path.return_value = build_dir
        ctrl = CostController()
        result = ctrl._resolve_provisioner_path(iac, ds, tmp_path, solution_controller=sc)
        assert result == build_dir
        sc.get_provisioner_path.assert_called_once_with(ds, tmp_path, iac)

    def test_returns_none_when_source_is_none(self, tmp_path):
        iac = MagicMock()
        iac.source = None
        ds = MagicMock()
        ctrl = CostController()
        result = ctrl._resolve_provisioner_path(iac, ds, tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# _get_estimator
# ---------------------------------------------------------------------------


class TestGetEstimator:
    def test_returns_none_when_no_cost_integrations(self):
        ctrl = CostController()
        with patch(
            "strata.integrations.factory.IntegrationFactory.get_known_types",
            return_value=[],
        ):
            assert ctrl._get_estimator() is None

    def test_returns_estimator_when_available(self):
        from strata.integrations.capabilities import ICostEstimator

        mock_estimator = MagicMock(spec=ICostEstimator)
        ctrl = CostController()
        with (
            patch(
                "strata.integrations.factory.IntegrationFactory.get_known_types",
                return_value=["infracost"],
            ),
            patch(
                "strata.integrations.factory.IntegrationFactory.create_by_type",
                return_value=mock_estimator,
            ),
        ):
            result = ctrl._get_estimator()
        assert result is mock_estimator

    def test_skips_non_cost_integrations(self):
        non_cost = MagicMock()
        ctrl = CostController()
        with (
            patch(
                "strata.integrations.factory.IntegrationFactory.get_known_types",
                return_value=["git"],
            ),
            patch(
                "strata.integrations.factory.IntegrationFactory.create_by_type",
                return_value=non_cost,
            ),
        ):
            result = ctrl._get_estimator()
        assert result is None


# ---------------------------------------------------------------------------
# is_auto_diff_enabled — gates deploy run --dry-run's automatic cost diff on
# whether a cost estimator is DECLARED in spec.integrations, unlike
# _get_estimator() which probes for any installed binary regardless of config.
# ---------------------------------------------------------------------------


class TestIsAutoDiffEnabled:
    def test_false_when_no_cost_integration_declared(self):
        ctrl = CostController()
        mock_service = MagicMock()
        mock_service.is_initialized.return_value = True
        mock_service.get_integration_with_capability.return_value = None
        with patch(
            "strata.services.integration_service.IntegrationService.get_instance",
            return_value=mock_service,
        ):
            assert ctrl.is_auto_diff_enabled() is False
        mock_service.get_integration_with_capability.assert_called_once()

    def test_true_when_cost_integration_declared_and_enabled(self):
        from strata.integrations.capabilities import ICostEstimator

        ctrl = CostController()
        mock_service = MagicMock()
        mock_service.is_initialized.return_value = True
        mock_service.get_integration_with_capability.return_value = MagicMock(spec=ICostEstimator)
        with patch(
            "strata.services.integration_service.IntegrationService.get_instance",
            return_value=mock_service,
        ):
            assert ctrl.is_auto_diff_enabled() is True

    def test_false_when_integration_declared_but_disabled(self):
        """A declared-but-disabled (enabled: false) integration never reaches
        the registry, so get_integration_with_capability returns None — same
        outcome as not declaring it at all."""
        ctrl = CostController()
        mock_service = MagicMock()
        mock_service.is_initialized.return_value = True
        mock_service.get_integration_with_capability.return_value = None
        with patch(
            "strata.services.integration_service.IntegrationService.get_instance",
            return_value=mock_service,
        ):
            assert ctrl.is_auto_diff_enabled() is False

    def test_initializes_integrations_if_not_already_done(self):
        ctrl = CostController()
        mock_service = MagicMock()
        mock_service.is_initialized.return_value = False
        mock_service.get_integration_with_capability.return_value = None
        with patch(
            "strata.services.integration_service.IntegrationService.get_instance",
            return_value=mock_service,
        ):
            ctrl.is_auto_diff_enabled()
        mock_service.initialize_integrations.assert_called_once()

    def test_does_not_reinitialize_if_already_initialized(self):
        ctrl = CostController()
        mock_service = MagicMock()
        mock_service.is_initialized.return_value = True
        mock_service.get_integration_with_capability.return_value = None
        with patch(
            "strata.services.integration_service.IntegrationService.get_instance",
            return_value=mock_service,
        ):
            ctrl.is_auto_diff_enabled()
        mock_service.initialize_integrations.assert_not_called()


# ---------------------------------------------------------------------------
# show()
# ---------------------------------------------------------------------------


class TestCostControllerShow:
    def _setup(self, tmp_path, provisioner_name="terraform"):
        """Create controller + mock deployment service with valid terraform artifacts."""
        build_dir = tmp_path / "myapp-1.0.0" / provisioner_name
        build_dir.mkdir(parents=True)
        (build_dir / ".terraform").mkdir()
        iac = _make_iac("terraform", provisioner_name)
        ds = _make_deployment_service(provisioners=[iac])
        ds.get_build_path.side_effect = lambda bp: tmp_path / "myapp-1.0.0"
        return CostController(), ds, tmp_path

    def test_no_estimator_returns_error(self, tmp_path):
        ctrl = CostController()
        ds = _make_deployment_service()
        with patch.object(ctrl, "_get_estimator", return_value=None):
            success, result = ctrl.show(ds, tmp_path)
        assert success is False
        assert "error" in result
        assert ctrl.has_errors()

    def test_estimator_not_installed_returns_error(self, tmp_path):
        ctrl = CostController()
        ds = _make_deployment_service()
        estimator = _make_estimator(available=False, error="infracost not in PATH")
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            success, result = ctrl.show(ds, tmp_path)
        assert success is False
        assert "not in PATH" in result["error"]

    def test_no_terraform_provisioners_returns_error(self, tmp_path):
        ctrl = CostController()
        ds = _make_deployment_service(provisioners=[])
        estimator = _make_estimator(available=True)
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            success, result = ctrl.show(ds, tmp_path)
        assert success is False
        assert "No terraform provisioners" in result["error"]

    def test_terraform_not_initialized_returns_error(self, tmp_path):
        # Create directory without .terraform/
        build_dir = tmp_path / "myapp-1.0.0" / "terraform"
        build_dir.mkdir(parents=True)
        iac = _make_iac("terraform", "terraform")
        ds = _make_deployment_service(provisioners=[iac])
        ds.get_build_path.side_effect = lambda bp: tmp_path / "myapp-1.0.0"
        ctrl = CostController()
        estimator = _make_estimator(available=True)
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            success, result = ctrl.show(ds, tmp_path)
        assert success is False
        assert ctrl.has_errors()

    def test_success_returns_breakdown(self, tmp_path):
        ctrl, ds, build_path = self._setup(tmp_path)
        estimator = _make_estimator(available=True, breakdown_result=_SAMPLE_BREAKDOWN)
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            success, result = ctrl.show(ds, build_path)
        assert success is True
        assert "provisioners" in result
        assert result["provisioners"]["terraform"]["breakdown"]["totalMonthlyCost"] == "1202.40"

    def test_passes_currency_to_estimator(self, tmp_path):
        ctrl, ds, build_path = self._setup(tmp_path)
        estimator = _make_estimator(available=True, breakdown_result=_SAMPLE_BREAKDOWN)
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            ctrl.show(ds, build_path, currency="EUR")
        _, kwargs = estimator.breakdown.call_args
        assert kwargs.get("currency") == "EUR"

    def test_success_adds_message(self, tmp_path):
        ctrl, ds, build_path = self._setup(tmp_path)
        estimator = _make_estimator(available=True, breakdown_result=_SAMPLE_BREAKDOWN)
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            ctrl.show(ds, build_path)
        assert ctrl.has_messages()

    def test_estimator_exception_adds_error(self, tmp_path):
        ctrl, ds, build_path = self._setup(tmp_path)
        estimator = _make_estimator(available=True)
        estimator.breakdown.side_effect = RuntimeError("bad TF")
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            success, result = ctrl.show(ds, build_path)
        assert success is False
        assert ctrl.has_errors()


# ---------------------------------------------------------------------------
# diff()
# ---------------------------------------------------------------------------


class TestCostControllerDiff:
    def _setup(self, tmp_path):
        build_dir = tmp_path / "myapp-1.0.0" / "terraform"
        build_dir.mkdir(parents=True)
        iac = _make_iac("terraform", "terraform")
        ds = _make_deployment_service(provisioners=[iac])
        ds.get_build_path.side_effect = lambda bp: tmp_path / "myapp-1.0.0"
        return CostController(), ds, tmp_path

    def test_no_estimator_returns_error(self, tmp_path):
        ctrl = CostController()
        ds = _make_deployment_service()
        with patch.object(ctrl, "_get_estimator", return_value=None):
            success, result = ctrl.diff(ds, tmp_path, "/plan.json")
        assert success is False
        assert "error" in result

    def test_plan_file_not_found_returns_error(self, tmp_path):
        ctrl = CostController()
        ds = _make_deployment_service(provisioners=[_make_iac()])
        estimator = _make_estimator(available=True)
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            success, result = ctrl.diff(ds, tmp_path, "/nonexistent/plan.json")
        assert success is False
        assert "not found" in result["error"].lower()

    def test_success_returns_diff(self, tmp_path):
        plan_file = tmp_path / "plan.json"
        plan_file.write_text("{}")
        ctrl, ds, build_path = self._setup(tmp_path)
        estimator = _make_estimator(available=True, diff_result=_SAMPLE_DIFF)
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            success, result = ctrl.diff(ds, build_path, str(plan_file))
        assert success is True
        assert result["diff"]["totalMonthlyCost"] == "200.00"

    def test_passes_currency(self, tmp_path):
        plan_file = tmp_path / "plan.json"
        plan_file.write_text("{}")
        ctrl, ds, build_path = self._setup(tmp_path)
        estimator = _make_estimator(available=True, diff_result=_SAMPLE_DIFF)
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            ctrl.diff(ds, build_path, str(plan_file), currency="GBP")
        _, kwargs = estimator.diff.call_args
        assert kwargs.get("currency") == "GBP"

    def test_estimator_exception_returns_error(self, tmp_path):
        plan_file = tmp_path / "plan.json"
        plan_file.write_text("{}")
        ctrl, ds, build_path = self._setup(tmp_path)
        estimator = _make_estimator(available=True)
        estimator.diff.side_effect = RuntimeError("plan not found")
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            success, result = ctrl.diff(ds, build_path, str(plan_file))
        assert success is False
        assert "plan not found" in result["error"]


# ---------------------------------------------------------------------------
# is_available()
# ---------------------------------------------------------------------------


class TestCostControllerIsAvailable:
    def test_returns_false_when_no_estimator(self):
        ctrl = CostController()
        with patch.object(ctrl, "_get_estimator", return_value=None):
            assert ctrl.is_available() is False

    def test_returns_false_when_estimator_not_installed(self):
        ctrl = CostController()
        estimator = _make_estimator(available=False, error="not found")
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            assert ctrl.is_available() is False

    def test_returns_true_when_estimator_installed(self):
        ctrl = CostController()
        estimator = _make_estimator(available=True)
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            assert ctrl.is_available() is True


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


class TestCostControllerCache:
    def test_get_cache_dir_returns_none_without_work_path(self):
        ctrl = CostController()
        assert ctrl._get_cache_dir() is None

    def test_get_cache_dir_returns_path_with_work_path(self, tmp_path):
        ctrl = CostController(work_path=tmp_path)
        cache_dir = ctrl._get_cache_dir()
        assert cache_dir is not None
        assert str(cache_dir).startswith(str(tmp_path))
        assert "cache" in str(cache_dir)
        assert "cost" in str(cache_dir)

    def test_compute_cache_key_is_deterministic(self, tmp_path):
        tf_path = tmp_path / "terraform"
        tf_path.mkdir()
        (tf_path / "main.tf").write_text("resource {}")
        ctrl = CostController(work_path=tmp_path)
        key1 = ctrl._compute_cache_key(tf_path, "EUR")
        key2 = ctrl._compute_cache_key(tf_path, "EUR")
        assert key1 == key2

    def test_compute_cache_key_differs_by_currency(self, tmp_path):
        tf_path = tmp_path / "terraform"
        tf_path.mkdir()
        (tf_path / "main.tf").write_text("resource {}")
        ctrl = CostController(work_path=tmp_path)
        key_eur = ctrl._compute_cache_key(tf_path, "EUR")
        key_usd = ctrl._compute_cache_key(tf_path, "USD")
        assert key_eur != key_usd

    def test_compute_cache_key_changes_on_same_size_edit(self, tmp_path):
        """Regression: name+size alone missed a same-byte-count content edit —
        mtime is now part of the hash so this no longer silently reuses a stale
        cache entry."""
        import os
        import time

        tf_path = tmp_path / "terraform"
        tf_path.mkdir()
        f = tf_path / "main.tf"
        f.write_text("resource {a}")  # same length as the edit below
        ctrl = CostController(work_path=tmp_path)
        key_before = ctrl._compute_cache_key(tf_path, "EUR")

        # Rewrite with different content but identical byte length, and force
        # a distinct mtime (some filesystems have coarse mtime resolution).
        f.write_text("resource {b}")
        future = time.time() + 5
        os.utime(f, (future, future))

        key_after = ctrl._compute_cache_key(tf_path, "EUR")
        assert key_before != key_after

    def test_read_cache_returns_none_when_no_work_path(self):
        ctrl = CostController()
        assert ctrl._read_cache("somekey") is None

    def test_write_and_read_cache_roundtrip(self, tmp_path):
        ctrl = CostController(work_path=tmp_path)
        data = {"breakdown": {"totalMonthlyCost": "500.00"}}
        ctrl._write_cache("testkey", data)
        result = ctrl._read_cache("testkey")
        assert result == data

    def test_read_cache_returns_none_for_missing_key(self, tmp_path):
        ctrl = CostController(work_path=tmp_path)
        assert ctrl._read_cache("nonexistent") is None

    def test_read_cache_returns_none_for_expired_entry(self, tmp_path):
        import time

        ctrl = CostController(work_path=tmp_path)
        data = {"totalMonthlyCost": "100.00"}
        ctrl._write_cache("expiredkey", data)
        cache_dir = ctrl._get_cache_dir()
        cache_file = cache_dir / "expiredkey.json"
        # Set mtime to 8 days ago
        old_time = time.time() - (8 * 24 * 3600)
        import os

        os.utime(cache_file, (old_time, old_time))
        assert ctrl._read_cache("expiredkey") is None
        assert not cache_file.exists()

    def test_invalidate_cache_removes_files(self, tmp_path):
        ctrl = CostController(work_path=tmp_path)
        ctrl._write_cache("key1", {"a": 1})
        ctrl._write_cache("key2", {"b": 2})
        removed = ctrl.invalidate_cache()
        assert removed == 2
        # Cache dir exists but is empty
        cache_dir = ctrl._get_cache_dir()
        assert list(cache_dir.glob("*.json")) == []

    def test_invalidate_cache_returns_zero_when_empty(self, tmp_path):
        ctrl = CostController(work_path=tmp_path)
        assert ctrl.invalidate_cache() == 0

    def test_show_uses_cache_on_second_call(self, tmp_path):
        """Second call with same terraform artifacts returns cached result."""
        build_dir = tmp_path / "myapp-1.0.0" / "terraform"
        build_dir.mkdir(parents=True)
        (build_dir / ".terraform").mkdir()
        (build_dir / "main.tf").write_text("resource {}")
        iac = _make_iac("terraform", "terraform")
        ds = _make_deployment_service(provisioners=[iac])
        ds.get_build_path.side_effect = lambda bp: tmp_path / "myapp-1.0.0"

        ctrl = CostController(work_path=tmp_path)
        estimator = _make_estimator(available=True, breakdown_result=_SAMPLE_BREAKDOWN)

        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            ctrl.show(ds, tmp_path)  # first call — runs infracost
            ctrl.show(ds, tmp_path)  # second call — should use cache

        # Infracost should only have been called once
        assert estimator.breakdown.call_count == 1

    def test_force_refresh_bypasses_cache(self, tmp_path):
        """force_refresh=True always calls infracost even if cache is fresh."""
        build_dir = tmp_path / "myapp-1.0.0" / "terraform"
        build_dir.mkdir(parents=True)
        (build_dir / ".terraform").mkdir()
        (build_dir / "main.tf").write_text("resource {}")
        iac = _make_iac("terraform", "terraform")
        ds = _make_deployment_service(provisioners=[iac])
        ds.get_build_path.side_effect = lambda bp: tmp_path / "myapp-1.0.0"

        ctrl = CostController(work_path=tmp_path)
        estimator = _make_estimator(available=True, breakdown_result=_SAMPLE_BREAKDOWN)

        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            ctrl.show(ds, tmp_path)  # populates cache
            ctrl.show(ds, tmp_path, force_refresh=True)  # should bypass cache

        assert estimator.breakdown.call_count == 2


# ---------------------------------------------------------------------------
# cost.json output
# ---------------------------------------------------------------------------


class TestCostJson:
    def _setup(self, tmp_path):
        build_dir = tmp_path / "myapp-1.0.0" / "terraform"
        build_dir.mkdir(parents=True)
        (build_dir / ".terraform").mkdir()
        iac = _make_iac("terraform", "terraform")
        ds = _make_deployment_service(provisioners=[iac])
        ds.get_build_path.side_effect = lambda bp: tmp_path / "myapp-1.0.0"
        return CostController(work_path=tmp_path), ds, tmp_path

    def test_cost_json_written_on_success(self, tmp_path):
        ctrl, ds, build_path = self._setup(tmp_path)
        estimator = _make_estimator(available=True, breakdown_result=_SAMPLE_BREAKDOWN)
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            success, _ = ctrl.show(ds, build_path)
        assert success is True
        cost_file = tmp_path / "myapp-1.0.0" / "cost.json"
        assert cost_file.exists()

    def test_cost_json_contains_provisioners_key(self, tmp_path):
        import json

        ctrl, ds, build_path = self._setup(tmp_path)
        estimator = _make_estimator(available=True, breakdown_result=_SAMPLE_BREAKDOWN)
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            ctrl.show(ds, build_path)
        cost_file = tmp_path / "myapp-1.0.0" / "cost.json"
        data = json.loads(cost_file.read_text())
        assert "provisioners" in data
        assert "terraform" in data["provisioners"]

    def test_cost_json_not_written_on_failure(self, tmp_path):
        ctrl = CostController(work_path=tmp_path)
        ds = _make_deployment_service(provisioners=[])  # no provisioners → fails
        estimator = _make_estimator(available=True)
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            success, _ = ctrl.show(ds, tmp_path)
        assert success is False
        # cost.json should not exist
        cost_file = tmp_path / "myapp-1.0.0" / "cost.json"
        assert not cost_file.exists()

    def test_write_cost_json_is_nonfatal_on_os_error(self, tmp_path):
        ctrl, ds, build_path = self._setup(tmp_path)
        estimator = _make_estimator(available=True, breakdown_result=_SAMPLE_BREAKDOWN)
        # Simulate an OS error when writing
        with (
            patch.object(ctrl, "_get_estimator", return_value=estimator),
            patch("pathlib.Path.write_text", side_effect=OSError("disk full")),
        ):
            success, result = ctrl.show(ds, build_path)
        # Cost result is still returned — write failure is non-fatal
        assert success is True
        assert "provisioners" in result
