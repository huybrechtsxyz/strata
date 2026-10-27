#!/usr/bin/env python3
"""Tests for `strata policy`'s controller (docs/work/security-gates-design.md D7; plan Phase 6).

A real on-disk solution is loaded with `open_solution()`; the gate integration is a vendor-neutral
fake returned by a monkeypatched `security_gate_controller.get_integration`, so no vendor code runs.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from strata.controllers import checkov_controller as checkov_module
from strata.controllers import policy_controller
from strata.controllers import sbom_controller as sbom_module
from strata.controllers import security_gate_controller as gates_module
from strata.controllers.checkov_controller import CheckovFindingsReport, evaluate_checkov_policy
from strata.controllers.policy_controller import check_policies, list_policies, policy_findings
from strata.controllers.policy_results import (
    checkov_policy_result,
    cve_policy_result,
    read_policy_results,
    write_policy_results,
)
from strata.controllers.sbom_controller import CveFindingsReport, evaluate_cve_policy
from strata.controllers.solution_context import open_solution
from strata.controllers.value_controller import resolve_deployment_workspace
from strata.integrations.capabilities import CredentialedIntegration, ImageFindingsIntegration, ReleaseGateIntegration
from strata.integrations.errors import IntegrationError
from strata.integrations.security_findings import ImageLookup, ReleaseGateOutcome, SecurityIssue
from strata.models.audit_manifest_model import ManifestPolicyResultModel
from strata.models.checkov_model import CheckovFindingModel, CheckovScanResultModel
from strata.models.sbom_model import CveAuditResultModel, CveFindingModel
from strata.models.workspace_model import WorkspaceSpecModel
from strata.utils.errors import SystemError, UsageError, ValidationError

COMMIT = "b" * 40
_HEADER = "apiVersion: strata.huybrechts.xyz/v2\nkind: {kind}\nmeta:\n  name: {name}\nspec:\n"


def _write(root: Path, relative: str, kind: str, name: str, spec: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_HEADER.format(kind=kind, name=name) + spec, encoding="utf-8")


def _solution(
    tmp_path: Path,
    *,
    release: bool = True,
    image: bool = True,
    cve: bool = False,
    checkov: bool = False,
    on_missing_data: str = "warn",
    enforcement: str = "deny",
    allowed: str | None = None,
    image_auth: bool = False,
) -> Path:
    root = tmp_path / "sln"
    root.mkdir(parents=True)
    (root / "strata.yaml").write_text(_HEADER.format(kind="solution", name="s") + "  {}\n", encoding="utf-8")
    (root / "infra").mkdir()
    (root / "infra" / "main.tf").write_text("# root module\n", encoding="utf-8")
    _write(root, "provider.yaml", "provider", "p1", "  type: local\n  region: local\n")
    _write(
        root,
        "resource.yaml",
        "resource",
        "r1",
        "  provider_type: local\n  resource_type: server\n  category: compute\n  default_tags:\n    managed-by: strata\n",
    )
    _write(root, "gate-ci.yaml", "integration", "gate-ci", "  type: fake-gate\n  capabilities: [release_gate]\n")
    auth = "  authentication:\n    method: api_key\n    api_key:\n      api_key: API_KEY\n" if image_auth else ""
    _write(
        root, "gate-api.yaml", "integration", "gate-api", f"  type: fake-gate\n  capabilities: [image_findings]\n{auth}"
    )
    policies = ""
    if cve:
        policies += (
            f"  cve_policy:\n    max_severity: HIGH\n    severity_threshold: MEDIUM\n"
            f"    enforcement: {enforcement}\n    on_missing_data: {on_missing_data}\n"
        )
    if checkov:
        policies += f"  checkov_policy:\n    enforcement: {enforcement}\n    on_missing_data: {on_missing_data}\n"
    if release:
        policies += (
            "  release_gate_policy:\n    integration: gate-ci\n    repository: org/infra\n"
            f"    enforcement: {enforcement}\n    commit: {COMMIT}\n"
        )
    if image:
        policies += f"  image_gate_policy:\n    integration: gate-api\n    enforcement: {enforcement}\n    max_severity: CRITICAL\n"
    _write(
        root,
        "workspace.yaml",
        "workspace",
        "main",
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n" + policies,
    )
    _write(
        root,
        "environment.yaml",
        "environment",
        "prd",
        "  variables:\n    - key: API_KEY\n      store: environment\n      value: TEST_POLICY_API_KEY\n",
    )
    _write(root, "deployment.yaml", "deployment", "app", "  workspace: main\n  environments:\n    - prd\n")
    if allowed is not None:
        _write(root, "configuration.yaml", "configuration", "config", allowed)
    return root


def _context(root: Path):
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    return context


def _build_path(tmp_path: Path, *purls: str) -> Path:
    build_path = tmp_path / "build"
    build_path.mkdir()
    components = [{"type": "container", "name": f"svc{n}", "purl": purl} for n, purl in enumerate(purls)]
    (build_path / "sbom.json").write_text(json.dumps({"components": components}), encoding="utf-8")
    return build_path


class _FakeGate(ReleaseGateIntegration, ImageFindingsIntegration, CredentialedIntegration):
    TYPE = "fake-gate"
    CAPABILITIES = frozenset({"release_gate", "image_findings"})
    TRANSPORTS = frozenset({"http"})

    def __init__(self, config, state):
        super().__init__(config)
        self.state = state

    def bind_credentials(self, values):
        self.state.bound.append(dict(values))

    def run_release_gate(self, request, *, on_poll=None):
        self.state.requests.append(request)
        return self.state.outcome

    def find_image_issues(self, image, severities):
        self.state.asked.append((image.repository, severities))
        if isinstance(self.state.lookup, Exception):
            raise self.state.lookup
        return self.state.lookup


@pytest.fixture
def gate(monkeypatch):
    state = SimpleNamespace(
        outcome=ReleaseGateOutcome(passed=True, reference="22"),
        lookup=ImageLookup(status="found", platform_name="app"),
        requests=[],
        asked=[],
        bound=[],
    )
    monkeypatch.setattr(gates_module, "get_integration", lambda integration_type, config=None: _FakeGate(config, state))
    return state


class _FakeScanner:
    """Stands in for the PATH Trivy/Grype; returns `findings`, whatever the threshold."""

    def __init__(self) -> None:
        self.available = True
        self.findings: list[CveFindingModel] = []
        self.thresholds: list[str] = []

    def is_available(self) -> bool:
        return self.available

    def ensure_available(self) -> None:
        if not self.available:
            raise IntegrationError("trivy: 'trivy' is not available on PATH.")

    def scan_sbom(self, sbom_path: Path, *, severity_threshold: str) -> CveAuditResultModel:
        self.thresholds.append(severity_threshold)
        counts = {level.lower(): sum(f.severity == level for f in self.findings) for level in ("CRITICAL", "HIGH")}
        return CveAuditResultModel(
            scanner="trivy",
            scanner_version="0.50.0",
            sbom_path=str(sbom_path),
            total_findings=len(self.findings),
            findings=list(self.findings),
            **counts,
        )


@pytest.fixture
def scanner(monkeypatch):
    fake = _FakeScanner()
    monkeypatch.setattr(sbom_module, "CveScannerIntegration", lambda: fake)
    return fake


class _FakeCheckov:
    """Stands in for the PATH checkov; `error` makes every scan fail."""

    def __init__(self) -> None:
        self.severities: list[str] = []
        self.error: IntegrationError | None = None
        self.scanned: list[Path] = []

    def scan(self, directory: Path, **kwargs) -> CheckovScanResultModel:
        self.scanned.append(directory)
        if self.error is not None:
            raise self.error
        findings = [
            CheckovFindingModel(
                check_id=f"CKV_{n}",
                check_name="check",
                resource=f"aws_s3_bucket.b{n}",
                file_path="/main.tf",
                file_line_range=[n + 1, n + 2],
                severity=severity,
            )
            for n, severity in enumerate(self.severities)
        ]
        return CheckovScanResultModel(
            failed=len(findings),
            findings=findings,
            scanner_version="3.3.16",
            framework="terraform",
            scanned_path=str(directory),
        )


@pytest.fixture
def checkov(monkeypatch):
    fake = _FakeCheckov()
    monkeypatch.setattr(checkov_module, "CheckovIntegration", lambda *args: fake)
    return fake


def _rendered(tmp_path: Path) -> Path:
    """A build path holding `tf_main`'s rendered source, as `build run` leaves it."""
    build_path = _build_path(tmp_path)
    (build_path / "infra").mkdir()
    return build_path


