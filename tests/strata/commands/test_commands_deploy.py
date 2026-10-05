#!/usr/bin/env python3
"""Tests for `strata deploy run` (docs/design/deploy-command.md).

Every test runs `build run` first (via the real CLI, matching real usage)
to populate `--build-path`, then exercises `deploy run` against it with
`run_command`/`shutil.which` stubbed — no real `terraform` binary needed,
same convention `test_deploy_controller.py` already established.
"""

from pathlib import Path

import pytest
from click.testing import CliRunner

from strata.commands.cli import cli
from strata.commands.exit_codes import EXIT_SUCCESS, EXIT_USAGE, EXIT_VALIDATION
from strata.utils.transport import CommandResult

MANIFEST = """apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec: {}
"""

PROVIDER = """apiVersion: strata.huybrechts.xyz/v2
kind: provider
meta:
  name: p1
spec:
  properties:
    type: local
    region: local
"""

RESOURCE = """apiVersion: strata.huybrechts.xyz/v2
kind: resource
meta:
  name: r1
spec:
  properties:
    provider_type: local
    resource_type: server
    category: compute
  default_tags:
    managed-by: strata
"""

WORKSPACE = """apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: main
spec:
  providers:
    - p1
  provisioners:
    - name: tf_main
      tool: terraform
      source:
        source_path: infra
  execution:
    - name: apply_infra
      provisioner: tf_main
      targets:
        - r1
  resources:
    - name: r1
      resource: r1
"""

ENVIRONMENT = """apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: prd
spec: {}
"""

DEPLOYMENT = """apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: app
spec:
  workspace: main
  environments:
    - prd
"""


@pytest.fixture
def runner():
    return CliRunner()


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def solution(tmp_path):
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)
    _write(root, "infra/main.tf", "# root module\n")
    _write(root, "provider.yaml", PROVIDER)
    _write(root, "resource.yaml", RESOURCE)
    _write(root, "workspace.yaml", WORKSPACE)
    _write(root, "environment.yaml", ENVIRONMENT)
    _write(root, "deployment.yaml", DEPLOYMENT)
    return root


@pytest.fixture
def _stub_terraform(monkeypatch):
    """No real `terraform` binary needed — every call succeeds."""
    calls: list[list[str]] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls.append(list(args))
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    return calls


def _build(runner, solution):
    result = runner.invoke(cli, ["build", "run", "app", "--path", str(solution)])
    assert result.exit_code == EXIT_SUCCESS, result.output


def _deploy(runner, *args):
    return runner.invoke(cli, ["deploy", "run", *[str(a) for a in args]])


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------


def test_successful_deploy_exits_zero(runner, solution, _stub_terraform):
    _build(runner, solution)
    result = _deploy(runner, "app", "--path", solution, "--force")
    assert result.exit_code == EXIT_SUCCESS, result.output


def test_unknown_deployment_exits_two(runner, solution, _stub_terraform):
    _build(runner, solution)
    result = _deploy(runner, "ghost-deployment", "--path", solution, "--force")
    assert result.exit_code == EXIT_USAGE


def test_outside_a_solution_exits_two(runner, tmp_path, _stub_terraform):
    result = _deploy(runner, "app", "--path", tmp_path, "--force")
    assert result.exit_code == EXIT_USAGE


def test_invalid_solution_exits_three(runner, solution, _stub_terraform):
    _write(solution, "deployment.yaml", DEPLOYMENT.replace("workspace: main", "workspace: ghost-workspace"))
    result = _deploy(runner, "app", "--path", solution, "--force")
    assert result.exit_code == EXIT_VALIDATION


def test_step_failure_exits_three(runner, solution, monkeypatch):
    _build(runner, solution)

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        if args[1] == "plan":
            return CommandResult(returncode=1, stdout="", stderr="plan exploded")
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    result = _deploy(runner, "app", "--path", solution, "--force")
    assert result.exit_code == EXIT_VALIDATION


# ---------------------------------------------------------------------------
# Options wiring
# ---------------------------------------------------------------------------


def test_build_path_option_overrides_the_default(runner, solution, tmp_path, _stub_terraform):
    custom = tmp_path / "out"
    result = runner.invoke(cli, ["build", "run", "app", "--path", str(solution), "--build-path", str(custom)])
    assert result.exit_code == EXIT_SUCCESS, result.output

    result = _deploy(runner, "app", "--path", solution, "--build-path", custom, "--force")
    assert result.exit_code == EXIT_SUCCESS, result.output


