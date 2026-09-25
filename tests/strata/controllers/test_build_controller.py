#!/usr/bin/env python3
"""Tests for `build_controller` (ADR-0022) — `ordered_by_depends_on()`,
`find_provisioner()`, `build_resolved_workspace_graph()`, and the full
`build_run()` orchestrator end to end."""

from pathlib import Path

import pytest
import yaml

from strata.controllers.build_controller import (
    BuildCleanError,
    build_resolved_workspace_graph,
    build_run,
    find_provisioner,
    ordered_by_depends_on,
)
from strata.controllers.solution_context import open_solution
from strata.controllers.solution_controller import DocumentIndex, DocumentRef, IndexEntry
from strata.models.common_models import PlatformKind, SourceModel
from strata.models.provider_model import ProviderMetaModel, ProviderModel, ProviderPropertiesModel, ProviderSpecModel
from strata.models.provisioning_model import ProvisionerModel, ProvisioningStepModel
from strata.models.workspace_model import WorkspaceMetaModel, WorkspaceModel, WorkspaceSpecModel
from strata.utils.errors import UsageError

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


# ---------------------------------------------------------------------------
# ordered_by_depends_on()
# ---------------------------------------------------------------------------


def _step(name: str, *, provisioner: str = "p", depends_on: list[str] | None = None) -> ProvisioningStepModel:
    return ProvisioningStepModel(name=name, provisioner=provisioner, targets=["r1"], depends_on=depends_on)


def test_ordered_by_depends_on_respects_dependency_order():
    steps = [_step("b", depends_on=["a"]), _step("a")]
    ordered = ordered_by_depends_on(steps)
    assert [s.name for s in ordered] == ["a", "b"]


def test_ordered_by_depends_on_preserves_independent_steps_original_order():
    steps = [_step("x"), _step("y")]
    ordered = ordered_by_depends_on(steps)
    assert [s.name for s in ordered] == ["x", "y"]


def test_ordered_by_depends_on_handles_a_diamond():
    steps = [_step("d", depends_on=["b", "c"]), _step("b", depends_on=["a"]), _step("c", depends_on=["a"]), _step("a")]
    ordered = [s.name for s in ordered_by_depends_on(steps)]
    assert ordered.index("a") < ordered.index("b") < ordered.index("d")
    assert ordered.index("a") < ordered.index("c") < ordered.index("d")


# ---------------------------------------------------------------------------
# find_provisioner()
# ---------------------------------------------------------------------------


def _workspace_with_provisioners(*provisioners: ProvisionerModel) -> WorkspaceModel:
    return WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(providers=["p1"], provisioners=list(provisioners)),
    )


def test_find_provisioner_returns_the_named_one():
    prov = ProvisionerModel(name="tf_main", tool="terraform", source=SourceModel(source_path="infra"))
    workspace = _workspace_with_provisioners(prov)
    assert find_provisioner(workspace, "tf_main") is prov


def test_find_provisioner_raises_for_an_unknown_name():
    workspace = _workspace_with_provisioners(
        ProvisionerModel(name="tf_main", tool="terraform", source=SourceModel(source_path="infra"))
    )
    with pytest.raises(UsageError, match="ghost"):
        find_provisioner(workspace, "ghost")


# ---------------------------------------------------------------------------
# build_resolved_workspace_graph()
# ---------------------------------------------------------------------------


def test_build_resolved_workspace_graph_walks_providers():
    index = DocumentIndex()
    provider = ProviderModel(
        meta=ProviderMetaModel(name="p1"),
        spec=ProviderSpecModel(properties=ProviderPropertiesModel(type="local", region="local")),
    )
    index.add(IndexEntry(ref=DocumentRef(kind=PlatformKind.PROVIDER, name="p1"), model=provider, source=Path("p1.yaml")))

    workspace = WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(
            providers=["p1"],
            provisioners=[ProvisionerModel(name="tf", tool="terraform", source=SourceModel(source_path="infra"))],
        ),
    )

    graph = build_resolved_workspace_graph(index, workspace)

    assert graph.providers == {"p1": provider}
    assert graph.topologies == {}
    assert graph.resources == {}