def _cve(vulnerability_id: str, severity: str = "CRITICAL", package: str = "openssl") -> CveFindingModel:
    return CveFindingModel(
        vulnerability_id=vulnerability_id,
        severity=severity,
        package_name=package,
        installed_version="1.0",
        fixed_version="1.1",
    )


def test_the_table_has_a_row_for_every_workspace_policy_field():
    fields = [name for name in WorkspaceSpecModel.model_fields if name.endswith("_policy")]
    assert sorted(row.name for row in policy_controller._POLICIES) == sorted(fields)


def test_every_policy_is_checkable_and_all_but_the_release_gate_have_findings():
    rows = {row.name: row for row in policy_controller._POLICIES}
    assert all(row.check is not None for row in rows.values())
    assert {name for name, row in rows.items() if row.findings is None} == {"release_gate_policy"}


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_shows_every_configured_policy_without_results(tmp_path, gate):
    infos = list_policies(_context(_solution(tmp_path, cve=True)), "app", tmp_path / "missing")
    assert [(i.name, i.phase, i.enforcement, i.backing, i.checkable) for i in infos] == [
        ("cve_policy", "build", "deny", "trivy/grype (PATH)", True),
        ("release_gate_policy", "deploy", "deny", "fake-gate 'gate-ci'", True),
        ("image_gate_policy", "deploy", "deny", "fake-gate 'gate-api'", True),
    ]
    assert all(info.last_result is None for info in infos)
    assert gate.requests == [] and gate.asked == []


