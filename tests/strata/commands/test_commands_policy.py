#!/usr/bin/env python3
"""Tests for `strata policy list | check | findings` (docs/work/security-gates-design.md D7; plan Phase 6).

Exit codes and output shape through the real CLI; the gate integration is a vendor-neutral fake.
"""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from strata.commands.cli import cli
from strata.commands.exit_codes import EXIT_FAILURE, EXIT_SUCCESS, EXIT_USAGE, EXIT_VALIDATION
from strata.controllers import checkov_controller as checkov_module
from strata.controllers import sbom_controller as sbom_module
from strata.controllers import security_gate_controller as gates_module
from strata.controllers.policy_results import read_policy_results
from strata.integrations.capabilities import ImageFindingsIntegration, ReleaseGateIntegration
from strata.integrations.errors import IntegrationError
from strata.integrations.security_findings import ImageLookup, ReleaseGateOutcome, SecurityIssue
from strata.models.checkov_model import CheckovFindingModel, CheckovScanResultModel
from strata.models.sbom_model import CveAuditResultModel, CveFindingModel

COMMIT = "d" * 40
_HEADER = "apiVersion: strata.huybrechts.xyz/v2\nkind: {kind}\nmeta:\n  name: {name}\nspec:\n"


def _write(root: Path, relative: str, kind: str, name: str, spec: str) -> None:
    (root / relative).write_text(_HEADER.format(kind=kind, name=name) + spec, encoding="utf-8")


def _solution(tmp_path: Path, *, policies: str | None = None, enforcement: str = "deny") -> Path:
    root = tmp_path / "sln"
    (root / "infra").mkdir(parents=True)
    (root / "infra" / "main.tf").write_text("# root module\n", encoding="utf-8")
    (root / "strata.yaml").write_text(_HEADER.format(kind="solution", name="s") + "  {}\n", encoding="utf-8")
    _write(root, "provider.yaml", "provider", "p1", "  type: local\n  region: local\n")
    _write(
        root,
        "resource.yaml",
        "resource",
        "r1",
        "  provider_type: local\n  resource_type: server\n  category: compute\n  default_tags:\n    managed-by: strata\n",
    )
    _write(root, "gate-ci.yaml", "integration", "gate-ci", "  type: fake-gate\n  capabilities: [release_gate]\n")
    _write(root, "gate-api.yaml", "integration", "gate-api", "  type: fake-gate\n  capabilities: [image_findings]\n")
    if policies is None:
        policies = (
            "  release_gate_policy:\n    integration: gate-ci\n    repository: org/infra\n"
            f"    enforcement: {enforcement}\n    commit: {COMMIT}\n"
            f"  image_gate_policy:\n    integration: gate-api\n    enforcement: {enforcement}\n"
        )
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
        "  variables:\n    - key: K\n      store: environment\n      value: K\n",
    )
    _write(root, "deployment.yaml", "deployment", "app", "  workspace: main\n  environments:\n    - prd\n")
    build_path = root / "build" / "app"
    build_path.mkdir(parents=True)
    components = [{"type": "container", "name": "web", "purl": "pkg:docker/org/app@1.0"}]
    (build_path / "sbom.json").write_text(json.dumps({"components": components}), encoding="utf-8")
    return root


class _FakeGate(ReleaseGateIntegration, ImageFindingsIntegration):
    TYPE = "fake-gate"
    CAPABILITIES = frozenset({"release_gate", "image_findings"})
    TRANSPORTS = frozenset({"http"})

    def __init__(self, config, state):
        super().__init__(config)
        self.state = state

    def run_release_gate(self, request, *, on_poll=None):
        self.state.requests.append(request)
        return self.state.outcome

    def find_image_issues(self, image, severities):
        self.state.asked.append(image.repository)
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
    )
    monkeypatch.setattr(gates_module, "get_integration", lambda integration_type, config=None: _FakeGate(config, state))
    monkeypatch.delenv("STRATA_OUTPUT", raising=False)
    return state


def _invoke(*args: str, env=None):
    return CliRunner().invoke(cli, ["policy", *args], env=env)


@pytest.mark.parametrize("sub", [[], ["list"], ["check"], ["findings"]])
def test_help(sub):
    result = _invoke(*sub, "--help")
    assert result.exit_code == EXIT_SUCCESS, result.output


