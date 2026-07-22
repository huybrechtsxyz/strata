#!/usr/bin/env python3
"""Unit tests for CostController."""

from pathlib import Path
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
# _resolve_terraform_path
# ---------------------------------------------------------------------------


class TestResolveTerraformPath:
    def test_returns_none_when_build_dir_missing(self, tmp_path):
        ctrl = CostController(work_path=tmp_path)
        result = ctrl._resolve_terraform_path("myapp")
        assert result is None

    def test_returns_none_when_no_matching_deployment(self, tmp_path):
        build = tmp_path / "build"
        build.mkdir()
        (build / "other-deployment").mkdir()
        ctrl = CostController(work_path=tmp_path)
        assert ctrl._resolve_terraform_path("myapp") is None

    def test_finds_terraform_path(self, tmp_path):
        tf_path = tmp_path / "build" / "myapp-1.0.0" / "terraform" / "terraform"
        tf_path.mkdir(parents=True)
        ctrl = CostController(work_path=tmp_path)
        result = ctrl._resolve_terraform_path("myapp")
        assert result == tf_path

    def test_finds_with_custom_provisioner_name(self, tmp_path):
        tf_path = tmp_path / "build" / "myapp" / "terraform" / "infra"
        tf_path.mkdir(parents=True)
        ctrl = CostController(work_path=tmp_path)
        result = ctrl._resolve_terraform_path("myapp", provisioner_name="infra")
        assert result == tf_path

    def test_returns_most_recent_when_multiple_builds(self, tmp_path):
        import time

        build = tmp_path / "build"
        older = build / "myapp-1.0.0" / "terraform" / "terraform"
        newer = build / "myapp-2.0.0" / "terraform" / "terraform"
        older.mkdir(parents=True)
        time.sleep(0.01)
        newer.mkdir(parents=True)
        ctrl = CostController(work_path=tmp_path)
        result = ctrl._resolve_terraform_path("myapp")
        assert result == newer


# ---------------------------------------------------------------------------
# _get_estimator
# ---------------------------------------------------------------------------


class TestGetEstimator:
    def test_returns_none_when_no_cost_integrations(self):
        ctrl = CostController(work_path=Path("/tmp"))
        with patch(
            "strata.integrations.factory.IntegrationFactory.get_known_types",
            return_value=[],
        ):
            assert ctrl._get_estimator() is None

    def test_returns_estimator_when_available(self):
        from strata.integrations.capabilities import ICostEstimator

        mock_estimator = MagicMock(spec=ICostEstimator)
        ctrl = CostController(work_path=Path("/tmp"))
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
        ctrl = CostController(work_path=Path("/tmp"))
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
    def _make_ctrl_with_tf(self, tmp_path, deployment_name="myapp", provisioner="terraform"):
        """Create a controller with valid terraform artifacts (including .terraform/)."""
        tf_path = tmp_path / "build" / deployment_name / "terraform" / provisioner
        tf_path.mkdir(parents=True)
        (tf_path / ".terraform").mkdir()
        return CostController(work_path=tmp_path)

    def test_no_estimator_returns_error(self, tmp_path):
        ctrl = CostController(work_path=tmp_path)
        with patch.object(ctrl, "_get_estimator", return_value=None):
            success, result = ctrl.show("myapp")
        assert success is False
        assert "error" in result
        assert ctrl.has_errors()

    def test_estimator_not_installed_returns_error(self, tmp_path):
        ctrl = CostController(work_path=tmp_path)
        estimator = _make_estimator(available=False, error="infracost not in PATH")
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            success, result = ctrl.show("myapp")
        assert success is False
        assert "not in PATH" in result["error"]

    def test_no_build_artifacts_returns_error(self, tmp_path):
        ctrl = CostController(work_path=tmp_path)
        estimator = _make_estimator(available=True)
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            success, result = ctrl.show("myapp")
        assert success is False
        assert "build artifacts" in result["error"].lower() or "strata build" in result["error"]

    def test_terraform_not_initialized_returns_error(self, tmp_path):
        tf_path = tmp_path / "build" / "myapp" / "terraform" / "terraform"
        tf_path.mkdir(parents=True)
        # Note: no .terraform/ directory
        ctrl = CostController(work_path=tmp_path)
        estimator = _make_estimator(available=True)
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            success, result = ctrl.show("myapp")
        assert success is False
        assert "not initialized" in result["error"].lower() or "terraform init" in result["error"]

    def test_success_returns_breakdown(self, tmp_path):
        ctrl = self._make_ctrl_with_tf(tmp_path)
        estimator = _make_estimator(available=True, breakdown_result=_SAMPLE_BREAKDOWN)
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            success, result = ctrl.show("myapp")
        assert success is True
        assert result["breakdown"]["totalMonthlyCost"] == "1202.40"

    def test_success_passes_currency_to_estimator(self, tmp_path):
        ctrl = self._make_ctrl_with_tf(tmp_path)
        estimator = _make_estimator(available=True, breakdown_result=_SAMPLE_BREAKDOWN)
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            ctrl.show("myapp", currency="EUR")
        estimator.breakdown.assert_called_once()
        _, kwargs = estimator.breakdown.call_args
        assert kwargs.get("currency") == "EUR"

    def test_success_adds_message(self, tmp_path):
        ctrl = self._make_ctrl_with_tf(tmp_path)
        estimator = _make_estimator(available=True, breakdown_result=_SAMPLE_BREAKDOWN)
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            ctrl.show("myapp")
        assert ctrl.has_messages()

    def test_estimator_exception_returns_error(self, tmp_path):
        ctrl = self._make_ctrl_with_tf(tmp_path)
        estimator = _make_estimator(available=True)
        estimator.breakdown.side_effect = RuntimeError("infracost breakdown failed: bad TF")
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            success, result = ctrl.show("myapp")
        assert success is False
        assert "bad TF" in result["error"]

    def test_custom_provisioner_name(self, tmp_path):
        ctrl = self._make_ctrl_with_tf(tmp_path, provisioner="infra")
        estimator = _make_estimator(available=True, breakdown_result=_SAMPLE_BREAKDOWN)
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            success, _ = ctrl.show("myapp", provisioner_name="infra")
        assert success is True