def test_list_names_the_declared_cve_scanner(tmp_path, gate):
    root = _solution(tmp_path, cve=True)
    _write(root, "grype.yaml", "integration", "grype-ci", "  type: grype\n")
    infos = list_policies(_context(root), "app", tmp_path / "missing")
    assert {i.name: i.backing for i in infos}["cve_policy"] == "grype 'grype-ci'"


def test_list_names_checkov_declared_or_on_path(tmp_path, gate):
    plain = list_policies(_context(_solution(tmp_path / "a", checkov=True)), "app", tmp_path)
    root = _solution(tmp_path / "b", checkov=True)
    _write(root, "checkov.yaml", "integration", "ckv", "  type: checkov\n")
    declared = list_policies(_context(root), "app", tmp_path)
    assert {i.name: (i.backing, i.checkable) for i in plain}["checkov_policy"] == ("checkov (PATH)", True)
    assert {i.name: i.backing for i in declared}["checkov_policy"] == "checkov 'ckv'"


def test_list_shows_the_last_recorded_result(tmp_path, gate):
    build_path = _build_path(tmp_path)
    recorded = ManifestPolicyResultModel(
        policy_name="cve_policy", policy_type="trivy", phase="build", enforcement="deny", passed=False, violations=["x"]
    )
    write_policy_results(build_path, [recorded])
    infos = list_policies(_context(_solution(tmp_path, cve=True)), "app", build_path)
    assert {i.name: i.last_result for i in infos}["cve_policy"] == recorded


