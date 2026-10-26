#!/usr/bin/env python3
"""Tests for `strata policy`'s controller (docs/work/security-gates-design.md D7; plan Phase 6).

A real on-disk solution is loaded with `open_solution()`; the gate integration is a vendor-neutral
fake returned by a monkeypatched `security_gate_controller.get_integration`, so no vendor code runs.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from strata.controllers import policy_controller
from strata.controllers import security_gate_controller as gates_module
from strata.controllers.policy_controller import check_policies, list_policies, policy_findings
from strata.controllers.policy_results import read_policy_results, write_policy_results
from strata.controllers.solution_context import open_solution
from strata.integrations.capabilities import CredentialedIntegration, ImageFindingsIntegration, ReleaseGateIntegration
from strata.integrations.errors import IntegrationError
from strata.integrations.security_findings import ImageLookup, ReleaseGateOutcome, SecurityIssue
from strata.models.audit_manifest_model import ManifestPolicyResultModel
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
        policies += "  cve_policy:\n    max_severity: HIGH\n"
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


def test_the_table_has_a_row_for_every_workspace_policy_field():
    fields = [name for name in WorkspaceSpecModel.model_fields if name.endswith("_policy")]
    assert sorted(row.name for row in policy_controller._POLICIES) == sorted(fields)


def test_only_the_gates_are_checkable_and_only_the_image_gate_has_findings():
    rows = {row.name: row for row in policy_controller._POLICIES}
    assert {name for name, row in rows.items() if row.check is not None} == {"release_gate_policy", "image_gate_policy"}
    assert {name for name, row in rows.items() if row.findings} == {"image_gate_policy"}


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_shows_every_configured_policy_without_results(tmp_path, gate):
    infos = list_policies(_context(_solution(tmp_path, cve=True)), "app", tmp_path / "missing")
    assert [(i.name, i.phase, i.enforcement, i.backing, i.checkable) for i in infos] == [
        ("cve_policy", "build", "deny", "trivy/grype", False),
        ("release_gate_policy", "deploy", "deny", "fake-gate 'gate-ci'", True),
        ("image_gate_policy", "deploy", "deny", "fake-gate 'gate-api'", True),
    ]
    assert all(info.last_result is None for info in infos)
    assert gate.requests == [] and gate.asked == []


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


def test_check_runs_every_checkable_policy_and_records_the_results(tmp_path, gate):
    build_path = _build_path(tmp_path, "pkg:docker/org/app@1.0")
    steps: list[str] = []
    diagnostics, checked = check_policies(
        _context(_solution(tmp_path, cve=True)), "app", build_path, on_step=steps.append
    )
    assert diagnostics.ok, diagnostics.messages()
    assert checked == ["release_gate_policy", "image_gate_policy"]
    assert len(gate.requests) == 1 and gate.asked
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
        (["cve_policy"], "does not evaluate cve_policy yet; it runs in the build phase"),
        (["nonsense_policy"], "Unknown policy 'nonsense_policy'"),
        (["checkov_policy"], "does not configure checkov_policy"),
    ],
)
def test_check_an_unsupported_or_unconfigured_policy_is_a_usage_error(tmp_path, gate, policies, match):
    with pytest.raises(UsageError, match=match):
        check_policies(_context(_solution(tmp_path, cve=True)), "app", tmp_path, policies=policies)
    assert gate.requests == []


def test_check_with_nothing_checkable_is_a_usage_error(tmp_path, gate):
    with pytest.raises(UsageError, match="Nothing to check"):
        check_policies(_context(_solution(tmp_path, release=False, image=False, cve=True)), "app", tmp_path)


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
    assert gate.asked == []


def test_findings_resolves_credentials_through_the_deployment(tmp_path, gate, monkeypatch):
    monkeypatch.setenv("TEST_POLICY_API_KEY", "secret-value-123456")
    build_path = _build_path(tmp_path, "pkg:docker/org/app@1.0")
    policy_findings(_context(_solution(tmp_path, image_auth=True)), "app", build_path, "image_gate_policy")
    assert gate.bound == [{"API_KEY": "secret-value-123456"}]