def test_list_console_and_json(tmp_path, gate):
    root = _solution(tmp_path)
    console = _invoke("list", "app", "--path", str(root))
    assert console.exit_code == EXIT_SUCCESS, console.output
    assert "release_gate_policy" in console.output and "fake-gate 'gate-ci'" in console.output

    result = _invoke("list", "app", "--path", str(root), env={"STRATA_OUTPUT": "json"})
    assert result.exit_code == EXIT_SUCCESS, result.output
    policies = json.loads(result.output)["data"]["policies"]
    assert [p["name"] for p in policies] == ["release_gate_policy", "image_gate_policy"]
    assert policies[0]["check"] is True and policies[0]["last_result"] is None
    assert gate.requests == []


def test_check_pass_is_zero_and_records_results(tmp_path, gate):
    root = _solution(tmp_path)
    result = _invoke("check", "app", "--path", str(root), "--output", "json")
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert json.loads(result.output)["data"]["checked"] == ["release_gate_policy", "image_gate_policy"]
    recorded = read_policy_results(root / "build" / "app") or []
    assert [r.policy_name for r in recorded] == ["release_gate_policy", "image_gate_policy"]


def test_check_warn_breach_is_zero(tmp_path, gate):
    gate.outcome = ReleaseGateOutcome(passed=False, open_issues=3)
    result = _invoke("check", "app", "--path", str(_solution(tmp_path, enforcement="warn")))
    assert result.exit_code == EXIT_SUCCESS, result.output


def test_check_deny_breach_is_three(tmp_path, gate):
    gate.outcome = ReleaseGateOutcome(passed=False, open_issues=3)
    result = _invoke("check", "app", "--path", str(_solution(tmp_path)))
    assert result.exit_code == EXIT_VALIDATION, result.output
    assert "release_gate_policy: FAILED" in result.output


def test_check_nothing_to_check_is_two(tmp_path, gate):
    result = _invoke("check", "app", "--path", str(_solution(tmp_path, policies="")))
    assert result.exit_code == EXIT_USAGE, result.output


def test_check_unconfigured_policy_is_two(tmp_path, gate):
    result = _invoke("check", "app", "--path", str(_solution(tmp_path)), "--policy", "checkov_policy")
    assert result.exit_code == EXIT_USAGE, result.output
    assert "does not configure checkov_policy" in result.output


def test_check_image_policy_only_makes_no_release_call(tmp_path, gate):
    result = _invoke("check", "app", "--path", str(_solution(tmp_path)), "--policy", "image_gate_policy")
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert gate.requests == [] and gate.asked == ["org/app"]


def test_check_commit_overrides_the_environment(tmp_path, gate, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "e" * 40)
    override = "f" * 40
    policies = "  release_gate_policy:\n    integration: gate-ci\n    repository: org/infra\n"
    result = _invoke("check", "app", "--path", str(_solution(tmp_path, policies=policies)), "--commit", override)
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert gate.requests[0].commit == override


def test_findings_json_shape(tmp_path, gate):
    gate.lookup = ImageLookup(
        status="found",
        platform_name="app",
        issues=(SecurityIssue(id="7", severity="HIGH", status="open", cve_id="CVE-2026-7", package="zlib"),),
    )
    root = _solution(tmp_path)
    result = _invoke("findings", "app", "--path", str(root), "--policy", "image_gate_policy", "-o", "json")
    assert result.exit_code == EXIT_SUCCESS, result.output
    data = json.loads(result.output)["data"]
    assert set(data) == {"policy", "integration", "severities", "images"}
    [image] = data["images"]
    assert image["image"] == "org/app:1.0" and image["counts"]["HIGH"] == 1
    assert image["issues"][0] == {
        "id": "7",
        "severity": "HIGH",
        "status": "open",
        "cve_id": "CVE-2026-7",
        "package": "zlib",
        "link": None,
        "allowlisted": False,
    }


def test_findings_console_lists_issues(tmp_path, gate):
    gate.lookup = ImageLookup(
        status="found",
        platform_name="app",
        issues=(SecurityIssue(id="7", severity="CRITICAL", status="open", cve_id="CVE-2026-7", package="zlib"),),
    )
    result = _invoke("findings", "app", "--path", str(_solution(tmp_path)), "--policy", "image_gate_policy")
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert "CVE-2026-7 in zlib" in result.output and "1 CRITICAL" in result.output


def test_findings_unreachable_is_one(tmp_path, gate):
    gate.lookup = IntegrationError("gate-api: unreachable")
    result = _invoke("findings", "app", "--path", str(_solution(tmp_path)), "--policy", "image_gate_policy")
    assert result.exit_code == EXIT_FAILURE, result.output


