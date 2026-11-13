#!/usr/bin/env python3
"""Tests for `deployment_steps.deployment_steps()` (docs/design/deploy-steps-command.md).

Real solutions on disk, opened with `open_solution()`; nothing is built or deployed. `--check-tools` tests patch the
tool lookup, as `test_integration_readiness.py` does.
"""

from pathlib import Path

import pytest

from strata.controllers.deployment_steps import deployment_steps
from strata.controllers.solution_context import open_solution
from strata.integrations import base as base_module
from strata.integrations.terraform import TerraformIntegration
from strata.utils.errors import UsageError

MANIFEST = "apiVersion: strata.huybrechts.xyz/v2\nkind: solution\nmeta:\n  name: test-solution\nspec: {}\n"

TF = "    - name: tf_{name}\n      tool: terraform\n      source:\n        source_path: infra_{name}\n"

# A diamond: a; b and c after a; d after b and c. Each step owns its own resource, so no two share a target.
DIAMOND = (
    "    - name: a\n      provisioner: tf_a\n      resources: [ra]\n      scope: infra\n"
    "    - name: b\n      provisioner: tf_b\n      resources: [rb]\n      depends_on: [a]\n      scope: infra\n"
    "    - name: c\n      provisioner: tf_c\n      resources: [rc]\n      depends_on: [a]\n      scope: apps\n"
    "    - name: d\n      provisioner: tf_d\n      resources: [rd]\n      depends_on: [b, c]\n"
)
RESOURCES = "".join(f"    - name: r{x}\n      resource: r1\n" for x in "abcd")


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _solution(
    tmp_path: Path,
    *,
    provisioners: str | None = None,
    execution: str = DIAMOND,
    resources: str = RESOURCES,
    workspace_extra: str = "",
    deployments: str | None = None,
    manifest: str = MANIFEST,
    files: dict[str, str] | None = None,
) -> Path:
    root = tmp_path / "sln"
    _write(root, "strata.yaml", manifest)
    for relative, content in {**{f"infra_{x}/main.tf": "# module\n" for x in "abcd"}, **(files or {})}.items():
        _write(root, relative, content)
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  type: local\n  region: local\n",
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
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        f"{workspace_extra}"
        "  providers:\n    - p1\n"
        f"  provisioners:\n{provisioners if provisioners is not None else ''.join(TF.format(name=x) for x in 'abcd')}"
        f"  execution:\n{execution}"
        f"  resources:\n{resources}",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec: {}\n",
    )
    _write(
        root,
        "deployment.yaml",
        deployments
        or (
            "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
            "  workspace: main\n  environments:\n    - prd\n"
        ),
    )
    return root


def _context(root: Path):
    return open_solution(root).require_valid()


def _by_name(result) -> dict:
    return {row.name: row for row in result.steps}


# --- order, level, direction -----------------------------------------------------------------------------------


def test_steps_are_listed_in_dependency_order_with_levels(tmp_path):
    result = deployment_steps(_context(_solution(tmp_path)), "app")

    assert [row.name for row in result.steps] == ["a", "b", "c", "d"]
    assert [row.order for row in result.steps] == [0, 1, 2, 3]
    assert {row.name: row.level for row in result.steps} == {"a": 0, "b": 1, "c": 1, "d": 2}
    assert _by_name(result)["d"].depends_on == ["b", "c"]
    assert result.direction == "apply"
    assert result.workspace == "main"
    assert result.partial is False
    assert result.diagnostics.ok


def test_destroy_lists_teardown_order_and_levels(tmp_path):
    result = deployment_steps(_context(_solution(tmp_path)), "app", destroy=True)

    assert [row.name for row in result.steps] == ["d", "c", "b", "a"]
    assert [row.order for row in result.steps] == [0, 1, 2, 3]
    assert {row.name: row.level for row in result.steps} == {"d": 0, "b": 1, "c": 1, "a": 2}
    assert result.direction == "destroy"


def test_order_and_level_do_not_change_with_a_filter(tmp_path):
    result = deployment_steps(_context(_solution(tmp_path)), "app", stages=["d"])

    assert [(row.name, row.order, row.level) for row in result.steps] == [("d", 3, 2)]
    assert result.steps[0].depends_on == ["b", "c"]


# --- selection ---------------------------------------------------------------------------------------------------