# ---------------------------------------------------------------------------
# diff()
# ---------------------------------------------------------------------------


class TestCostControllerDiff:
    def _make_ctrl_with_tf(self, tmp_path, deployment_name="myapp"):
        tf_path = tmp_path / "build" / deployment_name / "terraform" / "terraform"
        tf_path.mkdir(parents=True)
        return CostController(work_path=tmp_path)

    def test_no_estimator_returns_error(self, tmp_path):
        ctrl = CostController(work_path=tmp_path)
        with patch.object(ctrl, "_get_estimator", return_value=None):
            success, result = ctrl.diff("myapp", "/plan.json")
        assert success is False
        assert "error" in result

    def test_plan_file_not_found_returns_error(self, tmp_path):
        ctrl = self._make_ctrl_with_tf(tmp_path)
        estimator = _make_estimator(available=True)
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            success, result = ctrl.diff("myapp", "/nonexistent/plan.json")
        assert success is False
        assert "not found" in result["error"].lower()

    def test_success_returns_diff(self, tmp_path):
        plan_file = tmp_path / "plan.json"
        plan_file.write_text("{}")
        ctrl = self._make_ctrl_with_tf(tmp_path)
        estimator = _make_estimator(available=True, diff_result=_SAMPLE_DIFF)
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            success, result = ctrl.diff("myapp", str(plan_file))
        assert success is True
        assert result["diff"]["totalMonthlyCost"] == "200.00"

    def test_success_passes_currency(self, tmp_path):
        plan_file = tmp_path / "plan.json"
        plan_file.write_text("{}")
        ctrl = self._make_ctrl_with_tf(tmp_path)
        estimator = _make_estimator(available=True, diff_result=_SAMPLE_DIFF)
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            ctrl.diff("myapp", str(plan_file), currency="GBP")
        estimator.diff.assert_called_once()
        _, kwargs = estimator.diff.call_args
        assert kwargs.get("currency") == "GBP"

    def test_estimator_exception_returns_error(self, tmp_path):
        plan_file = tmp_path / "plan.json"
        plan_file.write_text("{}")
        ctrl = self._make_ctrl_with_tf(tmp_path)
        estimator = _make_estimator(available=True)
        estimator.diff.side_effect = RuntimeError("plan not found")
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            success, result = ctrl.diff("myapp", str(plan_file))
        assert success is False
        assert "plan not found" in result["error"]


# ---------------------------------------------------------------------------
# is_available()
# ---------------------------------------------------------------------------


class TestCostControllerIsAvailable:
    def test_returns_false_when_no_estimator(self, tmp_path):
        ctrl = CostController(work_path=tmp_path)
        with patch.object(ctrl, "_get_estimator", return_value=None):
            assert ctrl.is_available() is False

    def test_returns_false_when_estimator_not_installed(self, tmp_path):
        ctrl = CostController(work_path=tmp_path)
        estimator = _make_estimator(available=False, error="not found")
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            assert ctrl.is_available() is False

    def test_returns_true_when_estimator_installed(self, tmp_path):
        ctrl = CostController(work_path=tmp_path)
        estimator = _make_estimator(available=True)
        with patch.object(ctrl, "_get_estimator", return_value=estimator):
            assert ctrl.is_available() is True
