#!/usr/bin/env python3
"""Tests for `deploy_controller.deploy_run()` (docs/design/deploy-command.md).

Real solution fixtures build a workspace/deployment via `build_run()` first
(so `build_path` is populated exactly the way a real `strata build run`
would leave it), then `deploy_run()` is exercised against that output with
`run_command`/`shutil.which` stubbed — no real `terraform` binary needed,
same pattern `test_integrations_terraform.py` already uses.
"""

import json
from pathlib import Path

import pytest

from strata.controllers.build_controller import build_run
from strata.controllers.deploy_controller import deploy_run, tf_var_env
from strata.controllers.solution_context import open_solution
from strata.integrations.resolved_context import ValueResolution
from strata.utils.errors import UsageError
from strata.utils.transport import CommandResult

MANIFEST = """apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec: {}
"""


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _solution(tmp_path: Path) -> Path:
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)
    return root


def _context(root: Path):
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    return context


def _terraform_solution(tmp_path: Path, *, second_step: bool = False) -> Path:
    """One provider/resource/workspace with 1 (or 2 dependent) Terraform
    provisioner step(s), one environment, one deployment — mirrors
    `test_build_controller.py`'s own fixture, extended with a second,
    dependent step when `second_step=True` (ordering/multi-step tests)."""
    root = _solution(tmp_path)
    _write(root, "infra/main.tf", "# root module\n")
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  properties:\n    type: local\n    region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: server\n    category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    execution = "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      targets:\n        - r1\n      scope: infra\n"
    if second_step:
        execution += (
            "    - name: apply_apps\n      provisioner: tf_main\n      targets:\n        - r1\n"
            "      depends_on:\n        - apply_infra\n      scope: apps\n"
        )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        f"{execution}"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec: {}\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    return root


@pytest.fixture
def _capture(monkeypatch):
    """Stub `run_command`/`shutil.which` so every `terraform` invocation
    succeeds without a real binary — records every call's argv, in order."""
    calls: list[list[str]] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls.append(list(args))
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    return calls


def test_deploy_run_executes_full_lifecycle_per_step(tmp_path: Path, _capture):
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert diagnostics.ok
    commands = [call[1] for call in _capture]
    assert commands == ["init", "validate", "plan", "apply", "output"]


def test_deploy_run_plans_to_a_saved_file_and_applies_it(tmp_path: Path, _capture):
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    deploy_run(_context(root), "app", build_path)

    plan_call = next(call for call in _capture if call[1] == "plan")
    apply_call = next(call for call in _capture if call[1] == "apply")
    assert "-out" in plan_call and "apply_infra.tfplan" in plan_call
    assert "apply_infra.tfplan" in apply_call


def test_deploy_run_apply_never_uses_auto_approve(tmp_path: Path, _capture):
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    deploy_run(_context(root), "app", build_path, force=True)

    apply_call = next(call for call in _capture if call[1] == "apply")
    assert "-auto-approve" not in apply_call


def test_deploy_run_dry_run_calls_init_validate_plan_but_not_deploy(tmp_path: Path, _capture):
    """`--dry-run` now runs a real preview (`init`/`validate`/`plan`, saved to
    `<step>.tfplan` same as a normal run) and stops — `deploy`/`output`
    are never called. Matches v1's own `setup -> check -> plan` dry-run
    sequence; replaces the old "zero tool contact" meaning, which moved to
    `smoke_test`.
    """
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    diagnostics = deploy_run(_context(root), "app", build_path, force=True, dry_run=True)

    assert diagnostics.ok
    commands = [call[1] for call in _capture]
    assert commands == ["init", "validate", "plan"]


def test_deploy_run_dry_run_plan_failure_is_a_hard_error(tmp_path: Path, monkeypatch):
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        if args[1] == "plan":
            return CommandResult(returncode=1, stdout="", stderr="plan exploded")
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    diagnostics = deploy_run(_context(root), "app", build_path, force=True, dry_run=True)

    assert not diagnostics.ok
    assert any("plan failed" in e.message for e in diagnostics.errors)


def test_deploy_run_init_failure_is_a_hard_error(tmp_path: Path, monkeypatch):
    """Code review finding 1 (2026-10-06): `terraform init`'s own
    `CommandResult` was previously discarded unchecked — a failing init
    silently fell through to `validate`/`plan` instead of aborting."""
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        if args[1] == "init":
            return CommandResult(returncode=1, stdout="", stderr="init exploded")
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert not diagnostics.ok
    assert any("init failed" in e.message for e in diagnostics.errors)


def test_deploy_run_validate_failure_is_a_hard_error(tmp_path: Path, monkeypatch):
    """Code review finding 1 (2026-10-06): same gap as init, for
    `terraform validate`."""
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        if args[1] == "validate":
            return CommandResult(returncode=1, stdout="", stderr="validate exploded")
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert not diagnostics.ok
    assert any("validate failed" in e.message for e in diagnostics.errors)


def test_deploy_run_dry_run_integration_error_from_plan_is_a_warning_not_a_failure(tmp_path: Path, monkeypatch):
    """A tool whose `plan()` raises `IntegrationError` (no meaningful
    preview supported) is a warning, and the run continues — not a hard
    failure."""
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    from strata.integrations.errors import IntegrationError
    from strata.integrations.terraform import TerraformIntegration

    def _raising_plan(self, path, **kwargs):
        raise IntegrationError("no preview mechanism for this tool")

    monkeypatch.setattr(TerraformIntegration, "plan", _raising_plan)

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    diagnostics = deploy_run(_context(root), "app", build_path, force=True, dry_run=True)

    assert diagnostics.ok
    assert len(diagnostics.warnings) == 1
    assert "no plan preview available" in diagnostics.warnings[0].message


def test_deploy_run_real_apply_integration_error_from_plan_is_a_hard_failure(tmp_path: Path, monkeypatch):
    """Code review finding (2026-10-06): a tool whose `plan()` raises
    `IntegrationError` must NOT silently skip `deploy()` on a real,
    non-dry-run apply — `plan()` is a required prerequisite there, not an
    optional preview, so reporting the run as successful having never
    actually deployed the step would be a real correctness bug. Matches
    Helm/Compose's `deploy_namespace()`, which only ever treats a missing
    plan capability as a graceful skip inside its own `dry_run` branch."""
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    from strata.integrations.errors import IntegrationError
    from strata.integrations.terraform import TerraformIntegration

    def _raising_plan(self, path, **kwargs):
        raise IntegrationError("no preview mechanism for this tool")

    monkeypatch.setattr(TerraformIntegration, "plan", _raising_plan)

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert not diagnostics.ok
    assert any("plan is required before apply" in e.message for e in diagnostics.errors)


