#!/usr/bin/env python3
"""Unit tests for InfracostIntegration."""

import json
from unittest.mock import MagicMock, patch

import pytest

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.capabilities import ICostEstimator
from strata.integrations.infracost import InfracostIntegration
from strata.models.integration_model import IntegrationModel


def _cfg(name: str = "infracost") -> IntegrationModel:
    return IntegrationModel(name=name, type="infracost")


def _make_run_result(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    result = MagicMock()
    result.stdout = stdout
    result.stderr = stderr
    result.returncode = returncode
    result.success = returncode == 0
    return result


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


class TestInfracostIntegrationMetadata:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_command_is_infracost(self):
        assert InfracostIntegration.COMMAND == "infracost"

    def test_capabilities_include_cost_estimator(self):
        assert ICostEstimator in InfracostIntegration.CAPABILITIES

    def test_is_subclass_of_icost_estimator(self):
        assert issubclass(InfracostIntegration, ICostEstimator)

    def test_version_command(self):
        i = InfracostIntegration(_cfg())
        assert i.get_version_command() == ["infracost", "--version"]

    def test_setup_info_has_install_url(self):
        i = InfracostIntegration(_cfg())
        info = i.get_setup_info()
        assert "infracost.io" in info["install_url"]
        assert info["command"] == "infracost"


# ---------------------------------------------------------------------------
# Version parsing
# ---------------------------------------------------------------------------


class TestInfracostParseVersion:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_parse_standard_output(self):
        i = InfracostIntegration(_cfg())
        assert i.parse_version("Infracost v0.10.40") == "0.10.40"

    def test_parse_without_prefix(self):
        i = InfracostIntegration(_cfg())
        assert i.parse_version("0.10.40") == "0.10.40"

    def test_parse_with_extra_lines(self):
        i = InfracostIntegration(_cfg())
        output = "Infracost v0.10.40\nYour project has 3 resources"
        assert i.parse_version(output) == "0.10.40"

    def test_parse_fallback_returns_stripped(self):
        i = InfracostIntegration(_cfg())
        assert i.parse_version("  no-version  ") == "no-version"


# ---------------------------------------------------------------------------
# ensure_available
# ---------------------------------------------------------------------------


class TestInfracostEnsureAvailable:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_ensure_available_success(self):
        i = InfracostIntegration(_cfg())
        with (
            patch.object(i, "is_available", return_value=True),
            patch.object(i, "validate_version", return_value=(True, "")),
            patch.object(i, "get_version", return_value="0.10.40"),
        ):
            ok, msg = i.ensure_available()
        assert ok is True
        assert msg == ""

    def test_ensure_available_not_installed(self):
        i = InfracostIntegration(_cfg())
        with patch.object(i, "is_available", return_value=False):
            ok, msg = i.ensure_available()
        assert ok is False
        assert "not installed" in msg or "not in PATH" in msg
        assert "infracost.io" in msg

    def test_ensure_available_version_invalid(self):
        i = InfracostIntegration(_cfg())
        with (
            patch.object(i, "is_available", return_value=True),
            patch.object(i, "validate_version", return_value=(False, "version too old")),
        ):
            ok, msg = i.ensure_available()
        assert ok is False
        assert "version too old" in msg


# ---------------------------------------------------------------------------
# breakdown()
# ---------------------------------------------------------------------------


SAMPLE_BREAKDOWN = {
    "version": "0.1",
    "breakdown": {
        "resources": [
            {
                "name": "azurerm_mssql_database.main",
                "monthlyCost": "1202.40",
                "hourlyCost": "1.647",
            }
        ],
        "totalMonthlyCost": "1202.40",
        "totalHourlyCost": "1.647",
    },
}


class TestInfracostBreakdown:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_breakdown_returns_parsed_json(self):
        i = InfracostIntegration(_cfg())
        mock_result = _make_run_result(stdout=json.dumps(SAMPLE_BREAKDOWN))
        with patch("strata.integrations.infracost.run_command", return_value=mock_result):
            result = i.breakdown("/some/terraform/path")
        assert result["breakdown"]["totalMonthlyCost"] == "1202.40"

    def test_breakdown_includes_path_in_command(self):
        i = InfracostIntegration(_cfg())
        mock_result = _make_run_result(stdout=json.dumps(SAMPLE_BREAKDOWN))
        with patch("strata.integrations.infracost.run_command", return_value=mock_result) as mock_run:
            i.breakdown("/my/terraform")
        cmd = mock_run.call_args[0][0]
        assert "--path" in cmd
        assert "/my/terraform" in cmd

    def test_breakdown_includes_json_format(self):
        i = InfracostIntegration(_cfg())
        mock_result = _make_run_result(stdout=json.dumps(SAMPLE_BREAKDOWN))
        with patch("strata.integrations.infracost.run_command", return_value=mock_result) as mock_run:
            i.breakdown("/my/terraform")
        cmd = mock_run.call_args[0][0]
        assert "--format" in cmd
        assert "json" in cmd

    def test_breakdown_passes_currency(self):
        i = InfracostIntegration(_cfg())
        mock_result = _make_run_result(stdout=json.dumps(SAMPLE_BREAKDOWN))
        with patch("strata.integrations.infracost.run_command", return_value=mock_result) as mock_run:
            i.breakdown("/my/terraform", currency="EUR")
        cmd = mock_run.call_args[0][0]
        assert "--currency" in cmd
        assert "EUR" in cmd

    def test_breakdown_no_currency_by_default(self):
        i = InfracostIntegration(_cfg())
        mock_result = _make_run_result(stdout=json.dumps(SAMPLE_BREAKDOWN))
        with patch("strata.integrations.infracost.run_command", return_value=mock_result) as mock_run:
            i.breakdown("/my/terraform")
        cmd = mock_run.call_args[0][0]
        assert "--currency" not in cmd

    def test_breakdown_raises_on_failure(self):
        i = InfracostIntegration(_cfg())
        mock_result = _make_run_result(stderr="unsupported provider", returncode=1)
        with patch("strata.integrations.infracost.run_command", return_value=mock_result):
            with pytest.raises(RuntimeError, match="infracost breakdown failed"):
                i.breakdown("/my/terraform")

    def test_breakdown_raises_on_invalid_json(self):
        i = InfracostIntegration(_cfg())
        mock_result = _make_run_result(stdout="not json")
        with patch("strata.integrations.infracost.run_command", return_value=mock_result):
            with pytest.raises(RuntimeError, match="Failed to parse"):
                i.breakdown("/my/terraform")


# ---------------------------------------------------------------------------
# diff()
# ---------------------------------------------------------------------------


SAMPLE_DIFF = {
    "version": "0.1",
    "diff": {
        "resources": [],
        "totalMonthlyCost": "200.00",
        "totalHourlyCost": "0.274",
        "pastTotalMonthlyCost": "0.00",
    },
}


class TestInfracostDiff:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_diff_returns_parsed_json(self):
        i = InfracostIntegration(_cfg())
        mock_result = _make_run_result(stdout=json.dumps(SAMPLE_DIFF))
        with patch("strata.integrations.infracost.run_command", return_value=mock_result):
            result = i.diff("/my/terraform", "/my/plan.json")
        assert result["diff"]["totalMonthlyCost"] == "200.00"

    def test_diff_includes_path_and_plan_in_command(self):
        i = InfracostIntegration(_cfg())
        mock_result = _make_run_result(stdout=json.dumps(SAMPLE_DIFF))
        with patch("strata.integrations.infracost.run_command", return_value=mock_result) as mock_run:
            i.diff("/my/terraform", "/my/plan.json")
        cmd = mock_run.call_args[0][0]
        assert "--path" in cmd
        assert "/my/terraform" in cmd
        assert "--terraform-plan-json" in cmd
        assert "/my/plan.json" in cmd

    def test_diff_passes_currency(self):
        i = InfracostIntegration(_cfg())
        mock_result = _make_run_result(stdout=json.dumps(SAMPLE_DIFF))
        with patch("strata.integrations.infracost.run_command", return_value=mock_result) as mock_run:
            i.diff("/my/terraform", "/my/plan.json", currency="GBP")
        cmd = mock_run.call_args[0][0]
        assert "--currency" in cmd
        assert "GBP" in cmd

    def test_diff_raises_on_failure(self):
        i = InfracostIntegration(_cfg())
        mock_result = _make_run_result(stderr="plan not found", returncode=1)
        with patch("strata.integrations.infracost.run_command", return_value=mock_result):
            with pytest.raises(RuntimeError, match="infracost diff failed"):
                i.diff("/my/terraform", "/missing/plan.json")

    def test_diff_raises_on_invalid_json(self):
        i = InfracostIntegration(_cfg())
        mock_result = _make_run_result(stdout="not json")
        with patch("strata.integrations.infracost.run_command", return_value=mock_result):
            with pytest.raises(RuntimeError, match="Failed to parse"):
                i.diff("/my/terraform", "/my/plan.json")


# ---------------------------------------------------------------------------
# Factory registration
# ---------------------------------------------------------------------------


class TestInfracostFactoryRegistration:
    def test_infracost_in_builtin_class_map(self):
        from strata.integrations.factory import IntegrationFactory

        assert "infracost" in IntegrationFactory._BUILTIN_CLASS_MAP

    def test_factory_resolves_infracost_class(self):
        from strata.integrations.factory import IntegrationFactory

        module_path, class_name = IntegrationFactory._BUILTIN_CLASS_MAP["infracost"]
        assert module_path == "strata.integrations.infracost"
        assert class_name == "InfracostIntegration"

    def test_cost_capability_valid_in_integration_model(self):
        # Validates that 'cost' is accepted by IntegrationModel.capabilities validator
        model = IntegrationModel(name="infracost", type="infracost", capabilities={"cost"})
        assert "cost" in model.capabilities

    def test_unknown_capability_rejected(self):
        with pytest.raises(ValueError):
            IntegrationModel(name="infracost", type="infracost", capabilities={"unknown_cap"})


# ---------------------------------------------------------------------------
# ICostEstimator capability
# ---------------------------------------------------------------------------


class TestICostEstimatorCapability:
    def test_cost_in_capability_map(self):
        from strata.integrations.capabilities import CAPABILITY_MAP

        assert "cost" in CAPABILITY_MAP

    def test_cost_in_valid_capability_names(self):
        from strata.integrations.capabilities import VALID_CAPABILITY_NAMES

        assert "cost" in VALID_CAPABILITY_NAMES

    def test_cost_maps_to_icost_estimator(self):
        from strata.integrations.capabilities import CAPABILITY_MAP, ICostEstimator

        assert CAPABILITY_MAP["cost"] is ICostEstimator

    def test_icost_estimator_in_registry(self):
        from strata.integrations.capabilities import CAPABILITY_REGISTRY

        assert "ICostEstimator" in CAPABILITY_REGISTRY
        assert CAPABILITY_REGISTRY["ICostEstimator"]["examples"] == ["Infracost"]