def test_repeated_filters_select_like_deploy_run(tmp_path):
    context = _context(_solution(tmp_path))

    assert [row.name for row in deployment_steps(context, "app", scopes=["apps", "infra"]).steps] == ["a", "b", "c"]
    assert [row.name for row in deployment_steps(context, "app", stages=["a", "c"], scopes=["apps"]).steps] == ["c"]


def test_an_empty_selection_is_a_result_with_the_unmatched_values(tmp_path):
    result = deployment_steps(_context(_solution(tmp_path)), "app", stages=["ghost"], scopes=["db"])

    assert result.steps == []
    assert result.scopes == []
    assert result.unmatched_stages == ["ghost"]
    assert result.unmatched_scopes == ["db"]
    assert result.diagnostics.ok
    assert result.to_data()["unmatched"] == {"stages": ["ghost"], "scopes": ["db"]}


# --- scopes ------------------------------------------------------------------------------------------------------


def test_scopes_are_ordered_with_their_dependencies(tmp_path):
    result = deployment_steps(_context(_solution(tmp_path)), "app")

    assert [(s.scope, s.steps, s.depends_on, s.orderable) for s in result.scopes] == [
        ("infra", ["a", "b"], [], True),
        ("apps", ["c"], ["infra"], True),
        (None, ["d"], ["infra", "apps"], True),
    ]


def test_scope_dependencies_reverse_when_destroying(tmp_path):
    result = deployment_steps(_context(_solution(tmp_path)), "app", destroy=True)

    assert [(s.scope, s.depends_on) for s in result.scopes] == [
        (None, []),
        ("apps", [None]),
        ("infra", [None, "apps"]),
    ]


def test_scope_dependencies_include_scopes_outside_the_selection(tmp_path):
    result = deployment_steps(_context(_solution(tmp_path)), "app", scopes=["apps"])

    assert [(s.scope, s.depends_on) for s in result.scopes] == [("apps", ["infra"])]


def test_a_scope_cycle_is_listed_unorderable_with_a_warning(tmp_path):
    execution = (
        "    - name: net\n      provisioner: tf_a\n      resources: [ra]\n      scope: infra\n"
        "    - name: app\n      provisioner: tf_b\n      resources: [rb]\n      depends_on: [net]\n      scope: apps\n"
        "    - name: dns\n      provisioner: tf_c\n      resources: [rc]\n      depends_on: [app]\n      scope: infra\n"
    )
    result = deployment_steps(_context(_solution(tmp_path, execution=execution)), "app")

    assert [(s.scope, s.orderable) for s in result.scopes] == [("infra", False), ("apps", False)]
    assert result.diagnostics.ok
    assert {w.code for w in result.diagnostics.warnings} == {"scope_cycle"}
    assert len(result.diagnostics.warnings) == 2


# --- resolution --------------------------------------------------------------------------------------------------


def test_a_workspace_inherited_through_extends_is_used(tmp_path):
    deployments = (
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: base\nspec:\n"
        "  partial: true\n  workspace: main\n"
        "---\n"
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  extends: base\n  environments:\n    - prd\n"
    )
    context = _context(_solution(tmp_path, deployments=deployments))

    child = deployment_steps(context, "app")
    assert child.workspace == "main"
    assert [row.name for row in child.steps] == ["a", "b", "c", "d"]
    assert child.partial is False
    assert deployment_steps(context, "base").partial is True


def test_an_unknown_deployment_is_a_usage_error(tmp_path):
    with pytest.raises(UsageError, match="No deployment named 'ghost'"):
        deployment_steps(_context(_solution(tmp_path)), "ghost")


def test_a_deployment_without_workspace_is_a_usage_error(tmp_path):
    deployments = (
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: base\nspec:\n  partial: true\n"
        "---\n"
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n"
    )
    with pytest.raises(UsageError, match="has no workspace"):
        deployment_steps(_context(_solution(tmp_path, deployments=deployments)), "base")


# --- per-step fields ---------------------------------------------------------------------------------------------


def test_the_tool_is_read_from_the_solution(tmp_path):
    row = deployment_steps(_context(_solution(tmp_path)), "app").steps[0]

    assert row.provisioner == "tf_a"
    assert row.integration.type == "terraform"
    assert row.integration.transport == "cli"
    assert row.integration.command == "terraform"
    assert row.integration.required_version is None
    assert row.readiness is None