def test_deploy_run_streams_subprocess_lines_tool_prefixed(tmp_path: Path, monkeypatch):
    """`on_line` receives `(tool, stream, text)` for every subprocess
    output line, enriched with the current step's integration type by the
    orchestrator itself (the CLI layer has no per-step hook of its own)."""
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        if line_callback is not None:
            line_callback("stdout", f"fake output for {' '.join(args)}")
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    lines: list[tuple[str, str, str]] = []
    diagnostics = deploy_run(
        _context(root),
        "app",
        build_path,
        force=True,
        on_line=lambda tool, stream, text: lines.append((tool, stream, text)),
    )

    assert diagnostics.ok
    assert lines  # at least one line streamed
    assert all(tool == "terraform" for tool, _stream, _text in lines)
    assert all(stream == "stdout" for _tool, stream, _text in lines)
    assert any("init" in text for _tool, _stream, text in lines)
    assert any("plan" in text for _tool, _stream, text in lines)
    assert any("apply" in text for _tool, _stream, text in lines)


def _solution_with_secret(tmp_path: Path, secret_value: str) -> Path:
    """One Terraform step, one declared secret — shared by the redaction
    tests below."""
    root = _solution(tmp_path)
    _write(root, "infra/main.tf", "# root module\n")
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  properties:\n    type: local\n    region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: server\n    category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      targets:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        f"  secrets:\n    - key: db_password\n      store: constant\n      value: {secret_value}\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    return root


def test_deploy_run_redacts_secret_values_from_streamed_output(tmp_path: Path, monkeypatch):
    """A resolved secret value must never reach `on_line` verbatim, since
    some tools (Helm's `--dry-run` rendered manifest) echo substituted
    values in their own output."""
    root = _solution_with_secret(tmp_path, "hunter2")
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        if line_callback is not None:
            line_callback("stdout", "db_password = hunter2")
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    lines: list[str] = []
    diagnostics = deploy_run(
        _context(root), "app", build_path, force=True, on_line=lambda tool, stream, text: lines.append(text)
    )

    assert diagnostics.ok
    assert lines
    assert all("hunter2" not in text for text in lines)
    assert any("***" in text for text in lines)


def test_deploy_run_does_not_redact_a_secret_shorter_than_the_floor(tmp_path: Path, monkeypatch):
    """A secret value below `_MIN_REDACT_LENGTH` is left unmasked —
    accepted tradeoff, redacting it would turn ordinary short substrings
    throughout the rest of the output into `***` too."""
    root = _solution_with_secret(tmp_path, "abc")
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        if line_callback is not None:
            line_callback("stdout", "db_password = abc")
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    lines: list[str] = []
    diagnostics = deploy_run(
        _context(root), "app", build_path, force=True, on_line=lambda tool, stream, text: lines.append(text)
    )

    assert diagnostics.ok
    assert any("abc" in text for text in lines)


def test_deploy_run_smoke_test_never_calls_run_command(tmp_path: Path, _capture):
    """`smoke_test` keeps the old `dry_run` meaning permanently: zero tool
    contact. `dry_run` itself was redefined to run a real
    init/validate/plan preview instead (see
    `test_deploy_run_dry_run_calls_init_validate_plan_but_not_deploy`).
    """
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    diagnostics = deploy_run(_context(root), "app", build_path, force=True, smoke_test=True)

    assert diagnostics.ok
    assert _capture == []


def test_deploy_run_filters_by_stage(tmp_path: Path, _capture):
    root = _terraform_solution(tmp_path, second_step=True)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    steps: list[str] = []
    deploy_run(_context(root), "app", build_path, force=True, stage="apply_infra", on_step=steps.append)

    assert any("apply_infra" in s for s in steps)
    assert not any("apply_apps" in s for s in steps)


def test_deploy_run_filters_by_scope(tmp_path: Path, _capture):
    root = _terraform_solution(tmp_path, second_step=True)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    steps: list[str] = []
    deploy_run(_context(root), "app", build_path, force=True, scope="apps", on_step=steps.append)

    assert any("apply_apps" in s for s in steps)
    assert not any("apply_infra" in s for s in steps)


def test_deploy_run_runs_dependent_steps_in_order(tmp_path: Path, _capture):
    root = _terraform_solution(tmp_path, second_step=True)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    steps: list[str] = []
    deploy_run(_context(root), "app", build_path, force=True, on_step=steps.append)

    infra_index = next(i for i, s in enumerate(steps) if "apply_infra" in s)
    apps_index = next(i for i, s in enumerate(steps) if "apply_apps" in s)
    assert infra_index < apps_index


def test_tf_var_env_maps_resolved_values_to_tf_var_prefixed_env():
    resolved = ValueResolution(deployment="app", values={"db_password": "hunter2", "api_key": "abc123"})

    assert tf_var_env(resolved, "TF_VAR_") == {"TF_VAR_db_password": "hunter2", "TF_VAR_api_key": "abc123"}


def test_tf_var_env_empty_when_no_values_resolved():
    resolved = ValueResolution(deployment="app", values={})

    assert tf_var_env(resolved, "TF_VAR_") == {}


def test_tf_var_env_empty_when_prefix_is_none():
    """docs/design/gap_fit_v1.md: a container-capable integration (or any future
    infra tool with no env-var-prefix mechanism) declares `ENV_VAR_PREFIX
    = None` - this whole mechanism has nothing to deliver for it."""
    resolved = ValueResolution(deployment="app", values={"db_password": "hunter2"})

    assert tf_var_env(resolved, None) == {}


def test_deploy_run_injects_tf_var_env_for_every_step(tmp_path: Path, monkeypatch):
    root = _solution(tmp_path)
    _write(root, "infra/main.tf", "# root module\n")
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  properties:\n    type: local\n    region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: server\n    category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      targets:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  secrets:\n    - key: db_password\n      store: constant\n      value: hunter2\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    envs: list[dict[str, str] | None] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        envs.append(env)
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert diagnostics.ok
    assert len(envs) == 5  # init, validate, plan, apply, output
    assert all(env is not None and env.get("TF_VAR_db_password") == "hunter2" for env in envs)
    # Phase 6 (docs/design/gap_fit_v1.md gap #8): resource configuration is now also
    # delivered, broadcast to every step regardless of ownership. Every
    # resx_<type> merges into one TF_VAR_resources (docs/design/
    # terraform-tfvars-parity.md: every real file is actually named
    # "resources" regardless of type, not TF_VAR_resx_<type>).
    assert all(env is not None and "TF_VAR_resources" in env for env in envs)


# ---------------------------------------------------------------------------
# deploy_run()'s --pin (docs/work/version-lifecycle.md Phase 5) — covers the
# `artifacts` pin category, the one that resolves live at deploy time.
# ---------------------------------------------------------------------------