def test_list_an_unknown_deployment_is_a_usage_error(tmp_path, gate):
    with pytest.raises(UsageError, match="No deployment named 'nope'"):
        list_policies(_context(_solution(tmp_path)), "nope", tmp_path)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def test_check_runs_every_checkable_policy_and_records_the_results(tmp_path, gate, scanner):
    build_path = _build_path(tmp_path, "pkg:docker/org/app@1.0")
    steps: list[str] = []
    diagnostics, checked = check_policies(
        _context(_solution(tmp_path, cve=True)), "app", build_path, on_step=steps.append
    )
    assert diagnostics.ok, diagnostics.messages()
    assert checked == ["cve_policy", "release_gate_policy", "image_gate_policy"]
    assert len(gate.requests) == 1 and gate.asked and scanner.thresholds == ["MEDIUM"]
    assert [r.policy_name for r in read_policy_results(build_path) or []] == checked
    assert any("release gate" in step for step in steps)


def test_check_without_a_build_path_records_nothing(tmp_path, gate):
    diagnostics, _ = check_policies(
        _context(_solution(tmp_path, image=False)), "app", tmp_path / "absent", policies=["release_gate_policy"]
    )
    assert diagnostics.ok
    assert not (tmp_path / "absent").exists()


def test_check_policy_filter_makes_no_release_call(tmp_path, gate):
    build_path = _build_path(tmp_path, "pkg:docker/org/app@1.0")
    _, checked = check_policies(_context(_solution(tmp_path)), "app", build_path, policies=["image_gate_policy"])
    assert checked == ["image_gate_policy"]
    assert gate.requests == []


def test_check_commit_override_is_the_commit_judged(tmp_path, gate):
    override = "c" * 40
    check_policies(_context(_solution(tmp_path, image=False)), "app", tmp_path, commit_override=override)
    assert gate.requests[0].commit == override


def test_check_a_deny_breach_is_an_error_and_a_warn_breach_a_warning(tmp_path, gate):
    gate.outcome = ReleaseGateOutcome(passed=False, open_issues=1)
    denied, _ = check_policies(_context(_solution(tmp_path / "a", image=False)), "app", tmp_path)
    warned, _ = check_policies(_context(_solution(tmp_path / "b", image=False, enforcement="warn")), "app", tmp_path)
    assert [d.code for d in denied.errors] == ["release_gate_failed"]
    assert warned.ok and [d.code for d in warned.warnings] == ["release_gate_failed"]


@pytest.mark.parametrize(
    ("policies", "match"),
    [
        (["nonsense_policy"], "Unknown policy 'nonsense_policy'"),
        (["image_gate_policy"], "does not configure image_gate_policy"),
    ],
)
def test_check_an_unknown_or_unconfigured_policy_is_a_usage_error(tmp_path, gate, policies, match):
    with pytest.raises(UsageError, match=match):
        check_policies(
            _context(_solution(tmp_path, image=False, cve=True, checkov=True)), "app", tmp_path, policies=policies
        )
    assert gate.requests == []


def test_check_with_nothing_configured_is_a_usage_error(tmp_path, gate):
    with pytest.raises(UsageError, match="Nothing to check"):
        check_policies(_context(_solution(tmp_path, release=False, image=False)), "app", tmp_path)


# ---------------------------------------------------------------------------
# check: cve_policy (plan Phase 8)
# ---------------------------------------------------------------------------


def _check_cve(tmp_path: Path, **solution) -> tuple:
    root = _solution(tmp_path / "s", release=False, image=False, cve=True, **solution)
    build_path = _build_path(tmp_path)
    diagnostics, checked = check_policies(_context(root), "app", build_path)
    return diagnostics, checked, read_policy_results(build_path) or []


def test_check_cve_passes_and_records_a_build_phase_result(tmp_path, gate, scanner):
    scanner.findings = [_cve("CVE-2026-9", severity="MEDIUM")]
    diagnostics, checked, [result] = _check_cve(tmp_path)
    assert diagnostics.ok and checked == ["cve_policy"]
    assert (result.policy_name, result.policy_type, result.phase, result.passed) == (
        "cve_policy",
        "cve_max_severity",
        "build",
        True,
    )


