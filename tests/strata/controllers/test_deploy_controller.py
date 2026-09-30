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
    — never rewritten into the on-disk .auto.tfvars.json."""
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
    # On-disk build artifact stays literal/unresolved — never rewritten.
    # docs/design/terraform-tfvars-parity.md: the file's real v1 content is
    # wrapped under "dns_zones", the real Terraform variable name.
    on_disk = json.loads((build_path / "infra" / "dns.auto.tfvars.json").read_text())
    assert on_disk["dns_zones"]["public-dns"]["zones"]["example.com"]["records"][0]["value"] == "${var:public_ip}"
    # No networks/firewalls documents in this workspace — no TF_VAR set.
    assert "TF_VAR_networks" not in init_env
    assert "TF_VAR_firewalls" not in init_env


def test_deploy_run_resolves_configuration_payloads_tokens_via_tf_var(tmp_path: Path):
    """docs/design/gap_fit_v1.md gap #8 (Terraform-side refinement) / value-token-
    resolution.md Full Solution Phase 6: a '${var:KEY}' inside a resource's
    'configuration' is delivered via TF_VAR_resources (docs/design/
    terraform-tfvars-parity.md: every resx_<type> merges into the one real
    "resources" Terraform variable, not a separate TF_VAR_resx_<type> per
    type) — broadcast to every step, never rewritten into the on-disk
    .auto.tfvars.json."""
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
    # On-disk build artifact stays literal/unresolved — never rewritten.
    on_disk = json.loads((build_path / "infra" / "resx_server.auto.tfvars.json").read_text())
    assert on_disk["resources"]["r1"]["configuration"]["admin_password"] == "${secret:vm_admin_password}"


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
    assert json.loads(init_env["TF_VAR_NEW_UI"]) is True
    assert json.loads(init_env["TF_VAR_REGION"]) == "westeurope"
    # workspace's own fixed keys are delivered the same way.
    assert json.loads(init_env["TF_VAR_workspace_name"]) == "main"
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
    # On-disk build artifact stays literal/unresolved — never rewritten.
    # docs/design/terraform-tfvars-parity.md: the file's real v1 content is
    # wrapped under "platform_providers", the real Terraform variable name.
    on_disk = json.loads((build_path / "infra" / "providers.auto.tfvars.json").read_text())
    assert on_disk["platform_providers"]["p1"]["configuration"]["partner_id"] == "${var:partner_id}"
    assert on_disk["platform_providers"]["p1"]["custom"]["cost_center"] == "${secret:cost_center}"


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
