"""Tests for CheckovPolicy and CheckovIntegration JSON parsing.

Tests use subprocess mocking — no real Checkov binary required.
"""

import json
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

from strata.integrations.checkov import (
    CheckovFinding,
    CheckovIntegration,
    CheckovScanResult,
    _severity_index,
)
from strata.models.integration_model import IntegrationModel
from strata.models.policy_model import PolicyModel
from strata.models.workspace_model import SourceModel, WorkspaceIacModel
from strata.validators.policies.base_policy import PolicyContext
from strata.validators.policies.checkov_policy import CheckovPolicy

# ===========================================================================
# Fixtures & helpers
# ===========================================================================

_CHECKOV_SINGLE = {
    "check_type": "terraform",
    "results": {
        "passed_checks": [
            {
                "check_id": "CKV_AWS_23",
                "check_name": "Ensure logging is enabled",
                "resource": "aws_s3_bucket.logs",
                "file_path": "main.tf",
                "file_line_range": [1, 10],
                "severity": "LOW",
            },
        ],
        "failed_checks": [
            {
                "check_id": "CKV_AWS_144",
                "check_name": "Ensure S3 bucket versioning is enabled",
                "resource": "aws_s3_bucket.data",
                "file_path": "main.tf",
                "file_line_range": [20, 28],
                "severity": "HIGH",
            },
            {
                "check_id": "CKV_AWS_145",
                "check_name": "Ensure S3 bucket is encrypted",
                "resource": "aws_s3_bucket.data",
                "file_path": "main.tf",
                "file_line_range": [30, 40],
                "severity": "MEDIUM",
            },
        ],
        "skipped_checks": [],
    },
    "summary": {"passed": 1, "failed": 2, "skipped": 0},
}

_CHECKOV_CRITICAL = {
    "check_type": "terraform",
    "results": {
        "passed_checks": [],
        "failed_checks": [
            {
                "check_id": "CKV_AWS_1",
                "check_name": "Public S3",
                "resource": "aws_s3_bucket.pub",
                "file_path": "main.tf",
                "file_line_range": [1, 5],
                "severity": "CRITICAL",
            },
        ],
        "skipped_checks": [],
    },
    "summary": {"passed": 0, "failed": 1, "skipped": 0},
}

_CHECKOV_MULTI = [_CHECKOV_SINGLE, _CHECKOV_CRITICAL]


def _make_policy(severity_gate="high", skip_checks=None, enforcement="deny", include_checks=None, scope=None):
    cfg = {"severity_gate": severity_gate}
    if scope is not None:
        cfg["scope"] = scope
    if skip_checks:
        cfg["skip_checks"] = skip_checks
    if include_checks:
        cfg["include_checks"] = include_checks
    return PolicyModel(name="test_checkov", type="checkov", phase="build", enforcement=enforcement, configuration=cfg)


def _make_context(
    build_path: Optional[Path] = None,
    deployment_service=None,
    solution_controller=None,
) -> "PolicyContext":
    return PolicyContext(
        phase="build",
        work_path=build_path,
        build_path=build_path,
        deployment_service=deployment_service,
        solution_controller=solution_controller,
    )


def _make_integration() -> "CheckovIntegration":
    config = IntegrationModel(name="checkov", type="checkov")
    return CheckovIntegration(config)


def _make_provisioner(
    name: str,
    source_path: str = "terraform",
    target_path: Optional[str] = None,
    provisioner: str = "terraform",
) -> WorkspaceIacModel:
    """Build a real WorkspaceIacModel (ADR-0051 revision, 2026-09-16). ``provisioner``
    defaults to "terraform" but also supports "bicep"/"ansible" for the
    multi-framework follow-up (2026-09-16)."""
    source = SourceModel(source_path=source_path, target_path=target_path, repository="repo")
    return WorkspaceIacModel(name=name, provisioner=provisioner, source=source)


def _make_stage(name="stage1", provisioner=None, topology=None):
    stage = MagicMock()
    stage.name = name
    stage.provisioner = provisioner
    stage.topology = topology
    stage.helm_namespaces = None  # "no filtering" default — a bare MagicMock attr would
    # otherwise iterate as empty (MagicMock auto-configures __iter__ -> iter([])),
    # silently narrowing helm_namespaces_for_stage() to zero namespaces instead of "all".
    return stage


def _make_deployment_service(build_path: Path, provisioners, stages=None, topology=None) -> MagicMock:
    """Mock DeploymentService whose get_build_path() is identity (returns build_path
    unchanged) — keeps path-shape assertions focused on provisioner resolution rather
    than deployment-scoped build path arithmetic (covered elsewhere)."""
    svc = MagicMock()
    svc.get_build_path.side_effect = lambda bp: bp

    workspace_service = MagicMock()
    workspace_service.model.spec.provisioners = provisioners
    workspace_service.model.spec.topology = topology or []
    svc.get_workspace_service.return_value = workspace_service

    svc.model.spec.stages = stages or []
    return svc


# ===========================================================================
# CheckovIntegration — _parse_output
# ===========================================================================