# ---------------------------------------------------------------------------
# build_run() end to end
# ---------------------------------------------------------------------------


def _terraform_solution(tmp_path: Path) -> Path:
    """A minimal real solution: one provider, one resource, one workspace
    with a single Terraform provisioner, one environment, one deployment."""
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
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec: {}\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    return root


def test_build_run_materialises_source_and_writes_terraform_output(tmp_path: Path):
    root = _terraform_solution(tmp_path)

    build_path = tmp_path / "build"
    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok
    # sync_source() mirrored the provisioner's own source_path under build_path.
    materialised = build_path / "infra"
    assert (materialised / "main.tf").exists()
    # TerraformIntegration.default_output() wrote the default projection.
    assert (materialised / "workspace.auto.tfvars.json").exists()
    assert (materialised / "providers.auto.tfvars.json").exists()
    assert (materialised / "resx_server.auto.tfvars.json").exists()


def test_build_run_cleans_stale_output_by_default(tmp_path: Path):
    """v1 parity: a document removed from the solution (the resource here)
    must not leave its old, still-auto-loaded .auto.tfvars.json behind."""
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    _write(build_path, "stale/resx_ghost_type.auto.tfvars.json", '{"stale": true}')

    build_run(_context(root), "app", build_path)

    assert not (build_path / "stale" / "resx_ghost_type.auto.tfvars.json").exists()
    assert (build_path / "infra" / "workspace.auto.tfvars.json").exists()


def test_build_run_clean_false_preserves_existing_output(tmp_path: Path):
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    _write(build_path, "stale/leftover.txt", "still here")

    build_run(_context(root), "app", build_path, clean=False)

    assert (build_path / "stale" / "leftover.txt").read_text() == "still here"
    assert (build_path / "infra" / "workspace.auto.tfvars.json").exists()


def test_build_run_clean_wraps_a_failed_wipe_as_build_clean_error(tmp_path: Path, monkeypatch):
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    build_path.mkdir()

    def _boom(path):
        raise OSError("permission denied")

    monkeypatch.setattr("strata.controllers.build_controller.shutil.rmtree", _boom)

    with pytest.raises(BuildCleanError, match="permission denied"):
        build_run(_context(root), "app", build_path)


def test_build_run_raises_for_unknown_deployment(tmp_path: Path):
    root = _solution(tmp_path)
    with pytest.raises(UsageError, match="ghost"):
        build_run(_context(root), "ghost", tmp_path / "build")


def test_build_run_renders_helm_workload_modules(tmp_path: Path):
    """End to end for the workload pipeline (ADR-0022 D5-D7): a namespace's
    helm module is materialised and rendered alongside the provisioner loop,
    with zero namespace/module-specific code in build_run() itself — every
    namespace the workspace references already arrives resolved on `graph`."""
    root = _solution(tmp_path)
    _write(root, "infra/main.tf", "# root module\n")
    _write(root, "charts/authentik/Chart.yaml", "name: authentik\n")
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
    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok
    module_dir = build_path / "apps" / "auth"
    assert (module_dir / "Chart.yaml").exists()
    assert (module_dir / "values.yaml").exists()
    meta = yaml.safe_load((module_dir / "meta.yaml").read_text())
    assert meta == {"releaseName": "auth", "namespace": "apps"}



# NOTE: "deployment has no workspace" is not separately testable through a
# real, loadable solution — `DeploymentSpecModel.validate_complete_unless_partial()`
# already requires `workspace` for any complete, non-partial deployment, so
# `context.require_valid()` would never pass with one missing. `build_run()`'s
# own `if deployment.spec.workspace is None` check is a defensive backstop
# for that reason, not a reachable path via a valid solution.
