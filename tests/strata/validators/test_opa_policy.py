"""Tests for OPAIntegration and OPAPolicy.

No real OPA binary required — all subprocess / HTTP calls are mocked.
"""

import json
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from strata.integrations.opa import OPAIntegration, OPAResult
from strata.models.integration_model import IntegrationModel
from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import PolicyContext
from strata.validators.policies.opa_policy import OPAPolicy

# ===========================================================================
# Helpers
# ===========================================================================


def _make_policy(rule="data.strata.test.deny", policy_dir=None, endpoint=None, enforcement="deny"):
    cfg = {"rule": rule}
    if policy_dir:
        cfg["policy_dir"] = policy_dir
    if endpoint:
        cfg["endpoint"] = endpoint
    return PolicyModel(name="test_opa", type="opa", phase="build", enforcement=enforcement, configuration=cfg)


def _make_context(work_path: Optional[Path] = None) -> "PolicyContext":
    return PolicyContext(phase="build", work_path=work_path)


def _make_integration() -> "OPAIntegration":
    config = IntegrationModel(name="opa", type="opa")
    return OPAIntegration(config)


# ===========================================================================
# OPAIntegration._extract_violations
# ===========================================================================


class TestExtractViolations:
    def test_false_returns_empty(self):
        assert _make_integration()._extract_violations(False) == []

    def test_none_returns_empty(self):
        assert _make_integration()._extract_violations(None) == []

    def test_true_returns_generic_message(self):
        result = _make_integration()._extract_violations(True)
        assert len(result) == 1
        assert "violation" in result[0].lower()

    def test_list_of_strings(self):
        result = _make_integration()._extract_violations(["msg1", "msg2"])
        assert result == ["msg1", "msg2"]

    def test_list_of_dicts_with_msg(self):
        result = _make_integration()._extract_violations([{"msg": "violation A"}])
        assert result == ["violation A"]

    def test_empty_list_returns_empty(self):
        assert _make_integration()._extract_violations([]) == []

    def test_truthy_non_list_returns_violation(self):
        result = _make_integration()._extract_violations("unexpected string")
        assert len(result) == 1


# ===========================================================================
# OPAIntegration._parse_cli_result
# ===========================================================================


class TestParseCliResult:
    def _opa_output(self, value):
        return json.dumps({"result": [{"expressions": [{"value": value, "text": "..."}]}]})

    def test_empty_violations_passes(self):
        output = self._opa_output([])
        result = _make_integration()._parse_cli_result(output)
        assert result.passed
        assert result.violations == []

    def test_string_violations_fail(self):
        output = self._opa_output(["zone not allowed", "tag missing"])
        result = _make_integration()._parse_cli_result(output)
        assert not result.passed
        assert len(result.violations) == 2

    def test_false_result_passes(self):
        output = self._opa_output(False)
        result = _make_integration()._parse_cli_result(output)
        assert result.passed

    def test_empty_output_passes(self):
        result = _make_integration()._parse_cli_result("")
        assert result.passed

    def test_invalid_json_passes(self):
        result = _make_integration()._parse_cli_result("not json")
        assert result.passed

    def test_missing_result_key_passes(self):
        output = json.dumps({"other": "data"})
        result = _make_integration()._parse_cli_result(output)
        assert result.passed


# ===========================================================================
# OPAIntegration._parse_http_result
# ===========================================================================


class TestParseHttpResult:
    def test_violations_returned(self):
        raw = {"result": ["zone not allowed"]}
        result = _make_integration()._parse_http_result(raw)
        assert not result.passed
        assert result.violations == ["zone not allowed"]

    def test_empty_result_passes(self):
        raw = {"result": []}
        result = _make_integration()._parse_http_result(raw)
        assert result.passed

    def test_none_result_passes(self):
        raw = {"result": None}
        result = _make_integration()._parse_http_result(raw)
        assert result.passed

    def test_missing_result_key_passes(self):
        raw = {}
        result = _make_integration()._parse_http_result(raw)
        assert result.passed


# ===========================================================================
# OPAIntegration.evaluate — mode selection
# ===========================================================================


class TestOPAIntegrationEvaluate:
    def _make(self):
        return _make_integration()

    def test_http_tried_when_endpoint_set(self):
        opa = self._make()
        expected = OPAResult(passed=True, violations=[])
        with patch.object(opa, "evaluate_http", return_value=expected) as mock_http:
            result = opa.evaluate(
                "data.test.deny",
                {},
                endpoint="http://localhost:8181",
            )
        mock_http.assert_called_once()
        assert result.passed

    def test_cli_used_when_no_endpoint(self):
        opa = self._make()
        expected = OPAResult(passed=True, violations=[])
        with (
            patch.object(opa, "ensure_available", return_value=(True, "")),
            patch.object(opa, "evaluate_cli", return_value=expected) as mock_cli,
        ):
            result = opa.evaluate("data.test.deny", {}, endpoint=None)
        mock_cli.assert_called_once()
        assert result.passed

    def test_cli_fallback_when_http_fails(self):
        import urllib.error

        opa = self._make()
        fallback = OPAResult(passed=True, violations=[])
        with (
            patch.object(opa, "evaluate_http", side_effect=urllib.error.URLError("refused")),
            patch.object(opa, "ensure_available", return_value=(True, "")),
            patch.object(opa, "evaluate_cli", return_value=fallback) as mock_cli,
        ):
            result = opa.evaluate(
                "data.test.deny",
                {},
                endpoint="http://localhost:8181",
            )
        mock_cli.assert_called_once()
        assert result.passed

    def test_raises_when_cli_unavailable_and_no_endpoint(self):
        opa = self._make()
        with patch.object(opa, "ensure_available", return_value=(False, "opa not found")):
            with pytest.raises(RuntimeError, match="not found"):
                opa.evaluate("data.test.deny", {}, endpoint=None)