class TestCheckovIntegrationParsing:
    def test_parse_single_block(self):
        integ = _make_integration()
        result = integ._parse_output(json.dumps(_CHECKOV_SINGLE), "terraform", "/build/terraform")
        assert result.passed == 1
        assert result.failed == 2
        assert result.skipped == 0
        assert len(result.findings) == 2
        assert result.framework == "terraform"
        assert result.scanned_path == "/build/terraform"

    def test_parse_finding_fields(self):
        integ = _make_integration()
        result = integ._parse_output(json.dumps(_CHECKOV_SINGLE), "terraform", "/build")
        high = next(f for f in result.findings if f.check_id == "CKV_AWS_144")
        assert high.severity == "HIGH"
        assert high.resource == "aws_s3_bucket.data"
        assert high.file_path == "main.tf"
        assert high.file_line_range == [20, 28]

    def test_parse_multi_block_list(self):
        integ = _make_integration()
        result = integ._parse_output(json.dumps(_CHECKOV_MULTI), "terraform", "/build")
        # single (1+2) + critical (0+1)
        assert result.passed == 1
        assert result.failed == 3
        assert len(result.findings) == 3

    def test_parse_empty_output_returns_empty_result(self):
        integ = _make_integration()
        result = integ._parse_output("", "terraform", "/build")
        assert result.failed == 0
        assert result.findings == []

    def test_parse_invalid_json_returns_empty(self):
        integ = _make_integration()
        result = integ._parse_output("not json", "terraform", "/build")
        assert result.findings == []

    def test_unknown_severity_normalised(self):
        block = {
            "results": {
                "passed_checks": [],
                "failed_checks": [
                    {
                        "check_id": "CKV_X_1",
                        "check_name": "foo",
                        "resource": "r",
                        "file_path": "f.tf",
                        "severity": "NOTAREAL",
                    },
                ],
                "skipped_checks": [],
            },
            "summary": {"passed": 0, "failed": 1, "skipped": 0},
        }
        integ = _make_integration()
        result = integ._parse_output(json.dumps(block), "terraform", "/build")
        assert result.findings[0].severity == "UNKNOWN"

    def test_missing_severity_defaults_to_unknown(self):
        block = {
            "results": {
                "passed_checks": [],
                "failed_checks": [
                    {"check_id": "CKV_X_2", "check_name": "bar", "resource": "r", "file_path": "f.tf"},
                ],
                "skipped_checks": [],
            },
            "summary": {"passed": 0, "failed": 1, "skipped": 0},
        }
        integ = _make_integration()
        result = integ._parse_output(json.dumps(block), "terraform", "/build")
        assert result.findings[0].severity == "UNKNOWN"


# ===========================================================================
# CheckovScanResult.findings_at_or_above
# ===========================================================================


class TestCheckovScanResultFiltering:
    def _make_result(self, severities):
        findings = [
            CheckovFinding(check_id=f"CKV_{i}", check_name="n", resource="r", file_path="f.tf", severity=sev)
            for i, sev in enumerate(severities)
        ]
        return CheckovScanResult(
            passed=0,
            failed=len(findings),
            skipped=0,
            findings=findings,
            scanner_version="3.0",
            framework="terraform",
            scanned_path="/b",
        )

    def test_at_or_above_high_returns_critical_and_high(self):
        result = self._make_result(["CRITICAL", "HIGH", "MEDIUM", "LOW"])
        breaching = result.findings_at_or_above("HIGH")
        assert len(breaching) == 2
        severities = {f.severity for f in breaching}
        assert severities == {"CRITICAL", "HIGH"}

    def test_at_or_above_critical_only(self):
        result = self._make_result(["CRITICAL", "HIGH", "MEDIUM"])
        breaching = result.findings_at_or_above("CRITICAL")
        assert len(breaching) == 1
        assert breaching[0].severity == "CRITICAL"

    def test_at_or_above_medium(self):
        result = self._make_result(["CRITICAL", "HIGH", "MEDIUM", "LOW"])
        breaching = result.findings_at_or_above("MEDIUM")
        assert len(breaching) == 3

    def test_no_findings_returns_empty(self):
        result = CheckovScanResult(
            passed=5, failed=0, skipped=0, findings=[], scanner_version="3", framework="terraform", scanned_path="/b"
        )
        assert result.findings_at_or_above("HIGH") == []

    def test_total_property(self):
        result = self._make_result(["HIGH", "MEDIUM"])
        result.passed = 3
        result.skipped = 1
        assert result.total == 6  # 3 + 2 + 1


# ===========================================================================
# _severity_index helper
# ===========================================================================


class TestSeverityIndex:
    def test_critical_is_lowest_index(self):
        assert _severity_index("CRITICAL") < _severity_index("HIGH")
        assert _severity_index("HIGH") < _severity_index("MEDIUM")
        assert _severity_index("MEDIUM") < _severity_index("LOW")

    def test_unknown_severity_maps_to_last(self):
        assert _severity_index("UNKNOWN") == _severity_index("UNKNOWN")
        assert _severity_index("GIBBERISH") == _severity_index("UNKNOWN")

    def test_case_insensitive(self):
        assert _severity_index("critical") == _severity_index("CRITICAL")
        assert _severity_index("High") == _severity_index("HIGH")


# ===========================================================================
# CheckovPolicy.evaluate
# ===========================================================================


