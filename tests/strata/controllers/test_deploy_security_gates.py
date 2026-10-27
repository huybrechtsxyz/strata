#!/usr/bin/env python3
"""`deploy_run()` with security gates (docs/work/security-gates-design.md D5, D9; plan Phase 5).

A real solution is built with `build_run()` and deployed with Terraform stubbed, as in
`test_deploy_controller.py`. The gate integration is a vendor-neutral fake returned by a
monkeypatched `security_gate_controller.get_integration`, so no Aikido code runs.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from strata.controllers import security_gate_controller as gates_module
from strata.controllers.build_controller import build_run
from strata.controllers.deploy_controller import deploy_run
from strata.controllers.policy_results import read_policy_results, write_policy_results
from strata.controllers.solution_context import open_solution
from strata.integrations.capabilities import CredentialedIntegration, ImageFindingsIntegration, ReleaseGateIntegration
from strata.integrations.security_findings import ImageLookup, ReleaseGateOutcome
from strata.models.audit_manifest_model import ManifestPolicyResultModel
from strata.utils.transport import CommandResult

TOKEN = "gate-token-value-1234567890"
COMMIT = "a" * 40


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _solution(tmp_path: Path, *, enforcement: str = "deny") -> Path:
    """One Terraform step, plus a release gate whose CI token is a plain variable (not a secret)."""
    root = tmp_path / "sln"
    _write(root, "strata.yaml", "apiVersion: strata.huybrechts.xyz/v2\nkind: solution\nmeta:\n  name: s\nspec: {}\n")
    _write(root, "infra/main.tf", "# root module\n")
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n  type: local\n  region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  provider_type: local\n  resource_type: server\n  category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "integration.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: integration\nmeta:\n  name: gate-ci\nspec:\n"
        "  type: fake-gate\n  capabilities: [release_gate]\n"
        "  authentication:\n    method: api_key\n    api_key:\n      api_key: GATE_TOKEN\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n"
        "  release_gate_policy:\n    integration: gate-ci\n    repository: org/infra\n"
        f"    enforcement: {enforcement}\n    commit: {COMMIT}\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  variables:\n    - key: GATE_TOKEN\n      store: environment\n      value: TEST_GATE_TOKEN\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    return root


def _context(root: Path):
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    return context


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
        return ImageLookup(status="found")


@pytest.fixture
def gate(monkeypatch):
    state = SimpleNamespace(outcome=ReleaseGateOutcome(passed=True, reference="22"), requests=[], bound=[])
    monkeypatch.setattr(gates_module, "get_integration", lambda integration_type, config=None: _FakeGate(config, state))
    monkeypatch.setenv("TEST_GATE_TOKEN", TOKEN)
    return state


@pytest.fixture
def terraform(monkeypatch):
    """Every terraform call succeeds and prints the gate token, so redaction can be observed."""
    calls: list[list[str]] = []

    def _run(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls.append(list(args))
        if line_callback is not None:
            line_callback("stdout", f"token is {TOKEN}")
        return CommandResult(returncode=0, stdout=f"token is {TOKEN}", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _run)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    return calls


def _built(tmp_path: Path, **solution) -> tuple[Path, Path]:
    root = _solution(tmp_path, **solution)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)
    return root, build_path


def test_a_denied_gate_stops_the_deploy_before_any_step(tmp_path, gate, terraform):
    """Two guards make this hold: the gate hook's own early return, and deploy_run's general
    `if not diagnostics.ok` before steps. The hook returns first, before graph and payload work."""
    gate.outcome = ReleaseGateOutcome(passed=False, reference="22", open_issues=2)
    root, build_path = _built(tmp_path)

    diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert [d.code for d in diagnostics.errors] == ["release_gate_failed"]
    assert terraform == []
    assert gate.requests[0].commit == COMMIT
    assert gate.bound == [{"GATE_TOKEN": TOKEN}]


def test_a_passed_gate_lets_the_deploy_run_and_is_recorded(tmp_path, gate, terraform):
    root, build_path = _built(tmp_path)
    build_entry = ManifestPolicyResultModel(
        policy_name="cve_policy", policy_type="cve_max_severity", phase="build", enforcement="deny", passed=True
    )
    write_policy_results(build_path, [build_entry])

    diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert diagnostics.ok, diagnostics.messages()
    assert [call[1] for call in terraform] == ["init", "validate", "plan", "apply", "output"]
    results = read_policy_results(build_path)
    assert [(r.policy_name, r.policy_type, r.phase, r.passed) for r in results] == [
        ("cve_policy", "cve_max_severity", "build", True),
        ("release_gate_policy", "fake-gate", "deploy", True),
    ]


def test_a_warn_breach_deploys_and_is_recorded_with_its_violation(tmp_path, gate, terraform):
    gate.outcome = ReleaseGateOutcome(passed=False, reference="22")
    root, build_path = _built(tmp_path, enforcement="warn")

    diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert diagnostics.ok
    # The fake type is patched into the gate controller only, so the integration preflight warns it is unregistered.
    assert [d.code for d in diagnostics.warnings if d.code != "integration_type_unregistered"] == [
        "release_gate_failed"
    ]
    assert terraform
    result = read_policy_results(build_path)[0]
    assert result.passed is True and result.violations


def test_smoke_test_never_contacts_the_gate(tmp_path, gate, terraform):
    root, build_path = _built(tmp_path)
    steps: list[str] = []

    deploy_run(_context(root), "app", build_path, smoke_test=True, on_step=steps.append)

    assert gate.requests == []
    assert any("would run security gates" in step for step in steps)


def test_dry_run_still_runs_the_gate(tmp_path, gate, terraform):
    gate.outcome = ReleaseGateOutcome(passed=False)
    root, build_path = _built(tmp_path)

    diagnostics = deploy_run(_context(root), "app", build_path, dry_run=True)

    assert gate.requests
    assert not diagnostics.ok


def test_gate_credentials_are_redacted_even_when_declared_as_a_variable(tmp_path, gate, terraform):
    """GATE_TOKEN is a variable, so only the gate's on_secret hand-off puts it in the redaction set."""
    root, build_path = _built(tmp_path)
    steps: list[str] = []
    lines: list[str] = []

    diagnostics = deploy_run(
        _context(root),
        "app",
        build_path,
        force=True,
        on_step=steps.append,
        on_line=lambda tool, stream, text: lines.append(text),
    )

    assert diagnostics.ok, diagnostics.messages()
    assert lines and all(TOKEN not in line for line in lines)
    assert any("***" in line for line in lines)
    assert all(TOKEN not in text for text in steps + diagnostics.messages())
    assert TOKEN not in json.dumps([r.model_dump() for r in read_policy_results(build_path)])


def test_without_a_gate_policy_no_gate_integration_is_built(tmp_path, terraform, monkeypatch):
    built: list[str] = []
    monkeypatch.setattr(gates_module, "get_integration", lambda *a, **k: built.append("x"))
    monkeypatch.setenv("TEST_GATE_TOKEN", TOKEN)
    root = _solution(tmp_path)
    workspace = root / "workspace.yaml"
    text = workspace.read_text(encoding="utf-8")
    workspace.write_text(text[: text.index("  release_gate_policy:")], encoding="utf-8")
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert diagnostics.ok, diagnostics.messages()
    assert built == []
    assert read_policy_results(build_path) is None