def test_dry_run_never_calls_run_command(runner, solution, _stub_terraform):
    _build(runner, solution)
    result = _deploy(runner, "app", "--path", solution, "--dry-run")
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert _stub_terraform == []


def test_stage_option_restricts_execution(runner, solution, _stub_terraform):
    _build(runner, solution)
    result = _deploy(runner, "app", "--path", solution, "--force", "--stage", "apply_infra")
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert any(call[1] == "apply" for call in _stub_terraform)


def test_stage_option_rejects_unknown_stage(runner, solution, _stub_terraform):
    _build(runner, solution)
    result = _deploy(runner, "app", "--path", solution, "--force", "--stage", "ghost-stage")
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert _stub_terraform == []


def test_force_flag_is_accepted_and_currently_inert(runner, solution, _stub_terraform):
    _build(runner, solution)
    result = _deploy(runner, "app", "--path", solution)
    assert result.exit_code == EXIT_SUCCESS, result.output
    apply_call = next(call for call in _stub_terraform if call[1] == "apply")
    assert "-auto-approve" not in apply_call


# ---------------------------------------------------------------------------
# --change-* (docs/design/audit-trail.md's ChangeReferenceModel CLI wiring)
# ---------------------------------------------------------------------------


def test_change_reference_flags_populate_the_manifest(runner, solution, _stub_terraform):
    from strata.utils import layout

    _build(runner, solution)
    result = _deploy(
        runner,
        "app",
        "--path",
        solution,
        "--force",
        "--change-system",
        "jira",
        "--change-id",
        "OPS-1234",
        "--change-reason",
        "planned maintenance",
    )
    assert result.exit_code == EXIT_SUCCESS, result.output

    manifest_path = next(layout.audit_dir(solution).rglob("_manifest.json"))
    manifest_text = manifest_path.read_text()
    assert '"system": "jira"' in manifest_text
    assert '"id": "OPS-1234"' in manifest_text
    assert '"reason": "planned maintenance"' in manifest_text


def test_partial_change_reference_flags_exit_two_before_deploy_runs(runner, solution, _stub_terraform):
    _build(runner, solution)
    result = _deploy(runner, "app", "--path", solution, "--force", "--change-system", "jira")
    assert result.exit_code == EXIT_USAGE
    assert _stub_terraform == []  # deploy_run() must never have been called


def test_change_approval_flags_populate_the_manifest(runner, solution, _stub_terraform):
    from strata.utils import layout

    _build(runner, solution)
    result = _deploy(
        runner,
        "app",
        "--path",
        solution,
        "--force",
        "--change-system",
        "jira",
        "--change-id",
        "OPS-1234",
        "--change-reason",
        "planned maintenance",
        "--change-approved-by",
        "jsmith",
        "--change-approved-at",
        "2026-10-04T09:00:00+00:00",
    )
    assert result.exit_code == EXIT_SUCCESS, result.output

    manifest_path = next(layout.audit_dir(solution).rglob("_manifest.json"))
    manifest_text = manifest_path.read_text()
    assert '"approved_by": "jsmith"' in manifest_text
    assert '"approved_at": "2026-10-04T09:00:00+00:00"' in manifest_text


def test_partial_change_approval_flags_exit_two_before_deploy_runs(runner, solution, _stub_terraform):
    _build(runner, solution)
    result = _deploy(
        runner,
        "app",
        "--path",
        solution,
        "--force",
        "--change-system",
        "jira",
        "--change-id",
        "OPS-1234",
        "--change-reason",
        "planned maintenance",
        "--change-approved-by",
        "jsmith",
    )
    assert result.exit_code == EXIT_USAGE
    assert _stub_terraform == []  # deploy_run() must never have been called


def test_change_approval_flags_without_the_base_trio_exit_two(runner, solution, _stub_terraform):
    """An approval needs a change reference to approve — --change-approved-by/
    --change-approved-at alone (no --change-system/--change-id/--change-reason)
    must not be silently dropped."""
    _build(runner, solution)
    result = _deploy(
        runner,
        "app",
        "--path",
        solution,
        "--force",
        "--change-approved-by",
        "jsmith",
        "--change-approved-at",
        "2026-10-04T09:00:00+00:00",
    )
    assert result.exit_code == EXIT_USAGE
    assert _stub_terraform == []
