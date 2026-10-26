#!/usr/bin/env python3
"""Tests for `strata integration check` (docs/work/integration-readiness.md D9-D13).

Runs the real Aikido and Terraform integrations with their HTTP transport / subprocess replaced, so no
network or real tool is touched.
"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from strata.commands.cli import cli
from strata.commands.exit_codes import EXIT_FAILURE, EXIT_SUCCESS, EXIT_USAGE, EXIT_VALIDATION
from strata.integrations import aikido as aikido_module
from strata.utils.transport import NO_RESPONSE, HttpResult

TOKEN = "AIK_CI_test-token-1234567890"
_HEADER = "apiVersion: strata.huybrechts.xyz/v2\nkind: {kind}\nmeta:\n  name: {name}\nspec:\n"
_AUTH = "  authentication:\n    method: api_key\n    api_key:\n      api_key: AIKIDO_TOKEN\n"


def _write(root: Path, relative: str, kind: str, name: str, spec: str) -> None:
    (root / relative).write_text(_HEADER.format(kind=kind, name=name) + spec, encoding="utf-8")


def _solution(tmp_path: Path) -> Path:
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(_HEADER.format(kind="solution", name="s") + "  {}\n", encoding="utf-8")
    ci = "  type: aikido\n  capabilities: [release_gate]\n  configuration:\n    max_retries: 0\n"
    _write(root, "aikido-env.yaml", "integration", "aikido-env", ci)
    _write(root, "aikido-ci.yaml", "integration", "aikido-ci", ci + _AUTH)
    _write(
        root,
        "terraform.yaml",
        "integration",
        "terraform",
        "  type: terraform\n  capabilities: [infrastructure]\n  version: '>= 1.7'\n",
    )
    _write(root, "git.yaml", "integration", "git-old", "  type: git\n  capabilities: [sources]\n")
    _write(
        root,
        "environment.yaml",
        "environment",
        "prd",
        "  variables:\n    - key: AIKIDO_TOKEN\n      store: environment\n      value: TEST_AIKIDO_TOKEN\n",
    )
    _write(root, "provider.yaml", "provider", "p1", "  type: local\n  region: local\n")
    (root / "infra").mkdir()
    (root / "infra" / "main.tf").write_text("# root module\n", encoding="utf-8")
    _write(
        root,
        "workspace.yaml",
        "workspace",
        "main",
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n",
    )
    _write(root, "deployment.yaml", "deployment", "app", "  workspace: main\n  environments:\n    - prd\n")
    return root


@pytest.fixture
def http(monkeypatch):
    calls: list[tuple[str, str, dict]] = []
    answer = {"result": HttpResult(status=404, body='{"error": "not found"}')}

    def _request(method, url, *, headers=None, body=None, timeout=30):
        calls.append((method, url, dict(headers or {})))
        return answer["result"]

    monkeypatch.setattr(aikido_module, "http_request", _request)
    for name in ("AIKIDO_CI_TOKEN", "AIKIDO_API_KEY", "STRATA_OUTPUT"):
        monkeypatch.delenv(name, raising=False)
    return answer, calls


def _check(root: Path, *args: str):
    return CliRunner().invoke(cli, ["integration", "check", *args, "--path", str(root)])


def test_help():
    assert CliRunner().invoke(cli, ["integration", "check", "--help"]).exit_code == EXIT_SUCCESS


def test_environment_fallback_ok_is_zero(tmp_path, http, monkeypatch):
    monkeypatch.setenv("AIKIDO_CI_TOKEN", TOKEN)
    result = _check(_solution(tmp_path), "aikido-env", "--output", "json")
    assert result.exit_code == EXIT_SUCCESS, result.output
    data = json.loads(result.output)["data"]
    assert data["type"] == "aikido" and data["checks"][0].startswith("release_gate: CI token accepted")
    assert TOKEN not in result.output


def test_credentials_resolve_through_the_deployment(tmp_path, http, monkeypatch):
    monkeypatch.setenv("TEST_AIKIDO_TOKEN", TOKEN)
    _, calls = http
    result = _check(_solution(tmp_path), "aikido-ci", "--deployment", "app")
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert calls[0][2]["X-AIK-API-SECRET"] == TOKEN
    assert TOKEN not in result.output


def test_rejected_credentials_are_three(tmp_path, http, monkeypatch):
    answer, _ = http
    answer["result"] = HttpResult(status=401, body="unauthorized")
    monkeypatch.setenv("AIKIDO_CI_TOKEN", TOKEN)
    result = _check(_solution(tmp_path), "aikido-env")
    assert result.exit_code == EXIT_VALIDATION, result.output
    assert TOKEN not in result.output


def test_missing_credentials_are_three(tmp_path, http):
    result = _check(_solution(tmp_path), "aikido-env")
    assert result.exit_code == EXIT_VALIDATION, result.output


def test_unresolved_credentials_are_three(tmp_path, http, monkeypatch):
    monkeypatch.delenv("TEST_AIKIDO_TOKEN", raising=False)
    result = _check(_solution(tmp_path), "aikido-ci", "--deployment", "app")
    assert result.exit_code == EXIT_VALIDATION, result.output
    assert "AIKIDO_TOKEN" in result.output


def test_unreachable_is_one(tmp_path, http, monkeypatch):
    answer, _ = http
    answer["result"] = HttpResult(status=NO_RESPONSE, body="connection refused")
    monkeypatch.setenv("AIKIDO_CI_TOKEN", TOKEN)
    result = _check(_solution(tmp_path), "aikido-env")
    assert result.exit_code == EXIT_FAILURE, result.output


def test_authentication_without_deployment_is_two(tmp_path, http):
    _, calls = http
    result = _check(_solution(tmp_path), "aikido-ci")
    assert result.exit_code == EXIT_USAGE, result.output
    assert "--deployment" in result.output
    assert calls == []


@pytest.fixture
def tools(monkeypatch):
    """Local tools: `on_path` decides `shutil.which`; `terraform version` prints `version`."""
    import strata.integrations.base as base_module
    from strata.utils.transport import CommandResult

    state = {"on_path": True, "version": "1.9.2", "runs": []}

    def _run(args, **kwargs):
        state["runs"].append(list(args))
        return CommandResult(returncode=0, stdout=f"Terraform v{state['version']}", stderr="")

    monkeypatch.setattr(base_module, "run_command", _run)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}" if state["on_path"] else None)
    return state


def test_a_local_tool_without_a_connection_check_passes_on_local_readiness(tmp_path, http, tools):
    result = _check(_solution(tmp_path), "terraform", "--output", "json")
    assert result.exit_code == EXIT_SUCCESS, result.output
    payload = json.loads(result.output)
    readiness = payload["data"]["readiness"]
    assert readiness["ok"] and readiness["available"] and readiness["installed_version"] == "1.9.2"
    assert readiness["required_version"] == ">= 1.7" and payload["data"]["declared"]
    codes = [d["code"] for d in payload["diagnostics"]]
    assert codes == ["integration_ready", "integration_no_connection_check"]


def test_a_version_mismatch_is_three(tmp_path, http, tools):
    tools["version"] = "1.5.0"
    result = _check(_solution(tmp_path), "terraform")
    assert result.exit_code == EXIT_VALIDATION, result.output
    assert "integration_version_mismatch" in result.output


def test_a_tool_not_on_path_is_three_and_is_not_run(tmp_path, http, tools):
    tools["on_path"] = False
    result = _check(_solution(tmp_path), "terraform")
    assert result.exit_code == EXIT_VALIDATION, result.output
    assert "not available on PATH" in result.output and tools["runs"] == []


def test_an_undeclared_known_type_is_checked_with_its_defaults(tmp_path, http, tools):
    result = _check(_solution(tmp_path), "helm", "--output", "json")
    assert result.exit_code == EXIT_SUCCESS, result.output
    data = json.loads(result.output)["data"]
    assert data["type"] == "helm" and data["declared"] is False


def test_a_type_with_several_enabled_documents_is_two(tmp_path, http):
    result = _check(_solution(tmp_path), "aikido")
    assert result.exit_code == EXIT_USAGE, result.output
    assert "multiple 'aikido' integrations" in result.output


def test_a_sources_only_document_without_a_class_is_a_credential_holder(tmp_path, http):
    result = _check(_solution(tmp_path), "git-old")
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert "credential holder" in result.output and "no connection check" not in result.output


def test_a_document_of_an_unregistered_type_is_three(tmp_path, http):
    root = _solution(tmp_path)
    _write(root, "ansible.yaml", "integration", "ansible-old", "  type: ansible\n  capabilities: [infrastructure]\n")
    result = _check(root, "ansible-old")
    assert result.exit_code == EXIT_VALIDATION, result.output
    assert "integration_type_unregistered" in result.output and "not yet ported" in result.output


def test_disabled_integration_is_three_and_not_contacted(tmp_path, http, monkeypatch):
    monkeypatch.setenv("AIKIDO_CI_TOKEN", TOKEN)
    root = _solution(tmp_path)
    document = root / "aikido-env.yaml"
    document.write_text(document.read_text(encoding="utf-8") + "  enabled: false\n", encoding="utf-8")
    _, calls = http
    result = _check(root, "aikido-env")
    assert result.exit_code == EXIT_VALIDATION, result.output
    assert "integration_disabled" in result.output and calls == []


def test_unknown_deployment_is_two(tmp_path, http, monkeypatch):
    monkeypatch.setenv("AIKIDO_CI_TOKEN", TOKEN)
    result = _check(_solution(tmp_path), "aikido-env", "--deployment", "nope")
    assert result.exit_code == EXIT_USAGE, result.output


def _deployable(root: Path) -> Path:
    """Give deployment 'app' an execution step, a Key Vault secret and a release gate (D16's bindings)."""
    _write(
        root,
        "resource.yaml",
        "resource",
        "r1",
        "  provider_type: local\n  resource_type: server\n  category: compute\n  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "workspace.yaml",
        "workspace",
        "main",
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n"
        "  release_gate_policy:\n    integration: aikido-ci\n    repository: org/infra\n",
    )
    _write(
        root,
        "environment.yaml",
        "environment",
        "prd",
        "  variables:\n    - key: AIKIDO_TOKEN\n      store: environment\n      value: TEST_AIKIDO_TOKEN\n"
        "  secrets:\n    - key: DB_PASSWORD\n      store: azure-keyvault\n      value: db-password\n",
    )
    return root


def _list(root: Path, *args: str):
    return CliRunner().invoke(cli, ["integration", "list", *args, "--path", str(root)])


def test_list_without_a_deployment_shows_every_document(tmp_path, http, tools):
    result = _list(_solution(tmp_path), "--output", "json")
    assert result.exit_code == EXIT_SUCCESS, result.output
    rows = {row["name"]: row for row in json.loads(result.output)["data"]["integrations"]}
    assert sorted(rows) == ["aikido-ci", "aikido-env", "git-old", "terraform"]
    assert rows["git-old"]["credential_holder"] and not rows["git-old"]["registered"]
    assert rows["terraform"]["installed_version"] == "1.9.2" and rows["terraform"]["ok"]
    assert all(row["used_for"] == [] for row in rows.values())


def test_list_with_a_deployment_shows_what_it_uses(tmp_path, http, tools):
    result = _list(_deployable(_solution(tmp_path)), "--deployment", "app", "--output", "json")
    assert result.exit_code == EXIT_SUCCESS, result.output
    rows = {row["name"]: row for row in json.loads(result.output)["data"]["integrations"]}
    assert rows["terraform"]["used_for"] == ["provisioner 'tf_main' (step 'apply')"]
    assert rows["aikido-ci"]["used_for"] == ["release_gate_policy"]
    assert rows["azure-keyvault"]["used_for"] == ["value store (1 key(s))"]
    assert rows["azure-keyvault"]["declared"] is False and rows["azure-keyvault"]["ok"]
    assert rows["aikido-env"]["used_for"] == []


def test_list_fails_only_when_a_used_integration_is_not_ready(tmp_path, http, tools):
    tools["version"] = "1.5.0"
    root = _deployable(_solution(tmp_path))
    plain = _list(root)
    assert plain.exit_code == EXIT_SUCCESS, plain.output
    assert "warning" in plain.output and "integration_version_mismatch" in plain.output
    used = _list(root, "--deployment", "app")
    assert used.exit_code == EXIT_VALIDATION, used.output
    assert "USED FOR" in used.output and "not ready" in used.output


def test_list_with_an_unknown_deployment_is_two(tmp_path, http, tools):
    assert _list(_solution(tmp_path), "--deployment", "nope").exit_code == EXIT_USAGE


def test_unknown_name_is_two_and_lists_declared_names_and_known_types(tmp_path, http):
    result = _check(_solution(tmp_path), "nope")
    assert result.exit_code == EXIT_USAGE, result.output
    assert "'nope' is not declared and is not a known integration type" in result.output
    assert "aikido-ci" in result.output and "terraform" in result.output