class TestCheckovPolicyEvaluate:
    def _scan_result_from_block(self, block_json, framework="terraform"):
        integ = _make_integration()
        return integ._parse_output(json.dumps(block_json), framework, "/build/terraform")

    def _mock_policy_scan(self, scan_result):
        """Patch CheckovPolicy._run_scan to return a fixed result for every call."""
        return patch.object(CheckovPolicy, "_run_scan", return_value=scan_result)

    def _mock_resolve_dirs(self, dirs, reason=None):
        return patch.object(CheckovPolicy, "_resolve_provisioner_dirs", return_value=(dirs, reason))

    def test_skip_no_build_path(self):
        policy = CheckovPolicy(_make_policy())
        context = _make_context(build_path=None)
        result = policy.evaluate(context)
        assert result.passed
        assert "skipped" in (result.details or {})
        assert result.warnings, "a skip must never be silent"

    def test_skip_no_terraform_dir(self, tmp_path):
        policy = CheckovPolicy(_make_policy())
        with self._mock_resolve_dirs([], "no terraform artifacts found for the selected scope"):
            context = _make_context(build_path=tmp_path)
            result = policy.evaluate(context)
        assert result.passed
        assert "no terraform artifacts" in (result.details or {}).get("skipped", "")
        assert any("no terraform artifacts" in w for w in result.warnings)

    def test_skip_checkov_unavailable(self, tmp_path):
        policy = CheckovPolicy(_make_policy())
        with self._mock_resolve_dirs([("control_infra", tmp_path / "terraform")]), self._mock_policy_scan(None):
            context = _make_context(build_path=tmp_path)
            result = policy.evaluate(context)
        assert result.passed
        assert result.details["provisioners"][0]["skipped"] is True
        assert any("control_infra" in w for w in result.warnings)

    def test_pass_no_findings_above_gate(self, tmp_path):
        scan = self._scan_result_from_block(
            {
                "results": {
                    "passed_checks": [
                        {
                            "check_id": "CKV_X",
                            "check_name": "ok",
                            "resource": "r",
                            "file_path": "f.tf",
                            "severity": "LOW",
                        }
                    ],
                    "failed_checks": [],
                    "skipped_checks": [],
                },
                "summary": {"passed": 1, "failed": 0, "skipped": 0},
            }
        )
        policy = CheckovPolicy(_make_policy(severity_gate="high"))
        with self._mock_resolve_dirs([("control_infra", tmp_path / "terraform")]), self._mock_policy_scan(scan):
            result = policy.evaluate(_make_context(tmp_path))
        assert result.passed
        assert result.violations == []
        assert result.details["provisioners"][0]["passed"] == 1

    def test_fail_high_finding_above_gate(self, tmp_path):
        scan = self._scan_result_from_block(_CHECKOV_SINGLE)
        policy = CheckovPolicy(_make_policy(severity_gate="high"))
        with self._mock_resolve_dirs([("control_infra", tmp_path / "terraform")]), self._mock_policy_scan(scan):
            result = policy.evaluate(_make_context(tmp_path))
        assert not result.passed
        assert len(result.violations) == 1
        assert "CKV_AWS_144" in result.violations[0]
        assert "[HIGH]" in result.violations[0]
        assert "[control_infra]" in result.violations[0]

    def test_fail_critical_finding(self, tmp_path):
        scan = self._scan_result_from_block(_CHECKOV_CRITICAL)
        policy = CheckovPolicy(_make_policy(severity_gate="high"))
        with self._mock_resolve_dirs([("control_infra", tmp_path / "terraform")]), self._mock_policy_scan(scan):
            result = policy.evaluate(_make_context(tmp_path))
        assert not result.passed
        assert "CKV_AWS_1" in result.violations[0]

    def test_medium_gate_catches_medium_findings(self, tmp_path):
        scan = self._scan_result_from_block(_CHECKOV_SINGLE)
        policy = CheckovPolicy(_make_policy(severity_gate="medium"))
        with self._mock_resolve_dirs([("control_infra", tmp_path / "terraform")]), self._mock_policy_scan(scan):
            result = policy.evaluate(_make_context(tmp_path))
        assert not result.passed
        assert len(result.violations) == 2  # HIGH + MEDIUM

    def test_high_gate_ignores_medium_findings(self, tmp_path):
        scan = self._scan_result_from_block(_CHECKOV_SINGLE)
        policy = CheckovPolicy(_make_policy(severity_gate="high"))
        with self._mock_resolve_dirs([("control_infra", tmp_path / "terraform")]), self._mock_policy_scan(scan):
            result = policy.evaluate(_make_context(tmp_path))
        assert not result.passed
        assert len(result.violations) == 1  # only HIGH

    def test_warn_enforcement_fails_but_does_not_deny(self, tmp_path):
        scan = self._scan_result_from_block(_CHECKOV_CRITICAL)
        policy = CheckovPolicy(_make_policy(severity_gate="high", enforcement="warn"))
        with self._mock_resolve_dirs([("control_infra", tmp_path / "terraform")]), self._mock_policy_scan(scan):
            result = policy.evaluate(_make_context(tmp_path))
        assert not result.passed
        assert result.enforcement == "warn"

    def test_invalid_severity_gate_skips(self, tmp_path):
        policy = CheckovPolicy(_make_policy(severity_gate="extreme"))
        context = _make_context(tmp_path)
        result = policy.evaluate(context)
        assert result.passed
        assert "invalid severity_gate" in (result.details or {}).get("skipped", "")
        assert any("invalid severity_gate" in w for w in result.warnings)

    def test_details_populated(self, tmp_path):
        scan = self._scan_result_from_block(_CHECKOV_SINGLE)
        policy = CheckovPolicy(_make_policy())
        with self._mock_resolve_dirs([("control_infra", tmp_path / "terraform")]), self._mock_policy_scan(scan):
            result = policy.evaluate(_make_context(tmp_path))
        d = result.details or {}
        assert d["scanner"] == "checkov"
        assert d["scope"] == "staged"
        assert d["severity_gate"] == "HIGH"
        prov_detail = d["provisioners"][0]
        assert prov_detail["provisioner"] == "control_infra"
        assert prov_detail["passed"] == 1
        assert prov_detail["failed"] == 2
        assert prov_detail["breaching_count"] == 1  # only the HIGH one (gate=high)

    def test_multi_provisioner_any_breach_denies_all(self, tmp_path):
        """2. any fail is fail all — but still reported per provisioner."""
        clean = self._scan_result_from_block(
            {
                "results": {"passed_checks": [], "failed_checks": [], "skipped_checks": []},
                "summary": {"passed": 0, "failed": 0, "skipped": 0},
            }
        )
        breach = self._scan_result_from_block(_CHECKOV_CRITICAL)
        policy = CheckovPolicy(_make_policy(severity_gate="high"))
        dirs = [("control_infra", tmp_path / "control"), ("core_modules", tmp_path / "core")]
        with self._mock_resolve_dirs(dirs), patch.object(CheckovPolicy, "_run_scan", side_effect=[clean, breach]):
            result = policy.evaluate(_make_context(tmp_path))
        assert not result.passed
        assert len(result.details["provisioners"]) == 2
        assert any("[core_modules]" in v for v in result.violations)
        assert not any("[control_infra]" in v for v in result.violations)

    def test_multi_provisioner_partial_scan_failure_warns_without_denying(self, tmp_path):
        clean = self._scan_result_from_block(
            {
                "results": {"passed_checks": [], "failed_checks": [], "skipped_checks": []},
                "summary": {"passed": 0, "failed": 0, "skipped": 0},
            }
        )
        policy = CheckovPolicy(_make_policy(severity_gate="high"))
        dirs = [("control_infra", tmp_path / "control"), ("core_modules", tmp_path / "core")]
        with self._mock_resolve_dirs(dirs), patch.object(CheckovPolicy, "_run_scan", side_effect=[clean, None]):
            result = policy.evaluate(_make_context(tmp_path))
        assert result.passed  # nothing breached among what WAS scanned
        assert any("core_modules" in w for w in result.warnings)

    def test_unsupported_framework_skips_with_supported_list(self, tmp_path):
        policy = CheckovPolicy(_make_policy())
        policy.policy.configuration["framework"] = "compose"
        result = policy.evaluate(_make_context(tmp_path))
        assert result.passed
        skipped = (result.details or {}).get("skipped", "")
        assert "framework 'compose'" in skipped
        assert "ansible" in skipped and "bicep" in skipped and "terraform" in skipped
        assert any("framework 'compose'" in w for w in result.warnings)

    def test_bicep_framework_end_to_end(self, tmp_path):
        bicep = _make_provisioner("platform_bicep", source_path="bicep", provisioner="bicep")
        stage = _make_stage("infra", provisioner="platform_bicep")
        dep_svc = _make_deployment_service(tmp_path, [bicep], stages=[stage])
        (tmp_path / "bicep").mkdir()
        (tmp_path / "bicep" / "main.bicep").write_text("resource x 'Microsoft.Storage/x@2021-01-01' = {}")

        scan = self._scan_result_from_block(_CHECKOV_SINGLE, framework="bicep")
        policy = CheckovPolicy(_make_policy(severity_gate="high"))
        policy.policy.configuration["framework"] = "bicep"
        with self._mock_policy_scan(scan):
            result = policy.evaluate(_make_context(tmp_path, deployment_service=dep_svc))

        assert not result.passed
        assert result.details["provisioners"][0]["provisioner"] == "platform_bicep"

    def test_ansible_framework_end_to_end(self, tmp_path):
        ansible = _make_provisioner("config", source_path="ansible", provisioner="ansible")
        stage = _make_stage("configure", provisioner="config")
        dep_svc = _make_deployment_service(tmp_path, [ansible], stages=[stage])
        (tmp_path / "ansible").mkdir()
        (tmp_path / "ansible" / "site.yml").write_text("- hosts: all")

        scan = self._scan_result_from_block(
            {
                "results": {"passed_checks": [], "failed_checks": [], "skipped_checks": []},
                "summary": {"passed": 0, "failed": 0, "skipped": 0},
            },
            framework="ansible",
        )
        policy = CheckovPolicy(_make_policy())
        policy.policy.configuration["framework"] = "ansible"
        with self._mock_policy_scan(scan):
            result = policy.evaluate(_make_context(tmp_path, deployment_service=dep_svc))

        assert result.passed
        assert result.details["provisioners"][0]["provisioner"] == "config"