# ===========================================================================
# OPAPolicy.evaluate
# ===========================================================================


class TestOPAPolicyEvaluate:
    def _mock_run_opa(self, result):
        return patch.object(OPAPolicy, "_run_opa", return_value=result)

    def test_skip_no_rule(self):
        policy = OPAPolicy(PolicyModel(name="t", type="opa", phase="build", enforcement="deny", configuration={}))
        result = policy.evaluate(_make_context())
        assert result.passed
        assert "skipped" in (result.details or {})

    def test_pass_no_violations(self):
        opa_result = OPAResult(passed=True, violations=[])
        with self._mock_run_opa(opa_result):
            policy = OPAPolicy(_make_policy())
            result = policy.evaluate(_make_context())
        assert result.passed
        assert result.violations == []

    def test_fail_with_violations(self):
        opa_result = OPAResult(passed=False, violations=["zone not allowed"])
        with self._mock_run_opa(opa_result):
            policy = OPAPolicy(_make_policy())
            result = policy.evaluate(_make_context())
        assert not result.passed
        assert len(result.violations) == 1
        assert "zone not allowed" in result.violations[0]

    def test_skip_when_opa_unavailable(self):
        with self._mock_run_opa(None):
            policy = OPAPolicy(_make_policy())
            result = policy.evaluate(_make_context())
        assert result.passed
        assert "skipped" in (result.details or {})

    def test_skip_when_policy_dir_missing(self, tmp_path):
        policy = OPAPolicy(_make_policy(policy_dir="nonexistent/"))
        result = policy.evaluate(_make_context(work_path=tmp_path))
        assert result.passed
        assert "skipped" in (result.details or {})

    def test_policy_dir_resolved_relative_to_work_path(self, tmp_path):
        (tmp_path / "policies").mkdir()
        (tmp_path / "policies" / "test.rego").write_text("package test")
        opa_result = OPAResult(passed=True, violations=[])
        with self._mock_run_opa(opa_result) as mock:
            policy = OPAPolicy(_make_policy(policy_dir="policies/"))
            policy.evaluate(_make_context(work_path=tmp_path))
        _, kwargs = mock.call_args
        assert str(tmp_path / "policies") in str(kwargs.get("policy_dir") or mock.call_args[0])

    def test_warn_enforcement_fails_but_is_warn(self):
        opa_result = OPAResult(passed=False, violations=["msg"])
        with self._mock_run_opa(opa_result):
            policy = OPAPolicy(_make_policy(enforcement="warn"))
            result = policy.evaluate(_make_context())
        assert not result.passed
        assert result.enforcement == "warn"

    def test_details_populated(self):
        opa_result = OPAResult(passed=True, violations=[])
        with self._mock_run_opa(opa_result):
            policy = OPAPolicy(_make_policy())
            result = policy.evaluate(_make_context())
        d = result.details or {}
        assert d["rule"] == "data.strata.test.deny"
        assert "mode" in d


# ===========================================================================
# OPAPolicy._build_input
# ===========================================================================


class TestBuildInput:
    def test_phase_always_present(self):
        policy = OPAPolicy(_make_policy())
        ctx = PolicyContext(phase="deploy", work_path=None)
        doc = policy._build_input(ctx)
        assert doc["phase"] == "deploy"

    def test_paths_set_when_available(self, tmp_path):
        policy = OPAPolicy(_make_policy())
        ctx = PolicyContext(phase="build", work_path=tmp_path, build_path=tmp_path / "build")
        doc = policy._build_input(ctx)
        assert doc["work_path"] == str(tmp_path)
        assert doc["build_path"] == str(tmp_path / "build")

    def test_plan_data_included(self):
        policy = OPAPolicy(_make_policy())
        ctx = PolicyContext(phase="plan", work_path=None, plan_data={"resource_changes": []})
        doc = policy._build_input(ctx)
        assert "plan_data" in doc
        assert doc["plan_data"] == {"resource_changes": []}

    def test_configuration_service_serialized(self):
        policy = OPAPolicy(_make_policy())
        mock_model = MagicMock()
        mock_model.model_dump.return_value = {"kind": "configuration", "spec": {}}
        cfg_svc = MagicMock()
        cfg_svc.model = mock_model
        ctx = PolicyContext(phase="build", work_path=None, configuration_service=cfg_svc)
        doc = policy._build_input(ctx)
        assert "configuration" in doc
        assert doc["configuration"]["kind"] == "configuration"

    def test_none_services_omitted(self):
        policy = OPAPolicy(_make_policy())
        ctx = PolicyContext(phase="build", work_path=None)
        doc = policy._build_input(ctx)
        assert "platform" not in doc
        assert "configuration" not in doc
        assert "deployment" not in doc