def _artifact_pin_solution(tmp_path: Path, *, reference_version_from_deployment: bool) -> Path:
    """One Terraform provisioner, one Artifact, one Version document pinning
    it, and a `store: artifact` environment variable — `reference_version_from_deployment`
    controls whether `deployment.spec.version` names the Version document
    directly (the hand-edited case) or not at all (the --pin-only case)."""
    root = _solution(tmp_path)
    _write(root, "infra/main.tf", "# root module\n")
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  properties:\n    type: local\n    region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: server\n    category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      targets:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "artifact.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: artifact\nmeta:\n  name: dspapi_container\nspec:\n"
        "  image_name: int-docker-test/src/acme.dispatcher.api\n  image_tag: '1.0.0'\n",
    )
    _write(
        root,
        "version.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: version\nmeta:\n  name: prd\nspec:\n"
        "  pins:\n    artifacts:\n      dspapi_container: '2.0.0'\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  variables:\n    - key: image_tag\n      store: artifact\n      value: dspapi_container\n"
        "      field: image_tag\n",
    )
    version_line = "  version: prd\n" if reference_version_from_deployment else ""
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        f"  workspace: main\n{version_line}  environments:\n    - prd\n",
    )
    return root


def test_deploy_run_pin_resolves_the_artifacts_category(tmp_path: Path, monkeypatch):
    root = _artifact_pin_solution(tmp_path, reference_version_from_deployment=False)
    assert "version" not in (root / "deployment.yaml").read_text(encoding="utf-8")
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    envs: list[dict[str, str] | None] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        envs.append(env)
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    diagnostics = deploy_run(_context(root), "app", build_path, force=True, pin="prd")

    assert diagnostics.ok, diagnostics.messages()
    # A 'variables'-category key (FLAT_CATEGORIES handling) is delivered as
    # a raw string, same as a secret's own TF_VAR_ value — HashiCorp's own
    # documented TF_VAR convention for a plain scalar (docs/work/
    # terraform-variable-precedence.md Open Question 7 — json.dumps()-ing a
    # plain string embedded spurious literal quote characters that Terraform
    # never unwraps).
    assert all(env is not None and env.get("TF_VAR_image_tag") == "2.0.0" for env in envs)


def test_deploy_run_without_pin_uses_the_artifacts_own_declared_tag(tmp_path: Path, monkeypatch):
    """Control case: omitting --pin leaves the artifact's own declared
    image_tag in effect, proving the pin (not some other change) is what
    moved the value in the test above."""
    root = _artifact_pin_solution(tmp_path, reference_version_from_deployment=False)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    envs: list[dict[str, str] | None] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        envs.append(env)
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert diagnostics.ok, diagnostics.messages()
    assert all(env is not None and env.get("TF_VAR_image_tag") == "1.0.0" for env in envs)


def test_deploy_run_pin_never_mutates_the_deployment_document_on_disk(tmp_path: Path, monkeypatch):
    root = _artifact_pin_solution(tmp_path, reference_version_from_deployment=False)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)
    deployment_path = root / "deployment.yaml"
    before = deployment_path.read_text(encoding="utf-8")

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    deploy_run(_context(root), "app", build_path, force=True, pin="prd")

    assert deployment_path.read_text(encoding="utf-8") == before


def test_deploy_run_pin_raises_for_an_unknown_version(tmp_path: Path):
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    with pytest.raises(UsageError, match="--pin names an unknown version document: 'ghost'"):
        deploy_run(_context(root), "app", build_path, force=True, pin="ghost")


def test_deploy_run_pin_matches_hand_editing_spec_version(tmp_path: Path, monkeypatch):
    """The exact "Done when" criterion, deploy side: a --pin'd deploy
    produces identical resolution to hand-editing spec.version."""
    hand_edited_root = _artifact_pin_solution(tmp_path / "hand-edited", reference_version_from_deployment=True)
    pinned_root = _artifact_pin_solution(tmp_path / "pinned", reference_version_from_deployment=False)

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    hand_edited_build = tmp_path / "hand-edited-build"
    build_run(_context(hand_edited_root), "app", hand_edited_build)
    hand_edited_diagnostics = deploy_run(_context(hand_edited_root), "app", hand_edited_build, force=True)

    pinned_build = tmp_path / "pinned-build"
    build_run(_context(pinned_root), "app", pinned_build)
    pinned_diagnostics = deploy_run(_context(pinned_root), "app", pinned_build, force=True, pin="prd")

    assert hand_edited_diagnostics.ok and pinned_diagnostics.ok


def test_deploy_run_stops_on_plan_failure(tmp_path: Path, monkeypatch):
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        if args[1] == "plan":
            return CommandResult(returncode=1, stdout="", stderr="plan exploded")
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert not diagnostics.ok
    assert "plan failed" in diagnostics.messages()[0]


def test_deploy_run_preflight_fails_when_tool_unavailable(tmp_path: Path, monkeypatch):
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: None)

    diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert not diagnostics.ok
    assert "not available on PATH" in diagnostics.messages()[0]


def test_deploy_run_resolves_backend_configuration_tokens(tmp_path: Path):
    root = _solution(tmp_path)
    _write(root, "infra/main.tf", "# root module\n")
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  properties:\n    type: local\n    region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: server\n    category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "      backend:\n        type: azurerm\n        configuration:\n"
        "          resource_group_name: ${var:tf_state_resource_group}\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      targets:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  variables:\n    - key: tf_state_resource_group\n      store: constant\n      value: rg-prd\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    calls: list[list[str]] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls.append(list(args))
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as monkeypatch:
        import strata.integrations.base as base_module

        monkeypatch.setattr(base_module, "run_command", _fake_run_command)
        monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

        diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert diagnostics.ok
    init_call = next(call for call in calls if call[1] == "init")
    assert "-backend-config" in init_call
    assert "resource_group_name=rg-prd" in init_call