# ===========================================================================
# CheckovPolicy._resolve_provisioner_dirs
# ===========================================================================


class TestCheckovPolicyResolveProvisionerDirs:
    def test_no_build_path_skips(self):
        policy = CheckovPolicy(_make_policy())
        context = _make_context(build_path=None)
        dirs, reason = policy._resolve_provisioner_dirs(context, "staged", "terraform")
        assert dirs == []
        assert "no terraform artifacts" in reason

    def test_no_deployment_service_skips(self, tmp_path):
        policy = CheckovPolicy(_make_policy())
        context = _make_context(build_path=tmp_path)  # deployment_service defaults to None
        dirs, reason = policy._resolve_provisioner_dirs(context, "staged", "terraform")
        assert dirs == []
        assert "no terraform artifacts" in reason

    def test_no_terraform_provisioners_declared(self, tmp_path):
        ansible_prov = MagicMock()
        ansible_prov.name = "app"
        ansible_prov.provisioner = "ansible"
        dep_svc = _make_deployment_service(tmp_path, [ansible_prov])
        policy = CheckovPolicy(_make_policy())
        context = _make_context(tmp_path, deployment_service=dep_svc)
        dirs, reason = policy._resolve_provisioner_dirs(context, "staged", "terraform")
        assert dirs == []
        assert "no terraform provisioners" in reason

    def test_staged_scope_excludes_unstaged_provisioner(self, tmp_path):
        control = _make_provisioner("control_infra", source_path="control/terraform")
        core = _make_provisioner("core_modules", source_path="core/terraform")
        stage = _make_stage("infrastructure", provisioner="control_infra")
        dep_svc = _make_deployment_service(tmp_path, [control, core], stages=[stage])

        (tmp_path / "control" / "terraform").mkdir(parents=True)
        (tmp_path / "control" / "terraform" / "main.tf").write_text("# tf")
        (tmp_path / "core" / "terraform").mkdir(parents=True)
        (tmp_path / "core" / "terraform" / "main.tf").write_text("# tf")

        policy = CheckovPolicy(_make_policy())
        context = _make_context(tmp_path, deployment_service=dep_svc)  # no solution_controller -> fallback shape
        dirs, reason = policy._resolve_provisioner_dirs(context, "staged", "terraform")

        assert reason is None
        assert [name for name, _ in dirs] == ["control_infra"]

    def test_all_scope_includes_unstaged_provisioner(self, tmp_path):
        control = _make_provisioner("control_infra", source_path="control/terraform")
        core = _make_provisioner("core_modules", source_path="core/terraform")
        stage = _make_stage("infrastructure", provisioner="control_infra")
        dep_svc = _make_deployment_service(tmp_path, [control, core], stages=[stage])

        (tmp_path / "control" / "terraform").mkdir(parents=True)
        (tmp_path / "control" / "terraform" / "main.tf").write_text("# tf")
        (tmp_path / "core" / "terraform").mkdir(parents=True)
        (tmp_path / "core" / "terraform" / "main.tf").write_text("# tf")

        policy = CheckovPolicy(_make_policy())
        context = _make_context(tmp_path, deployment_service=dep_svc)
        dirs, reason = policy._resolve_provisioner_dirs(context, "all", "terraform")

        assert reason is None
        assert {name for name, _ in dirs} == {"control_infra", "core_modules"}

    def test_named_stage_scope(self, tmp_path):
        control = _make_provisioner("control_infra", source_path="control/terraform")
        core = _make_provisioner("core_modules", source_path="core/terraform")
        stage = _make_stage("infrastructure", provisioner="control_infra")
        dep_svc = _make_deployment_service(tmp_path, [control, core], stages=[stage])

        (tmp_path / "control" / "terraform").mkdir(parents=True)
        (tmp_path / "control" / "terraform" / "main.tf").write_text("# tf")
        (tmp_path / "core" / "terraform").mkdir(parents=True)
        (tmp_path / "core" / "terraform" / "main.tf").write_text("# tf")

        policy = CheckovPolicy(_make_policy())
        context = _make_context(tmp_path, deployment_service=dep_svc)
        dirs, reason = policy._resolve_provisioner_dirs(context, "infrastructure", "terraform")

        assert reason is None
        assert [name for name, _ in dirs] == ["control_infra"]

    def test_invalid_stage_name_scope_lists_valid_names(self, tmp_path):
        control = _make_provisioner("control_infra", source_path="terraform")
        stage = _make_stage("infrastructure", provisioner="control_infra")
        dep_svc = _make_deployment_service(tmp_path, [control], stages=[stage])
        (tmp_path / "terraform").mkdir()
        (tmp_path / "terraform" / "main.tf").write_text("# tf")

        policy = CheckovPolicy(_make_policy())
        context = _make_context(tmp_path, deployment_service=dep_svc)
        dirs, reason = policy._resolve_provisioner_dirs(context, "bogus_stage", "terraform")

        assert dirs == []
        assert "invalid scope 'bogus_stage'" in reason
        assert "infrastructure" in reason

    def test_no_tf_files_on_disk_reports_empty(self, tmp_path):
        control = _make_provisioner("control_infra", source_path="terraform")
        dep_svc = _make_deployment_service(tmp_path, [control])
        (tmp_path / "terraform").mkdir()  # exists but no .tf files

        policy = CheckovPolicy(_make_policy())
        context = _make_context(tmp_path, deployment_service=dep_svc)
        dirs, reason = policy._resolve_provisioner_dirs(context, "staged", "terraform")

        assert dirs == []
        assert "no terraform artifacts found for the selected scope" in reason

    def test_uses_solution_controller_get_provisioner_path_when_available(self, tmp_path):
        control = _make_provisioner("control_infra", source_path="control/terraform")
        stage = _make_stage("infra", provisioner="control_infra")
        dep_svc = _make_deployment_service(tmp_path, [control], stages=[stage])
        tf_dir = tmp_path / "somewhere_else" / "terraform"
        tf_dir.mkdir(parents=True)
        (tf_dir / "main.tf").write_text("# tf")

        solution_controller = MagicMock()
        solution_controller.get_provisioner_path.return_value = tf_dir

        policy = CheckovPolicy(_make_policy())
        context = _make_context(tmp_path, deployment_service=dep_svc, solution_controller=solution_controller)
        dirs, reason = policy._resolve_provisioner_dirs(context, "staged", "terraform")

        assert reason is None
        assert dirs == [("control_infra", tf_dir)]
        solution_controller.get_provisioner_path.assert_called_once_with(dep_svc, tmp_path, control)

    def test_fallback_shape_prefers_target_path_over_source_path(self, tmp_path):
        control = _make_provisioner("control_infra", source_path="control/terraform", target_path="override/terraform")
        stage = _make_stage("infra", provisioner="control_infra")
        dep_svc = _make_deployment_service(tmp_path, [control], stages=[stage])
        (tmp_path / "override" / "terraform").mkdir(parents=True)
        (tmp_path / "override" / "terraform" / "main.tf").write_text("# tf")

        policy = CheckovPolicy(_make_policy())
        context = _make_context(tmp_path, deployment_service=dep_svc)  # no solution_controller -> fallback
        dirs, reason = policy._resolve_provisioner_dirs(context, "staged", "terraform")

        assert reason is None
        assert dirs == [("control_infra", tmp_path / "override" / "terraform")]


