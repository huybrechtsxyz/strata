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