def test_deploy_run_resolves_dns_networks_firewalls_tokens_via_tf_var(tmp_path: Path):
    """docs/design/gap_fit_v1.md gap #9 / value-token-resolution.md Full Solution Phase 2:
    a '${var:KEY}' inside a DNS record's value (or a network/firewall field)
    is delivered as a whole resolved JSON payload via TF_VAR_dns_zones/networks/
    firewalls (docs/design/terraform-tfvars-parity.md: `dns`'s real v1
    Terraform variable name is `dns_zones`, not the file-category name)
    — AND (docs/design/terraform-variable-precedence.md) the on-disk
    .auto.tfvars.json is now rewritten with the same resolved value, since
    Terraform's own precedence would otherwise let the stale, unresolved
    file silently shadow the correctly-resolved TF_VAR_ env var."""
    root = _solution(tmp_path)
    _write(root, "infra/main.tf", "# root module\n")
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  properties:\n    type: local\n    region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: server\n    category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "dns.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: dns\nmeta:\n  name: public-dns\nspec:\n"
        "  zones:\n    - name: example.com\n      records:\n"
        "        - name: '@'\n          type: A\n          value: '${var:public_ip}'\n"
        "      default_tags:\n        environment: prd\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  dns_zones:\n    - public-dns\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      targets:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  variables:\n    - key: public_ip\n      store: constant\n      value: 1.2.3.4\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    envs: list[dict[str, str] | None] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        envs.append(env)
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(base_module, "run_command", _fake_run_command)
        monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

        diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert diagnostics.ok
    init_env = next(env for env in envs if env is not None)
    assert "TF_VAR_dns_zones" in init_env
    dns_payload = json.loads(init_env["TF_VAR_dns_zones"])
    record_value = dns_payload["public-dns"]["zones"]["example.com"]["records"][0]["value"]
    assert record_value == "1.2.3.4"
    # On-disk build artifact is now rewritten with the resolved value too
    # (docs/design/terraform-variable-precedence.md) — no secret-shaped
    # leaf anywhere in this category, so the file legitimately wins and must
    # carry the correct value, not the stale literal token.
    on_disk = json.loads((build_path / "infra" / "dns.auto.tfvars.json").read_text())
    assert on_disk["dns_zones"]["public-dns"]["zones"]["example.com"]["records"][0]["value"] == "1.2.3.4"
    # No networks/firewalls documents in this workspace — no TF_VAR set.
    assert "TF_VAR_networks" not in init_env
    assert "TF_VAR_firewalls" not in init_env


def test_deploy_run_never_creates_dns_networks_firewalls_files_when_unused(tmp_path: Path, _capture):
    """Code review finding (2026-10-08): `build_dns_networks_firewalls_payloads()`
    unconditionally returns all three keys ('dns'/'networks'/'firewalls'),
    even `{}` ones, unlike `build_configuration_payloads()`, which already
    filters empty categories. Without filtering by the *workspace-wide*
    payload first, the deploy-time file-rewrite step would have newly
    CREATED `dns.auto.tfvars.json`/`networks.auto.tfvars.json`/
    `firewalls.auto.tfvars.json` (each `{"<name>": {}}`) for every single
    deploy run, even for a workspace declaring none of the three —
    violating docs/design/terraform-variable-precedence.md's own "never
    create a file build run did not produce" boundary, and risking a real
    Terraform "value for undeclared variable" warning on every deploy of
    every workspace that doesn't use these categories at all."""
    root = _terraform_solution(tmp_path)  # no dns/networks/firewalls declared
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    infra = build_path / "infra"
    before = {f.name for f in infra.glob("*.auto.tfvars.json")}
    assert "dns.auto.tfvars.json" not in before
    assert "networks.auto.tfvars.json" not in before
    assert "firewalls.auto.tfvars.json" not in before

    diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert diagnostics.ok, diagnostics.messages()
    after = {f.name for f in infra.glob("*.auto.tfvars.json")}
    assert after == before, f"deploy run created new file(s): {after - before}"


def test_deploy_run_resolves_configuration_payloads_tokens_via_tf_var(tmp_path: Path):
    """docs/design/gap_fit_v1.md gap #8 (Terraform-side refinement) / value-token-
    resolution.md Full Solution Phase 6: a '${var:KEY}' inside a resource's
    'configuration' is delivered via TF_VAR_resources (docs/design/
    terraform-tfvars-parity.md: every resx_<type> merges into the one real
    "resources" Terraform variable, not a separate TF_VAR_resx_<type> per
    type) — broadcast to every step. The on-disk resx_<type>.auto.tfvars.json
    is always blanked to `{}` (docs/design/terraform-variable-precedence.md),
    never rewritten with its own resolved value — TF_VAR_resources
    above is the sole, uncontested source either way."""
    root = _solution(tmp_path)
    _write(root, "infra/main.tf", "# root module\n")
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  properties:\n    type: local\n    region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: server\n    category: compute\n"
        "  configuration:\n    admin_password: '${secret:vm_admin_password}'\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      targets:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  secrets:\n    - key: vm_admin_password\n      store: constant\n      value: hunter2\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    envs: list[dict[str, str] | None] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        envs.append(env)
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(base_module, "run_command", _fake_run_command)
        monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

        diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert diagnostics.ok
    init_env = next(env for env in envs if env is not None)
    assert "TF_VAR_resources" in init_env
    resources_payload = json.loads(init_env["TF_VAR_resources"])
    assert resources_payload["r1"]["configuration"]["admin_password"] == "hunter2"
    # On-disk build artifact is always blanked, unconditionally (docs/work/
    # terraform-variable-precedence.md Phase 3) — never rewritten with its
    # own resolved value, since multiple resx_<type> files all declare the
    # same "resources" variable; TF_VAR_resources above is the sole source.
    on_disk = json.loads((build_path / "infra" / "resx_server.auto.tfvars.json").read_text())
    assert on_disk == {}


def test_deploy_run_merges_multiple_resx_types_into_one_tf_var_resources(tmp_path: Path):
    """docs/design/terraform-tfvars-parity.md: every resx_<type> is really
    the same "resources" Terraform variable regardless of type (confirmed
    against v1's real `_build_resources_by_category()`) — two active
    resource types must merge into one TF_VAR_resources, not clobber one
    another under the same env var name."""
    root = _solution(tmp_path)
    _write(root, "infra/main.tf", "# root module\n")
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  properties:\n    type: local\n    region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: server\n    category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "resource2.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r2\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: storage\n    category: storage\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      targets:\n        - r1\n        - r2\n"
        "  resources:\n    - name: r1\n      resource: r1\n    - name: r2\n      resource: r2\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec: {}\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    envs: list[dict[str, str] | None] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        envs.append(env)
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(base_module, "run_command", _fake_run_command)
        monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

        diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert diagnostics.ok, diagnostics.messages()
    init_env = next(env for env in envs if env is not None)
    assert "TF_VAR_resx_server" not in init_env
    assert "TF_VAR_resx_storage" not in init_env
    resources_payload = json.loads(init_env["TF_VAR_resources"])
    assert set(resources_payload) == {"r1", "r2"}
    assert resources_payload["r1"]["resource_type"] == "server"
    assert resources_payload["r2"]["resource_type"] == "storage"