# ===========================================================================
# CheckovPolicy._resolve_helm_module_dirs (ADR-0051 Helm follow-up, 2026-09-17)
# ===========================================================================


def _make_module_ref(file_name: str) -> MagicMock:
    ref = MagicMock()
    ref.file = file_name
    return ref


def _make_module_service(name: str, chart_repository: Optional[str] = None, module_type: str = "helm") -> MagicMock:
    mod_service = MagicMock()
    mod_service.is_validated.return_value = True
    mod_service.model.meta.name = name
    mod_service.model.spec.type = module_type
    mod_service.model.spec.source.chart_repository = chart_repository
    return mod_service


def _make_helm_deployment_service(
    tmp_path: Path,
    namespace_names,
    stages=None,
    helm_provisioner_name: str = "helm",
    namespace_module_refs=None,
) -> MagicMock:
    """Deployment service scaffolded for Helm resolution tests: a workspace with one
    helm provisioner, the given namespaces, and get_namespace_services() returning a
    NamespaceService per name whose spec.modules is namespace_module_refs[name]."""
    from strata.models.common_models import ProvisionerType
    from strata.models.workspace_model import WorkspaceNamespaceModel

    svc = MagicMock()
    svc.get_build_path.side_effect = lambda bp: bp
    svc.model.spec.stages = stages or []

    helm_prov = MagicMock()
    helm_prov.name = helm_provisioner_name
    helm_prov.provisioner = ProvisionerType.HELM

    workspace_service = MagicMock()
    workspace_service.model.spec.provisioners = [helm_prov]
    workspace_service.model.spec.namespaces = [
        WorkspaceNamespaceModel(name=n, file=f"{n}.yaml") for n in namespace_names
    ]
    svc.get_workspace_service.return_value = workspace_service

    namespace_module_refs = namespace_module_refs or {}
    ns_services = {}
    for name in namespace_names:
        ns_service = MagicMock()
        ns_service.is_validated.return_value = True
        ns_service.model.spec.modules = namespace_module_refs.get(name, [])
        ns_services[name] = ns_service
    svc.get_namespace_services.return_value = ns_services

    return svc