@pytest.mark.parametrize(("enforcement", "error"), [("deny", True), ("warn", False)])
def test_check_cve_breach_follows_enforcement(tmp_path, gate, scanner, enforcement, error):
    scanner.findings = [_cve("CVE-2026-1")]
    diagnostics, _, [result] = _check_cve(tmp_path, enforcement=enforcement)
    codes = [d.code for d in (diagnostics.errors if error else diagnostics.warnings)]
    assert codes == ["cve_policy_violation"] and diagnostics.ok is not error
    assert result.passed is not error and "exceed max_count" in result.violations[0]


@pytest.mark.parametrize(("on_missing_data", "errors", "warnings"), [("skip", 0, 0), ("warn", 0, 1), ("block", 1, 0)])
def test_check_cve_without_a_scanner_follows_on_missing_data(
    tmp_path, gate, scanner, on_missing_data, errors, warnings
):
    scanner.available = False
    diagnostics, _, _ = _check_cve(tmp_path, on_missing_data=on_missing_data)
    assert (len(diagnostics.errors), len(diagnostics.warnings)) == (errors, warnings)
    assert scanner.thresholds == []


def test_check_cve_drops_allowlisted_findings(tmp_path, gate, scanner):
    scanner.findings = [_cve("CVE-2026-1")]
    diagnostics, _, [result] = _check_cve(tmp_path, allowed=_ALLOWED)
    assert diagnostics.ok and result.passed


def test_check_cve_replaces_the_result_build_run_recorded(tmp_path, gate, scanner):
    build_path = _build_path(tmp_path)
    old = ManifestPolicyResultModel(
        policy_name="cve_policy", policy_type="cve_max_severity", phase="build", enforcement="deny", passed=False
    )
    write_policy_results(build_path, [old])
    check_policies(_context(_solution(tmp_path / "s", release=False, image=False, cve=True)), "app", build_path)
    [result] = read_policy_results(build_path) or []
    assert result.passed is True


def test_check_cve_reaches_the_same_result_as_build_run(tmp_path, gate, scanner):
    """Phase 8's "done when": the same SBOM gives the same passed/violations either way."""
    scanner.findings = [_cve("CVE-2026-1"), _cve("CVE-2026-2", severity="HIGH")]
    context = _context(_solution(tmp_path / "s", release=False, image=False, cve=True))
    build_path = _build_path(tmp_path)
    _, workspace = resolve_deployment_workspace(context, "app")
    as_build_run = cve_policy_result(workspace, evaluate_cve_policy(build_path, workspace, context.controller.index))
    check_policies(context, "app", build_path)
    [as_check] = read_policy_results(build_path) or []
    assert as_build_run is not None
    assert (as_check.passed, as_check.violations, as_check.phase) == (
        as_build_run.passed,
        as_build_run.violations,
        as_build_run.phase,
    )


# ---------------------------------------------------------------------------
# findings
# ---------------------------------------------------------------------------

_ALLOWED = "  cve_allowed:\n    - id: CVE-2026-1\n      reason: accepted\n"


def test_findings_marks_allowlisted_issues_instead_of_hiding_them(tmp_path, gate):
    gate.lookup = ImageLookup(
        status="found",
        platform_name="app",
        issues=(
            SecurityIssue(id="1", severity="CRITICAL", status="open", cve_id="CVE-2026-1", package="openssl"),
            SecurityIssue(id="2", severity="CRITICAL", status="open", cve_id="CVE-2026-2", package="zlib"),
        ),
    )
    build_path = _build_path(tmp_path, "pkg:docker/org/app@1.0")
    report = policy_findings(_context(_solution(tmp_path, allowed=_ALLOWED)), "app", build_path, "image_gate_policy")
    [found] = report.images
    assert [issue.id for issue in found.issues] == ["1", "2"]
    assert found.allowlisted == frozenset({"1"})
    assert report.severities == ("CRITICAL",)
    assert report.integration == "fake-gate 'gate-api'"