def test_deploy_run_delivers_flat_categories_as_one_env_var_per_key(tmp_path: Path):
    """docs/design/terraform-tfvars-parity.md: `flags`/`variables` (and
    `workspace`/`properties`/`custom`) are flat, multi-variable categories
    — each top-level key is its own independent real Terraform variable,
    delivered as its own TF_VAR_<key>, never a single TF_VAR_flags/
    TF_VAR_variables blob."""
    root = _solution(tmp_path)
    _write(root, "infra/main.tf", "# root module\n")
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  properties:\n    type: local\n    region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: server\n    category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      targets:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  features:\n    - key: NEW_UI\n      store: constant\n      value: true\n"
        "  variables:\n    - key: REGION\n      store: constant\n      value: westeurope\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    envs: list[dict[str, str] | None] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        envs.append(env)
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(base_module, "run_command", _fake_run_command)
        monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

        diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert diagnostics.ok, diagnostics.messages()
    init_env = next(env for env in envs if env is not None)
    # Per-key delivery, not a category blob.
    assert "TF_VAR_flags" not in init_env
    assert "TF_VAR_variables" not in init_env
    # A plain string value is delivered raw, never JSON-quoted (docs/work/
    # terraform-variable-precedence.md Open Question 7) — only a genuinely
    # complex value (here, the bool) needs `json.loads()` to read back.
    assert json.loads(init_env["TF_VAR_NEW_UI"]) is True
    assert init_env["TF_VAR_REGION"] == "westeurope"
    # workspace's own fixed keys are delivered the same way.
    assert init_env["TF_VAR_workspace_name"] == "main"
    assert "TF_VAR_workspace" not in init_env


def test_deploy_run_rejects_flat_category_key_collision(tmp_path: Path):
    """docs/design/terraform-tfvars-parity.md: two flat categories
    declaring the same top-level key would silently collide as the same
    env var with no ordering guarantee — caught as a hard error instead.
    Here a user-declared variable happens to be named the same as one of
    `workspace`'s own fixed keys."""
    root = _solution(tmp_path)
    _write(root, "infra/main.tf", "# root module\n")
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  properties:\n    type: local\n    region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: server\n    category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      targets:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  variables:\n    - key: workspace_name\n      store: constant\n      value: collides\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert not diagnostics.ok
    assert any("workspace_name" in message for message in diagnostics.messages())


def test_deploy_run_resolves_provider_configuration_tokens_via_tf_var(tmp_path: Path):
    """docs/design/gap_fit_v1.md gap #17 / value-token-resolution.md's "Decision
    (2026-09-29)": a '${var:}'/'${secret:}' token inside a Provider's
    'configuration'/'custom' used to pass 'strata validate' cleanly but
    was never projected into any Terraform artifact at all — silently
    dropped, not even delivered unresolved. Now delivered via
    TF_VAR_platform_providers (the real v1 Terraform variable name,
    docs/design/terraform-tfvars-parity.md — not TF_VAR_providers, the
    file-category name), same broadcast mechanism as resx_<type>/topologies/
    properties/custom/tenant already had."""
    root = _solution(tmp_path)
    _write(root, "infra/main.tf", "# root module\n")
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  properties:\n    type: local\n    region: local\n"
        "  configuration:\n    partner_id: '${var:partner_id}'\n"
        "  custom:\n    cost_center: '${secret:cost_center}'\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: server\n    category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      targets:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  variables:\n    - key: partner_id\n      store: constant\n      value: 'ACME123'\n"
        "  secrets:\n    - key: cost_center\n      store: constant\n      value: platform\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    envs: list[dict[str, str] | None] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        envs.append(env)
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(base_module, "run_command", _fake_run_command)
        monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

        diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert diagnostics.ok
    init_env = next(env for env in envs if env is not None)
    assert "TF_VAR_platform_providers" in init_env
    providers_payload = json.loads(init_env["TF_VAR_platform_providers"])
    assert providers_payload["p1"]["configuration"]["partner_id"] == "ACME123"
    assert providers_payload["p1"]["custom"]["cost_center"] == "platform"
    # On-disk build artifact is blanked entirely (docs/work/
    # terraform-variable-precedence.md Phase 2) — "providers" is a
    # single-variable category (one Terraform variable, "platform_providers",
    # for the whole dict), and a secret-shaped leaf anywhere inside taints
    # the whole variable: the file must not declare it at all, or Terraform's
    # own precedence would let this stale, partially-unresolved file shadow
    # TF_VAR_platform_providers above (secrets are never written to disk,
    # even the resolved non-secret partner_id value next to it).
    on_disk = json.loads((build_path / "infra" / "providers.auto.tfvars.json").read_text())
    assert on_disk == {}


def test_deploy_run_rejects_output_token_in_configuration_payloads(tmp_path: Path):
    """docs/design/gap_fit_v1.md gap #8: '${output:...}' has no ownership mechanism
    for resx_<type>/topologies/properties/custom/tenant (broadcast-only,
    unlike dns/networks/firewalls) — rejected outright, not silently
    ignored or left unresolved."""
    root = _solution(tmp_path)
    _write(root, "infra/main.tf", "# root module\n")
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  properties:\n    type: local\n    region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: server\n    category: compute\n"
        "  configuration:\n    ip: '${output:apply_infra.vm_ip}'\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      targets:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec: {}\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert not diagnostics.ok
    assert "not supported outside dns/networks/firewalls" in diagnostics.messages()[0]


def test_deploy_run_resolves_output_token_in_dns_via_owning_step_targets(tmp_path: Path):
    """docs/design/gap_fit_v1.md gap #12: a DNS document claimed by a step's `targets`
    resolves '${output:...}' using THAT step's own dependency-scoped
    outputs — the worked example from the design discussion (a VM's public
    IP, produced by 'provision-hearth', consumed by 'apply-dns')."""
    root = _solution(tmp_path)
    _write(root, "infra_hearth/main.tf", "# root module\n")
    _write(root, "infra_dns/main.tf", "# root module\n")
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  properties:\n    type: local\n    region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: hearth\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: server\n    category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "dns.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: dns\nmeta:\n  name: public-dns\nspec:\n"
        "  zones:\n    - name: example.com\n      records:\n"
        "        - name: '@'\n          type: A\n          value: '${output:provision-hearth.public_ip}'\n"
        "      default_tags:\n        environment: prd\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  dns_zones:\n    - public-dns\n"
        "  provisioners:\n"
        "    - name: tf_hearth\n      tool: terraform\n      source:\n        source_path: infra_hearth\n"
        "    - name: tf_dns\n      tool: terraform\n      source:\n        source_path: infra_dns\n"
        "  execution:\n"
        "    - name: provision-hearth\n      provisioner: tf_hearth\n      targets:\n        - hearth\n"
        "    - name: apply-dns\n      provisioner: tf_dns\n      targets:\n        - hearth\n        - public-dns\n"
        "      depends_on:\n        - provision-hearth\n"
        "  resources:\n    - name: hearth\n      resource: hearth\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec: {}\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    envs_by_step: dict[str, list[dict[str, str] | None]] = {"provision-hearth": [], "apply-dns": []}
    current_step = {"name": None}

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        if cwd is not None and "infra_hearth" in str(cwd):
            envs_by_step["provision-hearth"].append(env)
        elif cwd is not None and "infra_dns" in str(cwd):
            envs_by_step["apply-dns"].append(env)
        if args[1] == "output":
            return CommandResult(returncode=0, stdout=json.dumps({"public_ip": {"value": "20.1.2.3"}}), stderr="")
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(base_module, "run_command", _fake_run_command)
        monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

        diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert diagnostics.ok, diagnostics.messages()
    hearth_envs = [e for e in envs_by_step["provision-hearth"] if e is not None]
    dns_envs = [e for e in envs_by_step["apply-dns"] if e is not None]
    # provision-hearth doesn't target public-dns — never sees TF_VAR_dns_zones.
    assert all("TF_VAR_dns_zones" not in e for e in hearth_envs)
    # apply-dns targets public-dns — sees the real, resolved output value.
    dns_payload = json.loads(dns_envs[0]["TF_VAR_dns_zones"])
    record_value = dns_payload["public-dns"]["zones"]["example.com"]["records"][0]["value"]
    assert record_value == "20.1.2.3"