class TestCheckovPolicyHelmResolution:
    def test_no_build_path_skips(self):
        policy = CheckovPolicy(_make_policy())
        context = _make_context(build_path=None)
        dirs, reason, warnings = policy._resolve_helm_module_dirs(context, "staged")
        assert dirs == []
        assert "no helm artifacts" in reason
        assert warnings == []

    def test_no_namespaces_declared_skips(self, tmp_path):
        dep_svc = _make_helm_deployment_service(tmp_path, [])
        policy = CheckovPolicy(_make_policy())
        context = _make_context(tmp_path, deployment_service=dep_svc)
        dirs, reason, warnings = policy._resolve_helm_module_dirs(context, "staged")
        assert dirs == []
        assert "no namespaces declared" in reason

    def test_staged_scope_finds_local_chart(self, tmp_path):
        (tmp_path / "module.yaml").write_text("kind: module")
        module_ref = _make_module_ref("module.yaml")
        dep_svc = _make_helm_deployment_service(
            tmp_path,
            ["prod"],
            stages=[_make_stage("platform", provisioner="helm")],
            namespace_module_refs={"prod": [module_ref]},
        )

        chart_dir = tmp_path / "prod" / "nginx"
        chart_dir.mkdir(parents=True)
        (chart_dir / "Chart.yaml").write_text("apiVersion: v2")

        mod_service = _make_module_service("nginx")
        policy = CheckovPolicy(_make_policy())
        context = _make_context(tmp_path, deployment_service=dep_svc)  # no solution_controller -> fallback shape
        with patch("strata.services.module_service.ModuleService.load", return_value=mod_service):
            dirs, reason, warnings = policy._resolve_helm_module_dirs(context, "staged")

        assert reason is None
        assert dirs == [("prod/nginx", chart_dir)]
        assert warnings == []

    def test_registry_chart_module_skipped_with_warning(self, tmp_path):
        (tmp_path / "module.yaml").write_text("kind: module")
        module_ref = _make_module_ref("module.yaml")
        dep_svc = _make_helm_deployment_service(
            tmp_path,
            ["prod"],
            stages=[_make_stage("platform", provisioner="helm")],
            namespace_module_refs={"prod": [module_ref]},
        )

        mod_service = _make_module_service("authentik", chart_repository="https://charts.goauthentik.io")
        policy = CheckovPolicy(_make_policy())
        context = _make_context(tmp_path, deployment_service=dep_svc)
        with patch("strata.services.module_service.ModuleService.load", return_value=mod_service):
            dirs, reason, warnings = policy._resolve_helm_module_dirs(context, "staged")

        assert dirs == []
        assert "no helm artifacts found for the selected scope" in reason
        assert any("authentik" in w and "registry" in w for w in warnings)

    def test_mixed_local_and_registry_modules(self, tmp_path):
        (tmp_path / "local_module.yaml").write_text("kind: module")
        (tmp_path / "registry_module.yaml").write_text("kind: module")
        local_ref = _make_module_ref("local_module.yaml")
        registry_ref = _make_module_ref("registry_module.yaml")
        dep_svc = _make_helm_deployment_service(
            tmp_path,
            ["prod"],
            stages=[_make_stage("platform", provisioner="helm")],
            namespace_module_refs={"prod": [local_ref, registry_ref]},
        )

        chart_dir = tmp_path / "prod" / "nginx"
        chart_dir.mkdir(parents=True)
        (chart_dir / "Chart.yaml").write_text("apiVersion: v2")

        local_mod = _make_module_service("nginx")
        registry_mod = _make_module_service("authentik", chart_repository="https://charts.goauthentik.io")
        policy = CheckovPolicy(_make_policy())
        context = _make_context(tmp_path, deployment_service=dep_svc)
        with patch("strata.services.module_service.ModuleService.load", side_effect=[local_mod, registry_mod]):
            dirs, reason, warnings = policy._resolve_helm_module_dirs(context, "staged")

        assert reason is None
        assert dirs == [("prod/nginx", chart_dir)]
        assert any("authentik" in w for w in warnings)

    def test_missing_chart_yaml_excludes_module(self, tmp_path):
        (tmp_path / "module.yaml").write_text("kind: module")
        module_ref = _make_module_ref("module.yaml")
        dep_svc = _make_helm_deployment_service(
            tmp_path,
            ["prod"],
            stages=[_make_stage("platform", provisioner="helm")],
            namespace_module_refs={"prod": [module_ref]},
        )
        (tmp_path / "prod" / "nginx").mkdir(parents=True)  # no Chart.yaml written

        mod_service = _make_module_service("nginx")
        policy = CheckovPolicy(_make_policy())
        context = _make_context(tmp_path, deployment_service=dep_svc)
        with patch("strata.services.module_service.ModuleService.load", return_value=mod_service):
            dirs, reason, warnings = policy._resolve_helm_module_dirs(context, "staged")

        assert dirs == []
        assert "no helm artifacts found for the selected scope" in reason

    def test_non_helm_module_type_excluded(self, tmp_path):
        (tmp_path / "module.yaml").write_text("kind: module")
        module_ref = _make_module_ref("module.yaml")
        dep_svc = _make_helm_deployment_service(
            tmp_path,
            ["prod"],
            stages=[_make_stage("platform", provisioner="helm")],
            namespace_module_refs={"prod": [module_ref]},
        )
        chart_dir = tmp_path / "prod" / "app"
        chart_dir.mkdir(parents=True)
        (chart_dir / "Chart.yaml").write_text("apiVersion: v2")

        mod_service = _make_module_service("app", module_type="compose")
        policy = CheckovPolicy(_make_policy())
        context = _make_context(tmp_path, deployment_service=dep_svc)
        with patch("strata.services.module_service.ModuleService.load", return_value=mod_service):
            dirs, reason, warnings = policy._resolve_helm_module_dirs(context, "staged")

        assert dirs == []
        assert "no helm artifacts found for the selected scope" in reason

    def test_named_stage_scope_respects_helm_namespaces_allowlist(self, tmp_path):
        for ns in ("prod", "staging"):
            (tmp_path / f"{ns}_module.yaml").write_text("kind: module")
        prod_ref = _make_module_ref("prod_module.yaml")
        staging_ref = _make_module_ref("staging_module.yaml")

        stage = _make_stage("prod_only", provisioner="helm")
        stage.helm_namespaces = ["prod"]
        dep_svc = _make_helm_deployment_service(
            tmp_path,
            ["prod", "staging"],
            stages=[stage],
            namespace_module_refs={"prod": [prod_ref], "staging": [staging_ref]},
        )

        for ns in ("prod", "staging"):
            chart_dir = tmp_path / ns / "nginx"
            chart_dir.mkdir(parents=True)
            (chart_dir / "Chart.yaml").write_text("apiVersion: v2")

        mod_service = _make_module_service("nginx")
        policy = CheckovPolicy(_make_policy())
        context = _make_context(tmp_path, deployment_service=dep_svc)
        with patch("strata.services.module_service.ModuleService.load", return_value=mod_service):
            dirs, reason, warnings = policy._resolve_helm_module_dirs(context, "prod_only")

        assert reason is None
        assert dirs == [("prod/nginx", tmp_path / "prod" / "nginx")]

    def test_invalid_scope_lists_valid_stage_names(self, tmp_path):
        dep_svc = _make_helm_deployment_service(
            tmp_path, ["prod"], stages=[_make_stage("platform", provisioner="helm")]
        )
        policy = CheckovPolicy(_make_policy())
        context = _make_context(tmp_path, deployment_service=dep_svc)
        dirs, reason, warnings = policy._resolve_helm_module_dirs(context, "bogus_stage")
        assert dirs == []
        assert "invalid scope 'bogus_stage'" in reason
        assert "platform" in reason

    def test_all_scope_includes_namespace_with_no_matching_stage(self, tmp_path):
        (tmp_path / "module.yaml").write_text("kind: module")
        module_ref = _make_module_ref("module.yaml")
        # No stages at all -> "staged" would find nothing, but "all" still works.
        dep_svc = _make_helm_deployment_service(
            tmp_path, ["prod"], stages=[], namespace_module_refs={"prod": [module_ref]}
        )
        chart_dir = tmp_path / "prod" / "nginx"
        chart_dir.mkdir(parents=True)
        (chart_dir / "Chart.yaml").write_text("apiVersion: v2")

        mod_service = _make_module_service("nginx")
        policy = CheckovPolicy(_make_policy())
        context = _make_context(tmp_path, deployment_service=dep_svc)
        with patch("strata.services.module_service.ModuleService.load", return_value=mod_service):
            dirs, reason, warnings = policy._resolve_helm_module_dirs(context, "all")

        assert reason is None
        assert dirs == [("prod/nginx", chart_dir)]

    def test_uses_solution_controller_get_module_build_path_when_available(self, tmp_path):
        (tmp_path / "module.yaml").write_text("kind: module")
        module_ref = _make_module_ref("module.yaml")
        dep_svc = _make_helm_deployment_service(
            tmp_path,
            ["prod"],
            stages=[_make_stage("platform", provisioner="helm")],
            namespace_module_refs={"prod": [module_ref]},
        )
        chart_dir = tmp_path / "elsewhere" / "nginx"
        chart_dir.mkdir(parents=True)
        (chart_dir / "Chart.yaml").write_text("apiVersion: v2")

        solution_controller = MagicMock()
        solution_controller.get_module_build_path.return_value = chart_dir
        solution_controller.get_repo_map.return_value = {}

        mod_service = _make_module_service("nginx")
        policy = CheckovPolicy(_make_policy())
        context = _make_context(tmp_path, deployment_service=dep_svc, solution_controller=solution_controller)
        with patch("strata.services.module_service.ModuleService.load", return_value=mod_service):
            dirs, reason, warnings = policy._resolve_helm_module_dirs(context, "staged")

        assert reason is None
        assert dirs == [("prod/nginx", chart_dir)]
        solution_controller.get_module_build_path.assert_called_once_with(dep_svc, tmp_path, "prod", "nginx")