def test_findings_with_integration_works_without_the_policy_and_defaults_to_high(tmp_path, gate):
    build_path = _build_path(tmp_path, "pkg:docker/org/app@1.0")
    report = policy_findings(
        _context(_solution(tmp_path, image=False)), "app", build_path, "image_gate_policy", integration="gate-api"
    )
    assert report.severities == ("CRITICAL", "HIGH")
    assert gate.asked == [("org/app", frozenset({"CRITICAL", "HIGH"}))]


def test_findings_severity_option_wins(tmp_path, gate):
    build_path = _build_path(tmp_path, "pkg:docker/org/app@1.0")
    report = policy_findings(_context(_solution(tmp_path)), "app", build_path, "image_gate_policy", severity="MEDIUM")
    assert report.severities == ("CRITICAL", "HIGH", "MEDIUM")


@pytest.mark.parametrize(
    ("policy", "kwargs", "match"),
    [
        ("release_gate_policy", {}, "does not support release_gate_policy"),
        ("nonsense", {}, "Unknown policy"),
        ("image_gate_policy", {"integration": "gate-zzz"}, "is not declared"),
    ],
)
def test_findings_usage_errors(tmp_path, gate, policy, kwargs, match):
    build_path = _build_path(tmp_path, "pkg:docker/org/app@1.0")
    with pytest.raises(UsageError, match=match):
        policy_findings(_context(_solution(tmp_path)), "app", build_path, policy, **kwargs)


def test_findings_without_a_policy_or_integration_is_a_usage_error(tmp_path, gate):
    with pytest.raises(UsageError, match="pass --integration"):
        policy_findings(_context(_solution(tmp_path, image=False)), "app", tmp_path, "image_gate_policy")


def test_findings_without_an_sbom_is_a_usage_error(tmp_path, gate):
    with pytest.raises(UsageError, match="No sbom.json"):
        policy_findings(_context(_solution(tmp_path)), "app", tmp_path / "absent", "image_gate_policy")


def test_findings_platform_failure_is_a_system_error(tmp_path, gate):
    gate.lookup = IntegrationError("gate-api: unreachable")
    build_path = _build_path(tmp_path, "pkg:docker/org/app@1.0")
    with pytest.raises(SystemError, match="unreachable"):
        policy_findings(_context(_solution(tmp_path)), "app", build_path, "image_gate_policy")


def test_findings_unresolved_credentials_are_a_validation_error(tmp_path, gate, monkeypatch):
    monkeypatch.delenv("TEST_POLICY_API_KEY", raising=False)
    build_path = _build_path(tmp_path, "pkg:docker/org/app@1.0")
    with pytest.raises(ValidationError, match="API_KEY"):
        policy_findings(_context(_solution(tmp_path, image_auth=True)), "app", build_path, "image_gate_policy")


# ---------------------------------------------------------------------------
# findings: cve_policy (plan Phase 8)
# ---------------------------------------------------------------------------


def test_cve_findings_marks_allowlisted_findings_and_uses_the_severity_threshold(tmp_path, gate, scanner):
    scanner.findings = [_cve("CVE-2026-1"), _cve("CVE-2026-2", package="zlib")]
    root = _solution(tmp_path / "s", cve=True, allowed=_ALLOWED)
    report = policy_findings(_context(root), "app", _build_path(tmp_path), "cve_policy")
    assert isinstance(report, CveFindingsReport)
    assert [(f.finding.vulnerability_id, f.allowlisted) for f in report.findings] == [
        ("CVE-2026-1", True),
        ("CVE-2026-2", False),
    ]
    assert scanner.thresholds == ["MEDIUM"]
    assert report.severities == ("CRITICAL", "HIGH", "MEDIUM")


def test_cve_findings_severity_option_wins_and_no_policy_defaults_to_high(tmp_path, gate, scanner):
    build_path = _build_path(tmp_path)
    policy_findings(_context(_solution(tmp_path / "a", cve=True)), "app", build_path, "cve_policy", severity="LOW")
    policy_findings(_context(_solution(tmp_path / "b")), "app", build_path, "cve_policy")
    assert scanner.thresholds == ["LOW", "HIGH"]