def test_a_terraform_step_can_plan_apply_and_destroy(tmp_path):
    capabilities = deployment_steps(_context(_solution(tmp_path)), "app").steps[0].capabilities

    assert (capabilities.saved_plan, capabilities.destroy, capabilities.container) == (True, True, False)


@pytest.mark.parametrize(("destroy_args", "can_destroy"), [("", False), ("        destroy_args: [--down]\n", True)])
def test_a_script_step_has_no_saved_plan(tmp_path, destroy_args, can_destroy):
    provisioners = (
        "    - name: script_main\n      tool: script\n      source:\n        source_path: scripts\n"
        f"      script:\n        file: deploy.py\n{destroy_args}"
    )
    execution = "    - name: run\n      provisioner: script_main\n      resources: [ra]\n"
    root = _solution(
        tmp_path, provisioners=provisioners, execution=execution, files={"scripts/deploy.py": "print('hi')\n"}
    )
    capabilities = deployment_steps(_context(root), "app").steps[0].capabilities

    assert capabilities.saved_plan is False
    assert capabilities.destroy is can_destroy
    assert capabilities.container is False


def test_a_gitops_step_has_no_saved_plan(tmp_path):
    manifest = (
        "apiVersion: strata.huybrechts.xyz/v2\nkind: solution\nmeta:\n  name: test-solution\nspec:\n"
        "  remotes:\n    - name: gitops-config\n      type: git\n"
        "      url: https://example.com/org/gitops-config.git\n      reference: main\n"
    )
    provisioners = (
        "    - name: forge_sync\n      tool: argocd\n      gitops:\n        remote: gitops-config\n"
        "        output_file: apps/forge/values.yaml\n      output:\n        template: templates/forge.yaml.j2\n"
    )
    execution = "    - name: sync\n      provisioner: forge_sync\n      resources: [ra]\n"
    root = _solution(
        tmp_path,
        manifest=manifest,
        provisioners=provisioners,
        execution=execution,
        files={"templates/forge.yaml.j2": "replicas: 1\n"},
    )
    row = deployment_steps(_context(root), "app").steps[0]

    assert row.capabilities.saved_plan is False
    assert row.capabilities.container is False


def test_a_helm_step_takes_the_container_path(tmp_path):
    provisioners = "    - name: helm_main\n      tool: helm\n      source:\n        source_path: infra_a\n"
    execution = "    - name: apps\n      provisioner: helm_main\n      namespaces:\n        - apps\n"
    root = _solution(
        tmp_path,
        provisioners=provisioners,
        execution=execution,
        workspace_extra="  sbom:\n    render_charts: false\n  namespaces:\n    - apps\n",
        files={
            "charts/authentik/Chart.yaml": "name: authentik\n",
            "module.yaml": (
                "apiVersion: strata.huybrechts.xyz/v2\nkind: module\nmeta:\n  name: authentik\nspec:\n"
                "  source:\n    source_path: charts/authentik\n  type: helm\n"
                "  default_labels:\n    app: authentik\n  services:\n    - name: server\n"
            ),
            "namespace.yaml": (
                "apiVersion: strata.huybrechts.xyz/v2\nkind: namespace\nmeta:\n  name: apps\nspec:\n"
                "  default_labels:\n    app: apps\n  modules:\n    - name: auth\n      module: authentik\n"
            ),
        },
    )
    row = deployment_steps(_context(root), "app").steps[0]

    assert (row.capabilities.saved_plan, row.capabilities.container) == (True, True)
    assert row.effective == {"resources": [], "namespaces": ["apps"]}


def test_targets_are_declared_and_effective_expands_topologies_without_disabled_resources(tmp_path):
    workspace_extra = (
        "  topology:\n    - name: core\n      type: kubernetes\n      components:\n"
        "        - resource: rb\n        - resource: rc\n"
    )
    resources = RESOURCES + "    - name: re\n      resource: r1\n      enabled: false\n"
    execution = "    - name: net\n      provisioner: tf_a\n      resources: [ra]\n      topologies: [core]\n"
    root = _solution(tmp_path, execution=execution, resources=resources, workspace_extra=workspace_extra)
    row = deployment_steps(_context(root), "app").steps[0]

    assert row.targets["resources"] == ["ra"]
    assert row.targets["topologies"] == ["core"]
    assert row.targets["namespaces"] == []
    assert row.effective == {"resources": ["ra", "rb", "rc"], "namespaces": []}