def test_deploy_run_rejects_unclaimed_output_token_in_dns(tmp_path: Path):
    """docs/design/gap_fit_v1.md gap #12: a DNS document using '${output:...}' with no
    step naming it in `targets` is a clear, single diagnostic error — not a
    per-step crash or silently inconsistent resolution."""
    root = _solution(tmp_path)
    _write(root, "infra/main.tf", "# root module\n")
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  properties:\n    type: local\n    region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: server\n    category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "dns.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: dns\nmeta:\n  name: public-dns\nspec:\n"
        "  zones:\n    - name: example.com\n      records:\n"
        "        - name: '@'\n          type: A\n          value: '${output:provision-hearth.public_ip}'\n"
        "      default_tags:\n        environment: prd\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  dns_zones:\n    - public-dns\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      targets:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec: {}\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert not diagnostics.ok
    message = diagnostics.messages()[0]
    assert "public-dns" in message
    assert "output" in message
    assert "targets" in message


def _output_chained_solution(tmp_path: Path) -> Path:
    """Two Terraform steps, two provisioners: `apply_infra` (no backend
    tokens) and `apply_apps` (depends_on apply_infra, backend.configuration
    references `${output:apply_infra.vm_ip}`) — the fixture for cross-step
    output context tests."""
    root = _solution(tmp_path)
    _write(root, "infra_a/main.tf", "# root module\n")
    _write(root, "infra_b/main.tf", "# root module\n")
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  properties:\n    type: local\n    region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: server\n    category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n"
        "    - name: tf_infra\n      tool: terraform\n      source:\n        source_path: infra_a\n"
        "    - name: tf_apps\n      tool: terraform\n      source:\n        source_path: infra_b\n"
        "      backend:\n        type: local\n        configuration:\n"
        "          host: ${output:apply_infra.vm_ip}\n"
        "  execution:\n"
        "    - name: apply_infra\n      provisioner: tf_infra\n      targets:\n        - r1\n"
        "    - name: apply_apps\n      provisioner: tf_apps\n      targets:\n        - r1\n"
        "      depends_on:\n        - apply_infra\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec: {}\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    return root


def test_deploy_run_resolves_output_token_from_upstream_step(tmp_path: Path, monkeypatch):
    root = _output_chained_solution(tmp_path)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    calls: list[tuple[list[str], Path | None]] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls.append((list(args), cwd))
        if args[1] == "output":
            if cwd is not None and cwd.name == "infra_a":
                payload = json.dumps({"vm_ip": {"value": "10.0.0.5", "type": "string", "sensitive": False}})
                return CommandResult(returncode=0, stdout=payload, stderr="")
            return CommandResult(returncode=0, stdout="{}", stderr="")
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    diagnostics = deploy_run(_context(root), "app", build_path)

    assert diagnostics.ok, diagnostics.messages()
    apps_init_call = next(
        call for call, cwd in calls if cwd is not None and cwd.name == "infra_b" and call[1] == "init"
    )
    assert "-backend-config" in apps_init_call
    assert "host=10.0.0.5" in apps_init_call


def test_deploy_run_rejects_output_token_from_non_dependency(tmp_path: Path, monkeypatch):
    root = _solution(tmp_path)
    _write(root, "infra/main.tf", "# root module\n")
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  properties:\n    type: local\n    region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: server\n    category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n"
        "    - name: tf_infra\n      tool: terraform\n      source:\n        source_path: infra\n"
        "    - name: tf_apps\n      tool: terraform\n      source:\n        source_path: infra\n"
        "      backend:\n        type: local\n        configuration:\n"
        "          host: ${output:apply_infra.vm_ip}\n"
        "  execution:\n"
        "    - name: apply_infra\n      provisioner: tf_infra\n      targets:\n        - r1\n"
        "    - name: apply_apps\n      provisioner: tf_apps\n      targets:\n        - r2\n"
        "  resources:\n    - name: r1\n      resource: r1\n    - name: r2\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec: {}\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        if args[1] == "output":
            return CommandResult(returncode=0, stdout="{}", stderr="")
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    with pytest.raises(ValueError, match="output:apply_infra.vm_ip"):
        deploy_run(_context(root), "app", build_path)


def test_collect_step_outputs_parses_terraform_json():
    from strata.controllers.deploy_controller import collect_step_outputs
    from strata.integrations.terraform import TerraformIntegration

    class _FakeResult:
        is_successful = True
        stdout = json.dumps(
            {
                "vm_ip": {"value": "10.0.0.5", "type": "string", "sensitive": False},
                "vm_count": {"value": 3, "type": "number", "sensitive": False},
            }
        )

    integration = TerraformIntegration()
    original_output = integration.output
    try:
        integration.output = lambda *a, **kw: _FakeResult()  # type: ignore[method-assign]
        result = collect_step_outputs(integration, Path("/work"), {})
    finally:
        integration.output = original_output  # type: ignore[method-assign]

    assert result == {"vm_ip": "10.0.0.5", "vm_count": "3"}


def test_collect_step_outputs_empty_for_integration_without_output():
    from strata.controllers.deploy_controller import collect_step_outputs
    from strata.integrations.compose import ComposeIntegration

    assert collect_step_outputs(ComposeIntegration(), Path("/work"), {}) == {}


def test_collect_step_outputs_empty_on_failed_output_call():
    from strata.controllers.deploy_controller import collect_step_outputs
    from strata.integrations.terraform import TerraformIntegration

    class _FakeResult:
        is_successful = False
        stdout = ""

    integration = TerraformIntegration()
    integration.output = lambda *a, **kw: _FakeResult()  # type: ignore[method-assign]

    assert collect_step_outputs(integration, Path("/work"), {}) == {}


# ---------------------------------------------------------------------------
# collect_step_outputs() auth/resolved_values (docs/design/gitops-integration.md
# Implementation Plan Phase 5) — must stay a no-op call shape for every tool
# except an actual GitOps step, found on review: TerraformIntegration.output()
# has no `**kwargs: Any` of its own, so unconditionally passing these two
# kwargs would break every real Terraform deploy's output collection.
# ---------------------------------------------------------------------------