def test_cve_findings_with_integration_is_a_usage_error(tmp_path, gate, scanner):
    with pytest.raises(UsageError, match="--integration only applies to image_gate_policy"):
        policy_findings(
            _context(_solution(tmp_path / "s", cve=True)), "app", _build_path(tmp_path), "cve_policy", integration="x"
        )


def test_cve_findings_without_an_sbom_is_a_usage_error(tmp_path, gate, scanner):
    with pytest.raises(UsageError, match="No sbom.json"):
        policy_findings(_context(_solution(tmp_path, cve=True)), "app", tmp_path / "absent", "cve_policy")


def test_cve_findings_without_a_scanner_is_a_system_error(tmp_path, gate, scanner):
    scanner.available = False
    with pytest.raises(SystemError, match="not available on PATH"):
        policy_findings(_context(_solution(tmp_path / "s", cve=True)), "app", _build_path(tmp_path), "cve_policy")


# ---------------------------------------------------------------------------
# checkov_policy (plan Phase 9)
# ---------------------------------------------------------------------------


def _check_checkov(tmp_path: Path, build_path: Path, **solution) -> tuple:
    root = _solution(tmp_path / "s", release=False, image=False, checkov=True, **solution)
    diagnostics, checked = check_policies(_context(root), "app", build_path)
    return diagnostics, checked, read_policy_results(build_path) or []


def test_check_checkov_scans_the_rendered_source_and_records_a_build_phase_result(tmp_path, gate, checkov):
    build_path = _rendered(tmp_path)
    checkov.severities = ["MEDIUM"]
    diagnostics, checked, [result] = _check_checkov(tmp_path, build_path)
    assert diagnostics.ok and checked == ["checkov_policy"]
    assert checkov.scanned == [build_path / "infra"]
    assert (result.policy_name, result.policy_type, result.phase, result.passed) == (
        "checkov_policy",
        "checkov",
        "build",
        True,
    )


@pytest.mark.parametrize(("enforcement", "error"), [("deny", True), ("warn", False)])
def test_check_checkov_breach_follows_enforcement(tmp_path, gate, checkov, enforcement, error):
    checkov.severities = ["CRITICAL"]
    diagnostics, _, [result] = _check_checkov(tmp_path, _rendered(tmp_path), enforcement=enforcement)
    codes = [d.code for d in (diagnostics.errors if error else diagnostics.warnings)]
    assert codes == ["checkov_policy_violation"]
    assert result.passed is not error and "provisioner 'tf_main' has 1 finding(s)" in result.violations[0]


@pytest.mark.parametrize(("on_missing_data", "errors", "warnings"), [("skip", 0, 0), ("warn", 0, 1), ("block", 1, 0)])
def test_check_checkov_without_the_rendered_source_follows_on_missing_data(
    tmp_path, gate, checkov, on_missing_data, errors, warnings
):
    diagnostics, _, _ = _check_checkov(tmp_path, _build_path(tmp_path), on_missing_data=on_missing_data)
    assert (len(diagnostics.errors), len(diagnostics.warnings)) == (errors, warnings)
    assert checkov.scanned == []


def test_check_checkov_scan_failure_follows_on_missing_data(tmp_path, gate, checkov):
    checkov.error = IntegrationError("checkov not found on PATH")
    diagnostics, _, _ = _check_checkov(tmp_path, _rendered(tmp_path), on_missing_data="block")
    assert [d.code for d in diagnostics.errors] == ["checkov_policy_missing_data"]


def test_check_includes_checkov_by_default(tmp_path, gate, scanner, checkov):
    _, checked = check_policies(_context(_solution(tmp_path / "s", cve=True, checkov=True)), "app", _rendered(tmp_path))
    assert checked == ["cve_policy", "checkov_policy", "release_gate_policy", "image_gate_policy"]


