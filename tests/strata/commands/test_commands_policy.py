#!/usr/bin/env python3
"""Tests for `strata policy list | check | findings` (docs/work/security-gates-design.md D7; plan Phase 6).

Exit codes and output shape through the real CLI; the gate integration is a vendor-neutral fake.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from strata.commands.cli import cli
from strata.commands.exit_codes import EXIT_FAILURE, EXIT_SUCCESS, EXIT_USAGE, EXIT_VALIDATION
from strata.controllers import security_gate_controller as gates_module
from strata.controllers.policy_results import read_policy_results
from strata.integrations.capabilities import ImageFindingsIntegration, ReleaseGateIntegration
from strata.integrations.errors import IntegrationError
from strata.integrations.security_findings import ImageLookup, ReleaseGateOutcome, SecurityIssue

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


def test_check_unsupported_policy_is_two(tmp_path, gate):
    result = _invoke("check", "app", "--path", str(_solution(tmp_path)), "--policy", "cve_policy")
    assert result.exit_code == EXIT_USAGE, result.output


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