def test_findings_unsupported_policy_is_two(tmp_path, gate):
    result = _invoke("findings", "app", "--path", str(_solution(tmp_path)), "--policy", "release_gate_policy")
    assert result.exit_code == EXIT_USAGE, result.output


# ---------------------------------------------------------------------------
# cve_policy (plan Phase 8)
# ---------------------------------------------------------------------------

_CVE_POLICY = "  cve_policy:\n    max_severity: HIGH\n    enforcement: {enforcement}\n    on_missing_data: block\n"


class _FakeScanner:
    def __init__(self) -> None:
        self.available = True
        self.findings: list[CveFindingModel] = []

    def is_available(self) -> bool:
        return self.available

    def ensure_available(self) -> None:
        if not self.available:
            raise IntegrationError("trivy: 'trivy' is not available on PATH.")

    def scan_sbom(self, sbom_path: Path, *, severity_threshold: str) -> CveAuditResultModel:
        return CveAuditResultModel(
            scanner="trivy",
            scanner_version="0.50.0",
            sbom_path=str(sbom_path),
            total_findings=len(self.findings),
            critical=sum(f.severity == "CRITICAL" for f in self.findings),
            findings=list(self.findings),
        )


@pytest.fixture
def scanner(monkeypatch):
    fake = _FakeScanner()
    monkeypatch.setattr(sbom_module, "CveScannerIntegration", lambda: fake)
    return fake


def _critical(vulnerability_id: str) -> CveFindingModel:
    return CveFindingModel(
        vulnerability_id=vulnerability_id,
        severity="CRITICAL",
        package_name="openssl",
        installed_version="1.0",
        fixed_version="1.1",
    )


@pytest.mark.parametrize(
    ("enforcement", "findings", "exit_code"),
    [("deny", 0, EXIT_SUCCESS), ("deny", 1, EXIT_VALIDATION), ("warn", 1, EXIT_SUCCESS)],
)
def test_check_cve_exit_codes(tmp_path, gate, scanner, enforcement, findings, exit_code):
    scanner.findings = [_critical(f"CVE-2026-{n}") for n in range(findings)]
    root = _solution(tmp_path, policies=_CVE_POLICY.format(enforcement=enforcement))
    result = _invoke("check", "app", "--path", str(root))
    assert result.exit_code == exit_code, result.output
    [recorded] = read_policy_results(root / "build" / "app") or []
    assert (recorded.policy_name, recorded.phase) == ("cve_policy", "build")


def test_findings_cve_console_marks_allowlisted(tmp_path, gate, scanner):
    scanner.findings = [_critical("CVE-2026-1"), _critical("CVE-2026-2")]
    root = _solution(tmp_path, policies=_CVE_POLICY.format(enforcement="deny"))
    _write(root, "configuration.yaml", "configuration", "c", "  cve_allowed:\n    - id: CVE-2026-1\n      reason: ok\n")
    result = _invoke("findings", "app", "--path", str(root), "--policy", "cve_policy")
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert "scanned with trivy 0.50.0, severities CRITICAL, HIGH" in result.output
    assert "2 CRITICAL" in result.output
    assert "CVE-2026-1 in openssl 1.0 (fixed in 1.1) [allowlisted]" in result.output
    assert "CVE-2026-2 in openssl 1.0 (fixed in 1.1)\n" in result.output


def test_findings_cve_json_shape(tmp_path, gate, scanner):
    scanner.findings = [_critical("CVE-2026-1")]
    root = _solution(tmp_path, policies=_CVE_POLICY.format(enforcement="deny"))
    result = _invoke("findings", "app", "--path", str(root), "--policy", "cve_policy", "-o", "json")
    assert result.exit_code == EXIT_SUCCESS, result.output
    data = json.loads(result.output)["data"]
    assert data["policy"] == "cve_policy" and data["scanner"] == "trivy" and data["scanner_version"] == "0.50.0"
    assert data["severities"] == ["CRITICAL", "HIGH"]
    assert data["counts"] == {"CRITICAL": 1, "HIGH": 0}
    [finding] = data["findings"]
    assert finding["vulnerability_id"] == "CVE-2026-1" and finding["allowlisted"] is False


def test_findings_cve_without_an_sbom_is_two(tmp_path, gate, scanner):
    root = _solution(tmp_path, policies=_CVE_POLICY.format(enforcement="deny"))
    (root / "build" / "app" / "sbom.json").unlink()
    result = _invoke("findings", "app", "--path", str(root), "--policy", "cve_policy")
    assert result.exit_code == EXIT_USAGE, result.output