def test_collect_step_outputs_without_auth_calls_output_with_no_extra_kwargs():
    from strata.controllers.deploy_controller import collect_step_outputs
    from strata.integrations.terraform import TerraformIntegration

    captured: dict[str, object] = {}

    class _FakeResult:
        is_successful = True
        stdout = "{}"

    def _fake_output(path, **kwargs):
        captured.update(kwargs)
        return _FakeResult()

    integration = TerraformIntegration()
    integration.output = _fake_output  # type: ignore[method-assign]

    collect_step_outputs(integration, Path("/work"), {})

    assert "auth" not in captured
    assert "resolved_values" not in captured


def test_collect_step_outputs_with_auth_forwards_both_kwargs():
    from strata.controllers.deploy_controller import collect_step_outputs
    from strata.integrations.gitops import ArgoCDIntegration
    from strata.models.auth_models import AuthenticationModel, CLIAuthenticationModel

    captured: dict[str, object] = {}

    class _FakeResult:
        is_successful = True
        stdout = "{}"

    def _fake_output(path, **kwargs):
        captured.update(kwargs)
        return _FakeResult()

    integration = ArgoCDIntegration()
    integration.output = _fake_output  # type: ignore[method-assign]
    auth = AuthenticationModel(method="cli", cli=CLIAuthenticationModel())

    collect_step_outputs(integration, Path("/work"), {}, auth=auth, resolved_values={"k": "v"})

    assert captured["auth"] is auth
    assert captured["resolved_values"] == {"k": "v"}


def test_collect_step_outputs_resolved_values_alone_still_forwards_both():
    """Only one of the two being set is enough to trigger forwarding —
    matches `collect_step_outputs()`'s own `if auth is not None or
    resolved_values is not None` condition."""
    from strata.controllers.deploy_controller import collect_step_outputs
    from strata.integrations.gitops import ArgoCDIntegration

    captured: dict[str, object] = {}

    class _FakeResult:
        is_successful = True
        stdout = "{}"

    def _fake_output(path, **kwargs):
        captured.update(kwargs)
        return _FakeResult()

    integration = ArgoCDIntegration()
    integration.output = _fake_output  # type: ignore[method-assign]

    collect_step_outputs(integration, Path("/work"), {}, resolved_values={"k": "v"})

    assert captured["auth"] is None
    assert captured["resolved_values"] == {"k": "v"}


def test_collect_step_outputs_real_terraform_output_tolerates_auth_kwargs():
    """Regression test for the exact bug found on review: real
    `TerraformIntegration.output()` must not raise `TypeError` when `auth`/
    `resolved_values` are passed (its own new `**kwargs: Any` absorbs them)
    — this would have silently degraded to `{}` before that fix."""
    from strata.controllers.deploy_controller import collect_step_outputs
    from strata.integrations.terraform import TerraformIntegration
    from strata.models.auth_models import AuthenticationModel, CLIAuthenticationModel

    class _FakeResult:
        is_successful = True
        stdout = json.dumps({"vm_ip": {"value": "10.0.0.5"}})

    integration = TerraformIntegration()
    integration.output = lambda *a, **kw: _FakeResult()  # type: ignore[method-assign]
    auth = AuthenticationModel(method="cli", cli=CLIAuthenticationModel())

    result = collect_step_outputs(integration, Path("/work"), {}, auth=auth, resolved_values={"k": "v"})

    assert result == {"vm_ip": "10.0.0.5"}


def test_upstream_step_names_is_transitive():
    from strata.controllers.deploy_controller import _upstream_step_names
    from strata.models.provisioning_model import ProvisioningStepModel

    steps = [
        ProvisioningStepModel(name="a", provisioner="p", targets=["r1"]),
        ProvisioningStepModel(name="b", provisioner="p", targets=["r1"], depends_on=["a"]),
        ProvisioningStepModel(name="c", provisioner="p", targets=["r1"], depends_on=["b"]),
    ]

    assert _upstream_step_names("c", steps) == {"a", "b"}
    assert _upstream_step_names("a", steps) == set()


# ---------------------------------------------------------------------------
# output.template's actual render (docs/design/deploy-command.md
# Implementation Plan phase 8) — build run only validates it; deploy run
# renders it for real, with fully-resolved values, before init() runs.
# ---------------------------------------------------------------------------


def _output_template_solution(tmp_path: Path) -> Path:
    root = _solution(tmp_path)
    _write(root, "infra/main.tf", "# root module\n")
    _write(root, "variables.json.j2", '{"region": "{{ variables.REGION }}"}')
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  properties:\n    type: local\n    region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: server\n    category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "      output:\n        template: variables.json.j2\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      targets:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  variables:\n    - key: REGION\n      store: constant\n      value: westeurope\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    return root


def test_deploy_run_renders_output_template_before_init(tmp_path: Path, _capture):
    root = _output_template_solution(tmp_path)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)
    # build run only validates output.template — confirms nothing was written yet.
    assert not (build_path / "infra" / "variables.json").exists()

    diagnostics = deploy_run(_context(root), "app", build_path)

    assert diagnostics.ok, diagnostics.messages()
    rendered = build_path / "infra" / "variables.json"
    assert rendered.exists()
    assert rendered.read_text() == '{"region": "westeurope"}'
    # rendered before init, not after
    assert _capture[0][1] == "init"


def test_deploy_run_reports_a_template_render_failure(tmp_path: Path, _capture):
    root = _output_template_solution(tmp_path)
    _write(root, "variables.json.j2", "{{ graph.workspace.meta.ghost_field }}")
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    diagnostics = deploy_run(_context(root), "app", build_path)

    assert not diagnostics.ok
    assert "failed to render" in diagnostics.messages()[0]
    assert _capture == []  # never reached init


# ---------------------------------------------------------------------------
# Helm/Compose namespace dispatch (docs/design/gap_fit_v1.md gap #13,
# docs/design/deploy-command.md) - a "container"-capable step deploys the
# namespace(s) its own `targets` names directly, bypassing Terraform's
# init/validate/plan/apply sequence entirely.
# ---------------------------------------------------------------------------