def test_stage_parameters_show_only_what_strata_applies(tmp_path):
    deployments = (
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n"
        "  stages:\n    - step: a\n      description: The network\n      enabled: false\n      on_failure: continue\n"
        "      timeouts:\n        plan: 900\n        apply: 1800\n"
    )
    result = deployment_steps(_context(_solution(tmp_path, deployments=deployments)), "app")
    rows = _by_name(result)

    assert rows["a"].description == "The network"
    assert rows["a"].timeouts == {"setup": None, "check": None, "plan": 900, "apply": 1800, "destroy": None}
    assert rows["b"].timeouts is None
    # `enabled: false` is not applied by strata yet, so the step is still listed and the field is not shown.
    step_data = result.to_data()["steps"][0]
    assert "enabled" not in step_data
    assert "on_failure" not in step_data


def test_to_data_matches_the_contract(tmp_path):
    data = deployment_steps(_context(_solution(tmp_path)), "app", scopes=["apps"]).to_data()

    assert set(data) == {
        "deployment",
        "workspace",
        "partial",
        "direction",
        "filters",
        "unmatched",
        "tools_checked",
        "steps",
        "scopes",
    }
    assert data["filters"] == {"stages": [], "scopes": ["apps"]}
    assert set(data["steps"][0]) == {
        "name",
        "scope",
        "order",
        "level",
        "depends_on",
        "provisioner",
        "integration",
        "readiness",
        "capabilities",
        "targets",
        "effective",
        "description",
        "timeouts",
    }
    assert data["scopes"] == [{"scope": "apps", "steps": ["c"], "depends_on": ["infra"], "orderable": True}]


# --- --check-tools -----------------------------------------------------------------------------------------------


def _patch_terraform(monkeypatch, *, available: bool, version: str | None = "1.9.8") -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}" if available else None)

    def _version(self):
        calls.append(self.name)
        return version

    monkeypatch.setattr(TerraformIntegration, "get_version", _version)
    return calls


def _with_terraform_version(tmp_path: Path, required: str) -> Path:
    files = {
        "integration.yaml": (
            "apiVersion: strata.huybrechts.xyz/v2\nkind: integration\nmeta:\n  name: terraform\nspec:\n"
            f"  type: terraform\n  version: '{required}'\n"
        )
    }
    return _solution(tmp_path, files=files)


def test_check_tools_reports_a_ready_tool_once_per_tool(tmp_path, monkeypatch):
    calls = _patch_terraform(monkeypatch, available=True)
    result = deployment_steps(_context(_with_terraform_version(tmp_path, ">=1.5")), "app", check_tools=True)

    assert result.diagnostics.ok, result.diagnostics.messages()
    assert all(row.readiness is not None and row.readiness.ok for row in result.steps)
    assert result.steps[0].readiness.installed_version == "1.9.8"
    assert result.steps[0].integration.required_version == ">=1.5"
    assert len(calls) == 1
    assert result.to_data()["tools_checked"] is True


def test_check_tools_fails_on_a_missing_tool(tmp_path, monkeypatch):
    _patch_terraform(monkeypatch, available=False)
    result = deployment_steps(_context(_solution(tmp_path)), "app", check_tools=True)

    assert not result.diagnostics.ok
    assert {e.code for e in result.diagnostics.errors} == {"integration_unavailable"}
    assert len(result.diagnostics.errors) == 1  # one tool, reported once
    assert all(row.readiness is not None and not row.readiness.ok for row in result.steps)


def test_check_tools_fails_on_a_wrong_version(tmp_path, monkeypatch):
    _patch_terraform(monkeypatch, available=True, version="1.4.0")
    result = deployment_steps(_context(_with_terraform_version(tmp_path, ">=1.5")), "app", check_tools=True)

    assert {e.code for e in result.diagnostics.errors} == {"integration_version_mismatch"}


def test_without_check_tools_no_tool_is_contacted(tmp_path, monkeypatch):
    def _fail(*args, **kwargs):
        raise AssertionError("no tool lookup without --check-tools")

    monkeypatch.setattr(base_module.shutil, "which", _fail)
    monkeypatch.setattr(TerraformIntegration, "get_version", _fail)
    result = deployment_steps(_context(_solution(tmp_path)), "app")

    assert all(row.readiness is None for row in result.steps)