def test_check_checkov_reaches_the_same_result_as_build_run(tmp_path, gate, checkov):
    """Phase 9's "done when": the evaluator `build run` calls, with the paths `build run` hands it."""
    checkov.severities = ["CRITICAL", "HIGH", "UNKNOWN"]
    context = _context(_solution(tmp_path / "s", release=False, image=False, checkov=True))
    build_path = _rendered(tmp_path)
    _, workspace = resolve_deployment_workspace(context, "app")
    as_build_run = checkov_policy_result(
        workspace,
        evaluate_checkov_policy(
            build_path, workspace, {"tf_main": build_path / "infra"}, namespaces={}, index=context.controller.index
        ),
    )
    check_policies(context, "app", build_path)
    [as_check] = read_policy_results(build_path) or []
    assert as_build_run is not None
    assert (as_check.passed, as_check.violations) == (as_build_run.passed, as_build_run.violations)


def test_checkov_findings_lists_rated_at_threshold_and_every_unrated(tmp_path, gate, checkov):
    checkov.severities = ["CRITICAL", "MEDIUM", "UNKNOWN"]
    root = _solution(tmp_path / "s", checkov=True)
    report = policy_findings(_context(root), "app", _rendered(tmp_path), "checkov_policy")
    assert isinstance(report, CheckovFindingsReport)
    assert report.severities == ("CRITICAL", "HIGH") and report.scanner_version == "3.3.16"
    [target] = report.targets
    assert target.label == "provisioner 'tf_main'"
    assert [(f.finding.severity, f.unrated) for f in target.findings] == [("CRITICAL", False), ("UNKNOWN", True)]


def test_checkov_findings_severity_option_lowers_the_threshold(tmp_path, gate, checkov):
    checkov.severities = ["MEDIUM"]
    root = _solution(tmp_path / "s", checkov=True)
    report = policy_findings(_context(root), "app", _rendered(tmp_path), "checkov_policy", severity="MEDIUM")
    assert isinstance(report, CheckovFindingsReport)
    assert [f.finding.severity for f in report.targets[0].findings] == ["MEDIUM"]


def test_checkov_findings_shows_an_unrendered_target_instead_of_failing(tmp_path, gate, checkov):
    report = policy_findings(
        _context(_solution(tmp_path / "s", checkov=True)), "app", _build_path(tmp_path), "checkov_policy"
    )
    assert isinstance(report, CheckovFindingsReport)
    [target] = report.targets
    assert (target.path, target.missing, report.scanner_version) == (None, "not materialised during this build", None)


def test_checkov_findings_without_build_output_is_a_usage_error(tmp_path, gate, checkov):
    with pytest.raises(UsageError, match="run 'strata build run' first"):
        policy_findings(_context(_solution(tmp_path, checkov=True)), "app", tmp_path / "absent", "checkov_policy")


def test_checkov_findings_scan_failure_is_a_system_error(tmp_path, gate, checkov):
    checkov.error = IntegrationError("checkov not found on PATH")
    with pytest.raises(SystemError, match="provisioner 'tf_main': checkov not found"):
        policy_findings(_context(_solution(tmp_path / "s", checkov=True)), "app", _rendered(tmp_path), "checkov_policy")


def test_checkov_findings_with_integration_is_a_usage_error(tmp_path, gate, checkov):
    with pytest.raises(UsageError, match="--integration only applies to image_gate_policy"):
        policy_findings(
            _context(_solution(tmp_path / "s", checkov=True)),
            "app",
            _rendered(tmp_path),
            "checkov_policy",
            integration="x",
        )
    assert gate.asked == []


def test_findings_resolves_credentials_through_the_deployment(tmp_path, gate, monkeypatch):
    monkeypatch.setenv("TEST_POLICY_API_KEY", "secret-value-123456")
    build_path = _build_path(tmp_path, "pkg:docker/org/app@1.0")
    policy_findings(_context(_solution(tmp_path, image_auth=True)), "app", build_path, "image_gate_policy")
    assert gate.bound == [{"API_KEY": "secret-value-123456"}]