def test_findings_cve_without_a_scanner_is_one(tmp_path, gate, scanner):
    scanner.available = False
    root = _solution(tmp_path, policies=_CVE_POLICY.format(enforcement="deny"))
    result = _invoke("findings", "app", "--path", str(root), "--policy", "cve_policy")
    assert result.exit_code == EXIT_FAILURE, result.output
    assert "not available on PATH" in result.output


# ---------------------------------------------------------------------------
# checkov_policy (plan Phase 9)
# ---------------------------------------------------------------------------

_CHECKOV_POLICY = "  checkov_policy:\n    enforcement: {enforcement}\n    on_missing_data: block\n"


class _FakeCheckov:
    def __init__(self) -> None:
        self.severities: list[str] = []
        self.error: IntegrationError | None = None

    def scan(self, directory: Path, **kwargs) -> CheckovScanResultModel:
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


def _checkov_solution(tmp_path: Path, enforcement: str = "deny") -> Path:
    root = _solution(tmp_path, policies=_CHECKOV_POLICY.format(enforcement=enforcement))
    (root / "build" / "app" / "infra").mkdir()
    return root


@pytest.mark.parametrize(
    ("enforcement", "severities", "exit_code"),
    [
        ("deny", [], EXIT_SUCCESS),
        ("deny", ["HIGH"], EXIT_VALIDATION),
        ("warn", ["HIGH"], EXIT_SUCCESS),
        ("deny", ["UNKNOWN"], EXIT_SUCCESS),
    ],
)
def test_check_checkov_exit_codes(tmp_path, gate, checkov, enforcement, severities, exit_code):
    checkov.severities = severities
    root = _checkov_solution(tmp_path, enforcement)
    result = _invoke("check", "app", "--path", str(root))
    assert result.exit_code == exit_code, result.output
    [recorded] = read_policy_results(root / "build" / "app") or []
    assert (recorded.policy_name, recorded.phase) == ("checkov_policy", "build")


def test_findings_checkov_console_marks_unrated(tmp_path, gate, checkov):
    checkov.severities = ["HIGH", "UNKNOWN"]
    result = _invoke("findings", "app", "--path", str(_checkov_solution(tmp_path)), "--policy", "checkov_policy")
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert "terraform, scanned with checkov 3.3.16, severities CRITICAL, HIGH" in result.output
    assert "provisioner 'tf_main': 1 finding(s), 1 unrated" in result.output
    assert "HIGH     CKV_0 aws_s3_bucket.b0 at /main.tf:1\n" in result.output
    assert "CKV_1 aws_s3_bucket.b1 at /main.tf:2 [unrated, never counted]" in result.output


def test_findings_checkov_json_shape(tmp_path, gate, checkov):
    checkov.severities = ["UNKNOWN"]
    root = _checkov_solution(tmp_path)
    result = _invoke("findings", "app", "--path", str(root), "--policy", "checkov_policy", "-o", "json")
    assert result.exit_code == EXIT_SUCCESS, result.output
    data = json.loads(result.output)["data"]
    assert set(data) == {"policy", "scanner_version", "framework", "severities", "nothing", "targets"}
    [target] = data["targets"]
    assert target["target"] == "provisioner 'tf_main'" and target["missing"] is None
    assert target["findings"][0]["check_id"] == "CKV_0" and target["findings"][0]["unrated"] is True


def test_findings_checkov_unrendered_target_is_listed(tmp_path, gate, checkov):
    root = _solution(tmp_path, policies=_CHECKOV_POLICY.format(enforcement="deny"))
    result = _invoke("findings", "app", "--path", str(root), "--policy", "checkov_policy")
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert "provisioner 'tf_main': not scanned (not materialised during this build)" in result.output


def test_findings_checkov_without_build_output_is_two(tmp_path, gate, checkov):
    root = _solution(tmp_path, policies=_CHECKOV_POLICY.format(enforcement="deny"))
    shutil.rmtree(root / "build")
    result = _invoke("findings", "app", "--path", str(root), "--policy", "checkov_policy")
    assert result.exit_code == EXIT_USAGE, result.output


def test_findings_checkov_scan_failure_is_one(tmp_path, gate, checkov):
    checkov.error = IntegrationError("checkov not found on PATH")
    result = _invoke("findings", "app", "--path", str(_checkov_solution(tmp_path)), "--policy", "checkov_policy")
    assert result.exit_code == EXIT_FAILURE, result.output
