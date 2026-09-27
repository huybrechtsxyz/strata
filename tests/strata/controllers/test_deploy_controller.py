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


def test_deploy_run_dry_run_never_calls_run_command(tmp_path: Path, _capture):
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    diagnostics = deploy_run(_context(root), "app", build_path, force=True, dry_run=True)

    assert diagnostics.ok
    assert _capture == []


def test_deploy_run_filters_by_stage(tmp_path: Path, _capture):
    root = _terraform_solution(tmp_path, second_step=True)
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)

    steps: list[str] = []
    deploy_run(
        _context(root), "app", build_path, force=True, stage="apply_infra", on_step=steps.append
    )

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

    assert tf_var_env(resolved) == {"TF_VAR_db_password": "hunter2", "TF_VAR_api_key": "abc123"}


def test_tf_var_env_empty_when_no_values_resolved():
    resolved = ValueResolution(deployment="app", values={})

    assert tf_var_env(resolved) == {}


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
    assert all(env == {"TF_VAR_db_password": "hunter2"} for env in envs)


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
    apps_init_call = next(call for call, cwd in calls if cwd is not None and cwd.name == "infra_b" and call[1] == "init")
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