def _helm_solution(tmp_path: Path) -> Path:
    """One Terraform infra step plus one Helm-provisioner step targeting a
    namespace - real dispatch evidence for gap #13 (v1's real
    `DeployerFactory.resolve_type()`: the step's own provisioner picks the
    tool, never inferred from namespace linkage)."""
    root = _terraform_solution(tmp_path)
    _write(root, "charts/authentik/Chart.yaml", "name: authentik\n")
    _write(
        root,
        "module.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: module\nmeta:\n  name: authentik\nspec:\n"
        "  source:\n    source_path: charts/authentik\n  type: helm\n"
        "  default_labels:\n    app: authentik\n"
        "  services:\n    - name: server\n",
    )
    _write(
        root,
        "namespace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: namespace\nmeta:\n  name: apps\nspec:\n"
        "  default_labels:\n    app: apps\n"
        "  modules:\n    - name: auth\n      module: authentik\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  namespaces:\n    - apps\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "    - name: helm_main\n      tool: helm\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      targets:\n        - r1\n"
        "      scope: infra\n"
        "    - name: deploy_apps\n      provisioner: helm_main\n      targets:\n        - apps\n"
        "      scope: apps\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    return root


def test_deploy_run_dispatches_container_capable_step_to_deploy_namespace(tmp_path: Path, _capture):
    root = _helm_solution(tmp_path)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert diagnostics.ok, diagnostics.messages()

    # Terraform step ran its normal lifecycle, unchanged.
    tf_commands = [call[1] for call in _capture if call[0] == "terraform"]
    assert tf_commands == ["init", "validate", "plan", "apply", "output"]

    # The Helm step never goes through init/validate/plan at all - straight
    # to one `helm upgrade` per namespace module.
    helm_calls = [call for call in _capture if call[0] == "helm"]
    assert len(helm_calls) == 1
    module_dir = build_path / "apps" / "auth"
    assert helm_calls[0] == [
        "helm",
        "upgrade",
        "--install",
        "--create-namespace",
        "--wait",
        "--atomic",
        "--timeout",
        "5m",
        "--namespace",
        "apps",
        "-f",
        str(module_dir / "values.yaml"),
        "auth",
        str(module_dir),
    ]


def test_deploy_run_dry_run_previews_container_capable_step_via_plan(tmp_path: Path, _capture):
    """A container-capable step gets a real preview under `--dry-run` too:
    `helm upgrade --dry-run --install` via `deploy_namespace()`'s
    `plan_or_warn()` call, never a real `helm upgrade`/`deploy()`."""
    root = _helm_solution(tmp_path)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    diagnostics = deploy_run(_context(root), "app", build_path, force=True, dry_run=True)

    assert diagnostics.ok, diagnostics.messages()

    # Terraform step: real init/validate/plan, stops before apply.
    tf_commands = [call[1] for call in _capture if call[0] == "terraform"]
    assert tf_commands == ["init", "validate", "plan"]

    # Helm step: one real preview call, `--dry-run`, never `deploy()`'s
    # `--install --create-namespace --wait --atomic --timeout 5m` shape.
    helm_calls = [call for call in _capture if call[0] == "helm"]
    assert len(helm_calls) == 1
    module_dir = build_path / "apps" / "auth"
    assert helm_calls[0] == [
        "helm",
        "upgrade",
        "--dry-run",
        "--install",
        "--namespace",
        "apps",
        "-f",
        str(module_dir / "values.yaml"),
        "auth",
        str(module_dir),
    ]


def test_deploy_run_filters_helm_steps_by_scope_like_terraform_steps(tmp_path: Path, _capture):
    root = _helm_solution(tmp_path)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    steps: list[str] = []
    deploy_run(_context(root), "app", build_path, force=True, scope="apps", on_step=steps.append)

    assert any("deploy_apps" in s for s in steps)
    assert not any("apply_infra" in s for s in steps)
    assert [call[0] for call in _capture] == ["helm"]


def test_deploy_run_container_step_with_no_matching_namespace_target_errors(tmp_path: Path, _capture):
    root = _helm_solution(tmp_path)
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  namespaces:\n    - apps\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "    - name: helm_main\n      tool: helm\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      targets:\n        - r1\n"
        "      scope: infra\n"
        "    - name: deploy_apps\n      provisioner: helm_main\n      targets:\n        - r1\n"
        "      depends_on:\n        - apply_infra\n"
        "      scope: apps\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert not diagnostics.ok
    assert "names no namespace" in diagnostics.messages()[0]


def _compose_solution(tmp_path: Path) -> Path:
    """Same shape as `_helm_solution()`, but `tool: compose` - proves the
    orchestrator's container-capability branch is genuinely tool-agnostic
    (docs/design/gap_fit_v1.md gap #13), not accidentally Helm-specific."""
    root = _terraform_solution(tmp_path)
    _write(root, "services/portainer/.keep", "")
    _write(
        root,
        "module.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: module\nmeta:\n  name: portainer\nspec:\n"
        "  source:\n    source_path: services/portainer\n  type: compose\n"
        "  default_labels:\n    app: portainer\n"
        "  services:\n    - name: portainer\n      image: portainer/portainer-ce\n",
    )
    _write(
        root,
        "namespace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: namespace\nmeta:\n  name: apps\nspec:\n"
        "  default_labels:\n    app: apps\n"
        "  modules:\n    - name: mgmt\n      module: portainer\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  namespaces:\n    - apps\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "    - name: compose_main\n      tool: compose\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      targets:\n        - r1\n"
        "      scope: infra\n"
        "    - name: deploy_apps\n      provisioner: compose_main\n      targets:\n        - apps\n"
        "      scope: apps\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    return root


def test_deploy_run_dispatches_compose_step_to_deploy_namespace(tmp_path: Path, _capture):
    root = _compose_solution(tmp_path)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert diagnostics.ok, diagnostics.messages()

    # Terraform step ran its normal lifecycle, unchanged.
    tf_commands = [call[1] for call in _capture if call[0] == "terraform"]
    assert tf_commands == ["init", "validate", "plan", "apply", "output"]

    # The Compose step never goes through init/validate/plan at all -
    # straight to one `docker stack deploy` for the whole namespace.
    docker_calls = [call for call in _capture if call[0] == "docker"]
    assert len(docker_calls) == 1
    compose_file = build_path / "apps" / "docker-compose.yml"
    assert docker_calls[0] == [
        "docker",
        "stack",
        "deploy",
        "--with-registry-auth",
        "-c",
        str(compose_file),
        "apps",
    ]


def test_deploy_run_does_not_leak_tf_var_env_into_container_capable_steps(tmp_path: Path):
    """docs/design/gap_fit_v1.md: Helm/Compose declare no `ENV_VAR_PREFIX` (base
    default `None`) - `tf_var_env()` now returns `{}` for them, so a
    container-capable step's own subprocess never receives TF_VAR_-
    prefixed secrets meant for Terraform."""
    root = _helm_solution(tmp_path)
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  secrets:\n    - key: db_password\n      store: constant\n      value: hunter2\n",
    )
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    calls: list[tuple[str, dict[str, str] | None]] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls.append((args[0], env))
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(base_module, "run_command", _fake_run_command)
        monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

        diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert diagnostics.ok
    terraform_env = next(env for command, env in calls if command == "terraform")
    assert terraform_env is not None and terraform_env.get("TF_VAR_db_password") == "hunter2"
    helm_env = next(env for command, env in calls if command == "helm")
    assert not helm_env