class TestCheckovPolicyHelmFrameworkEvaluate:
    def test_evaluate_with_helm_framework_end_to_end(self, tmp_path):
        (tmp_path / "module.yaml").write_text("kind: module")
        module_ref = _make_module_ref("module.yaml")
        dep_svc = _make_helm_deployment_service(
            tmp_path,
            ["prod"],
            stages=[_make_stage("platform", provisioner="helm")],
            namespace_module_refs={"prod": [module_ref]},
        )
        chart_dir = tmp_path / "prod" / "nginx"
        chart_dir.mkdir(parents=True)
        (chart_dir / "Chart.yaml").write_text("apiVersion: v2")

        mod_service = _make_module_service("nginx")
        policy = CheckovPolicy(_make_policy())
        policy.policy.configuration["framework"] = "helm"
        scan = _make_integration()._parse_output(json.dumps(_CHECKOV_SINGLE), "helm", "/build")
        context = _make_context(tmp_path, deployment_service=dep_svc)
        with (
            patch("strata.services.module_service.ModuleService.load", return_value=mod_service),
            patch.object(CheckovPolicy, "_run_scan", return_value=scan),
        ):
            result = policy.evaluate(context)

        assert not result.passed
        assert result.details["provisioners"][0]["provisioner"] == "prod/nginx"
        assert "[prod/nginx]" in result.violations[0]

    def test_evaluate_helm_all_registry_modules_skips_with_warnings(self, tmp_path):
        (tmp_path / "module.yaml").write_text("kind: module")
        module_ref = _make_module_ref("module.yaml")
        dep_svc = _make_helm_deployment_service(
            tmp_path,
            ["prod"],
            stages=[_make_stage("platform", provisioner="helm")],
            namespace_module_refs={"prod": [module_ref]},
        )
        mod_service = _make_module_service("authentik", chart_repository="https://charts.goauthentik.io")
        policy = CheckovPolicy(_make_policy())
        policy.policy.configuration["framework"] = "helm"
        context = _make_context(tmp_path, deployment_service=dep_svc)
        with patch("strata.services.module_service.ModuleService.load", return_value=mod_service):
            result = policy.evaluate(context)

        assert result.passed
        assert any("authentik" in w and "registry" in w for w in result.warnings)

    def test_bicep_framework_uses_bicep_glob(self, tmp_path):
        bicep = _make_provisioner("platform_bicep", source_path="bicep", provisioner="bicep")
        stage = _make_stage("infra", provisioner="platform_bicep")
        dep_svc = _make_deployment_service(tmp_path, [bicep], stages=[stage])
        (tmp_path / "bicep").mkdir()
        (tmp_path / "bicep" / "main.bicep").write_text("resource x 'Microsoft.Storage/x@2021-01-01' = {}")

        policy = CheckovPolicy(_make_policy())
        context = _make_context(tmp_path, deployment_service=dep_svc)
        dirs, reason = policy._resolve_provisioner_dirs(context, "staged", "bicep")

        assert reason is None
        assert dirs == [("platform_bicep", tmp_path / "bicep")]

    def test_ansible_framework_matches_yml_or_yaml(self, tmp_path):
        ansible = _make_provisioner("config", source_path="ansible", provisioner="ansible")
        stage = _make_stage("configure", provisioner="config")
        dep_svc = _make_deployment_service(tmp_path, [ansible], stages=[stage])
        (tmp_path / "ansible").mkdir()
        (tmp_path / "ansible" / "site.yaml").write_text("- hosts: all")  # .yaml, not .yml

        policy = CheckovPolicy(_make_policy())
        context = _make_context(tmp_path, deployment_service=dep_svc)
        dirs, reason = policy._resolve_provisioner_dirs(context, "staged", "ansible")

        assert reason is None
        assert dirs == [("config", tmp_path / "ansible")]

    def test_bicep_provisioner_excluded_when_framework_is_terraform(self, tmp_path):
        bicep = _make_provisioner("platform_bicep", source_path="bicep", provisioner="bicep")
        stage = _make_stage("infra", provisioner="platform_bicep")
        dep_svc = _make_deployment_service(tmp_path, [bicep], stages=[stage])
        (tmp_path / "bicep").mkdir()
        (tmp_path / "bicep" / "main.bicep").write_text("resource x 'Microsoft.Storage/x@2021-01-01' = {}")

        policy = CheckovPolicy(_make_policy())
        context = _make_context(tmp_path, deployment_service=dep_svc)
        dirs, reason = policy._resolve_provisioner_dirs(context, "staged", "terraform")

        assert dirs == []
        assert "no terraform provisioners" in reason
