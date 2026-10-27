#!/usr/bin/env python3
"""Tests for `build_controller` (ADR-0022) — `ordered_by_depends_on()`,
`find_provisioner()`, `build_resolved_workspace_graph()`, and the full
`build_run()` orchestrator end to end."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from strata.controllers.build_controller import (
    BuildCleanError,
    apply_asset_version_pin,
    apply_remote_version_pins,
    build_resolved_workspace_graph,
    build_run,
    expected_source_paths,
    find_provisioner,
    ordered_by_depends_on,
)
from strata.controllers.solution_context import open_solution
from strata.controllers.solution_controller import DocumentIndex, DocumentRef, IndexEntry
from strata.controllers.source_sync import SourceSyncError
from strata.controllers.value_controller import resolve_deployment_workspace
from strata.integrations.terraform_projection import build_strata_payload
from strata.models.asset_model import AssetModel
from strata.models.checkov_model import CheckovFindingModel, CheckovScanResultModel
from strata.models.common_models import ModuleReferenceModel, PlatformKind, SourceModel
from strata.models.deployment_model import DeploymentModel
from strata.models.module_model import ModuleMetaModel, ModuleModel, ModuleSpecModel
from strata.models.namespace_model import NamespaceMetaModel, NamespaceModel, NamespaceSpecModel
from strata.models.provider_model import ProviderMetaModel, ProviderModel, ProviderSpecModel
from strata.models.provisioning_model import ProvisionerModel, ProvisioningStepModel
from strata.models.sbom_model import CveAuditResultModel
from strata.models.solution_model import RemoteFetch, RemoteType, SolutionRemoteModel
from strata.models.version_model import VersionMetaModel, VersionModel, VersionSpecModel
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
    return ProvisioningStepModel(name=name, provisioner=provisioner, resources=["r1"], depends_on=depends_on)


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


def test_ordered_by_depends_on_raises_on_a_cycle():
    """Regression test for the Phase 1 refactor (docs/work/
    provisioner-source-dependencies.md): this used to silently drop cyclic
    steps from the result instead of raising — confirm the new
    `topological_order()`-backed implementation actually raises instead.
    """
    steps = [_step("a", depends_on=["b"]), _step("b", depends_on=["a"])]
    with pytest.raises(ValueError, match="Circular dependency in execution steps"):
        ordered_by_depends_on(steps)


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


def test_expected_source_paths_keys_a_source_less_step_by_its_step_name(tmp_path: Path):
    """As `build_run()`'s step loop does for a sync/GitOps provisioner (`build_path / step.name`)."""
    workspace = WorkspaceModel.model_validate(
        {
            "meta": {"name": "ws"},
            "spec": {
                "providers": ["p1"],
                "provisioners": [{"name": "gitops", "tool": "argocd"}],
                "execution": [{"name": "sync_apps", "provisioner": "gitops", "resources": ["r1"]}],
                "resources": [{"name": "r1", "resource": "r1"}],
            },
        }
    )
    (tmp_path / "sync_apps").mkdir()
    assert expected_source_paths(tmp_path, workspace) == {"gitops": tmp_path / "sync_apps"}


# ---------------------------------------------------------------------------
# _apply_remote_version_pins() (docs/design/version-pin-overlay.md Phase 3)
# ---------------------------------------------------------------------------


def _remote(name: str = "infra-remote", *, reference: str | None = "main") -> SolutionRemoteModel:
    return SolutionRemoteModel(
        name=name,
        type=RemoteType.GIT,
        url="https://example.com/org/infra.git",
        reference=reference,
        fetch=RemoteFetch.STRATA,
    )


def _version(name: str = "prd", *, remote_pins: dict[str, str] | None = None) -> VersionModel:
    return VersionModel(
        meta=VersionMetaModel(name=name),
        spec=VersionSpecModel(pins={"remotes": remote_pins} if remote_pins else {}),
    )


def test_apply_remote_version_pins_returns_same_dict_when_version_is_none():
    remotes = {"infra-remote": _remote()}
    assert apply_remote_version_pins(remotes, None) is remotes


def test_apply_remote_version_pins_returns_same_dict_when_no_pin_matches():
    remotes = {"infra-remote": _remote()}
    version = _version(remote_pins={"other-remote": "v9.9.9"})
    assert apply_remote_version_pins(remotes, version) is remotes


def test_apply_remote_version_pins_overrides_reference_when_pinned():
    remotes = {"infra-remote": _remote(reference="main"), "untouched": _remote("untouched", reference="v1.0.0")}
    version = _version(remote_pins={"infra-remote": "v2.0.0"})

    overlaid = apply_remote_version_pins(remotes, version)

    assert overlaid is not remotes
    assert overlaid["infra-remote"].reference == "v2.0.0"
    assert overlaid["untouched"] is remotes["untouched"]
    assert remotes["infra-remote"].reference == "main"  # original dict/model untouched


def test_apply_remote_version_pins_logs_each_application():
    import io
    import json

    from strata.logging.config import configure_logging, shutdown_logging

    remotes = {"infra-remote": _remote(reference="main")}
    version = _version(remote_pins={"infra-remote": "v2.0.0"})

    stream = io.StringIO()
    try:
        configure_logging(level="INFO", json_output=True, stream=stream)
        apply_remote_version_pins(remotes, version)
    finally:
        shutdown_logging()

    line = stream.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "version pin applied"
    assert payload["category"] == "remotes"
    assert payload["name"] == "infra-remote"
    assert payload["declared"] == "main"
    assert payload["pinned"] == "v2.0.0"
    assert payload["version"] == "prd"


# ---------------------------------------------------------------------------
# build_resolved_workspace_graph()
# ---------------------------------------------------------------------------


def test_build_resolved_workspace_graph_walks_providers():
    index = DocumentIndex()
    provider = ProviderModel(
        meta=ProviderMetaModel(name="p1"),
        spec=ProviderSpecModel(type="local", region="local"),
    )
    index.add(
        IndexEntry(ref=DocumentRef(kind=PlatformKind.PROVIDER, name="p1"), model=provider, source=Path("p1.yaml"))
    )

    workspace = WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(
            providers=["p1"],
            provisioners=[ProvisionerModel(name="tf", tool="terraform", source=SourceModel(source_path="infra"))],
        ),
    )

    graph = build_resolved_workspace_graph(index, workspace)

    assert graph.providers == {"p1": provider}
    assert graph.resources == {}
    assert graph.modules == {}


def test_build_resolved_workspace_graph_keys_providers_by_slot():
    """A slot naming a different document is keyed by the slot (strata.providers.<slot>)."""
    index = DocumentIndex()
    provider = ProviderModel(
        meta=ProviderMetaModel(name="azure-westeurope"),
        spec=ProviderSpecModel(type="local", region="local"),
    )
    index.add(
        IndexEntry(
            ref=DocumentRef(kind=PlatformKind.PROVIDER, name="azure-westeurope"),
            model=provider,
            source=Path("p.yaml"),
        )
    )
    workspace = WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(
            providers=[{"slot": "azure", "provider": "azure-westeurope"}],
            provisioners=[ProvisionerModel(name="tf", tool="terraform", source=SourceModel(source_path="infra"))],
        ),
    )

    graph = build_resolved_workspace_graph(index, workspace)

    assert graph.providers == {"azure": provider}


def test_build_resolved_workspace_graph_applies_deployment_provider_binding():
    """Same workspace, second region: the slot keeps its key, the bound document supplies the
    region, and the projection names which document is bound."""
    index = DocumentIndex()
    for name, region in (("azure-westeurope", "westeurope"), ("azure-americas", "eastus2")):
        index.add(
            IndexEntry(
                ref=DocumentRef(kind=PlatformKind.PROVIDER, name=name),
                model=ProviderModel(
                    meta=ProviderMetaModel(name=name), spec=ProviderSpecModel(type="azure", region=region)
                ),
                source=Path(f"{name}.yaml"),
            )
        )
    workspace = WorkspaceModel(
        meta=WorkspaceMetaModel(name="apim"),
        spec=WorkspaceSpecModel(
            providers=[{"slot": "azure", "provider": "azure-westeurope"}],
            provisioners=[ProvisionerModel(name="tf", tool="terraform", source=SourceModel(source_path="infra"))],
        ),
    )
    deployment = DeploymentModel.model_validate(
        {
            "meta": {"name": "apim-americas-prd"},
            "spec": {
                "workspace": "apim",
                "environments": ["prd"],
                "providers": [{"slot": "azure", "provider": "azure-americas"}],
            },
        }
    )

    graph = build_resolved_workspace_graph(index, workspace, deployment=deployment)

    assert list(graph.providers) == ["azure"]
    projected = build_strata_payload(graph)["providers"]["azure"]
    assert projected["region"] == "eastus2"
    assert projected["provider"] == "azure-americas"


def test_build_resolved_workspace_graph_walks_modules_via_namespaces():
    """docs/design/unified-terraform-projection.md: `graph.modules`
    is populated from every resolved namespace's own `spec.modules`, not
    directly from a workspace-level field. Two namespaces reference the
    same module under two different reference names — confirms dict-by-name
    dedup doesn't break (`_lookup_all()` keys by Module *document* name,
    not by the reference name pointing at it)."""
    index = DocumentIndex()
    module_a = ModuleModel(
        meta=ModuleMetaModel(name="authentik"),
        spec=ModuleSpecModel(source=SourceModel(source_path="modules/authentik"), type="helm"),
    )
    module_b = ModuleModel(
        meta=ModuleMetaModel(name="grafana"),
        spec=ModuleSpecModel(source=SourceModel(source_path="modules/grafana"), type="helm"),
    )
    index.add(
        IndexEntry(ref=DocumentRef(kind=PlatformKind.MODULE, name="authentik"), model=module_a, source=Path("a.yaml"))
    )
    index.add(
        IndexEntry(ref=DocumentRef(kind=PlatformKind.MODULE, name="grafana"), model=module_b, source=Path("b.yaml"))
    )

    namespace_one = NamespaceModel(
        meta=NamespaceMetaModel(name="hearth"),
        spec=NamespaceSpecModel(
            modules=[
                ModuleReferenceModel(name="auth", module="authentik"),
                ModuleReferenceModel(name="auth-again", module="authentik"),  # same module, 2nd reference name
            ]
        ),
    )
    namespace_two = NamespaceModel(
        meta=NamespaceMetaModel(name="metrics"),
        spec=NamespaceSpecModel(modules=[ModuleReferenceModel(name="dash", module="grafana")]),
    )
    index.add(
        IndexEntry(
            ref=DocumentRef(kind=PlatformKind.NAMESPACE, name="hearth"), model=namespace_one, source=Path("ns1.yaml")
        )
    )
    index.add(
        IndexEntry(
            ref=DocumentRef(kind=PlatformKind.NAMESPACE, name="metrics"), model=namespace_two, source=Path("ns2.yaml")
        )
    )

    workspace = WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(
            providers=["p1"],
            provisioners=[ProvisionerModel(name="tf", tool="terraform", source=SourceModel(source_path="infra"))],
            namespaces=["hearth", "metrics"],
        ),
    )

    graph = build_resolved_workspace_graph(index, workspace)

    assert graph.modules == {"authentik": module_a, "grafana": module_b}


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
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
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
    # TerraformIntegration.default_output() wrote the unified projection
    # (ADR-0035, docs/design/unified-terraform-projection.md) — one
    # strata.auto.tfvars.json file, not the old per-category files.
    assert (materialised / "strata.auto.tfvars.json").exists()
    assert not (materialised / "workspace.auto.tfvars.json").exists()
    assert not (materialised / "resx_server.auto.tfvars.json").exists()


# ---------------------------------------------------------------------------
# Assets (docs/design/asset-management.md Phase 3) — sync_asset(), resolved
# once up front from the deployment's own graph.properties, before the step
# loop.
# ---------------------------------------------------------------------------


def _asset_solution(tmp_path: Path) -> Path:
    """Extends _terraform_solution with one asset (templated source_path),
    one consuming step — this doc's own Phase 3 'Done when' fixture."""
    root = _terraform_solution(tmp_path)
    _write(root, "assets/contoso/prod/openapi/openapi.json", "{}")
    _write(
        root,
        "asset.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: asset\nmeta:\n  name: openapi\nspec:\n"
        "  source_path: assets/contoso/{deploy_tier}/openapi\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  properties:\n    deploy_tier: prod\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "      assets:\n        - openapi\n"
        "  resources:\n    - name: r1\n      resource: r1\n"
        "  assets:\n    - openapi\n",
    )
    return root


def test_build_run_materialises_an_asset_before_the_step_loop(tmp_path: Path):
    root = _asset_solution(tmp_path)
    build_path = tmp_path / "build"

    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok
    destination = build_path / "assets" / "openapi"
    assert (destination / "openapi.json").read_text() == "{}"


def test_build_run_projects_the_asset_into_the_consuming_steps_strata_payload(tmp_path: Path):
    """docs/design/asset-management.md Phase 4 — the same fixture workspace
    from Phase 3 produces strata.auto.tfvars.json with a populated
    assets.<name>.{path,files} key, every other projected category
    unchanged."""
    root = _asset_solution(tmp_path)
    build_path = tmp_path / "build"

    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok
    payload = json.loads((build_path / "infra" / "strata.auto.tfvars.json").read_text())["strata"]
    assert payload["assets"] == {
        "openapi": {
            "asset": "openapi",
            "path": "assets/openapi",
            "path_from_step": "../assets/openapi",
            "files": ["openapi.json"],
        }
    }
    assert set(payload) == {
        "workspace",
        "tenant",
        "providers",
        "resources",
        "namespaces",
        "modules",
        "dns",
        "networks",
        "firewalls",
        "topologies",
        "properties",
        "custom",
        "flags",
        "variables",
        "default_tags",
        "assets",
    }


def test_build_run_path_from_step_resolves_from_each_steps_own_directory(tmp_path: Path):
    """Two provisioners at different depths see different `path_from_step`
    values, and each one really leads from that step's directory to the files."""
    root = _asset_solution(tmp_path)
    _write(root, "terraform/apim/main.tf", "# apim\n")
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n"
        "    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "    - name: tf_apim\n      tool: terraform\n      source:\n        source_path: terraform/apim\n"
        "  execution:\n"
        "    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "      assets:\n        - openapi\n"
        "    - name: apply_apim\n      provisioner: tf_apim\n      resources:\n        - r1\n"
        "      depends_on:\n        - apply_infra\n"
        "      assets:\n        - openapi\n"
        "  resources:\n    - name: r1\n      resource: r1\n"
        "  assets:\n    - openapi\n",
    )
    build_path = tmp_path / "build"

    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok
    seen = {}
    for step_dir in ("infra", "terraform/apim"):
        asset = json.loads((build_path / step_dir / "strata.auto.tfvars.json").read_text())["strata"]["assets"][
            "openapi"
        ]
        assert asset["path"] == "assets/openapi"
        seen[step_dir] = asset["path_from_step"]
        assert (build_path / step_dir / asset["path_from_step"] / asset["files"][0]).read_text() == "{}"
    assert seen == {"infra": "../assets/openapi", "terraform/apim": "../../assets/openapi"}


def _asset_slot_solution(tmp_path: Path) -> Path:
    """`_asset_solution` with the workspace slot `api` bound to the `openapi` document."""
    root = _asset_solution(tmp_path)
    workspace = (root / "workspace.yaml").read_text(encoding="utf-8")
    workspace = workspace.replace("      assets:\n        - openapi\n", "      assets:\n        - api\n")
    workspace = workspace.replace("  assets:\n    - openapi\n", "  assets:\n    - slot: api\n      asset: openapi\n")
    _write(root, "workspace.yaml", workspace)
    return root


def test_build_run_materialises_a_slot_under_the_slot_name_not_the_document_name(tmp_path: Path):
    root = _asset_slot_solution(tmp_path)
    build_path = tmp_path / "build"

    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok
    assert (build_path / "assets" / "api" / "openapi.json").read_text() == "{}"
    assert not (build_path / "assets" / "openapi").exists()
    payload = json.loads((build_path / "infra" / "strata.auto.tfvars.json").read_text())["strata"]
    assert payload["assets"] == {
        "api": {
            "asset": "openapi",
            "path": "assets/api",
            "path_from_step": "../assets/api",
            "files": ["openapi.json"],
        }
    }


def test_build_run_asset_error_names_the_slot_when_it_differs_from_the_document(tmp_path: Path):
    root = _asset_slot_solution(tmp_path)
    shutil.rmtree(root / "assets")

    with pytest.raises(SourceSyncError, match="Asset slot 'api': Asset 'openapi'"):
        build_run(_context(root), "app", tmp_path / "build")


def test_build_run_dry_run_reports_the_slot_an_asset_would_fill(tmp_path: Path):
    root = _asset_slot_solution(tmp_path)
    steps: list[str] = []

    build_run(_context(root), "app", tmp_path / "build", dry_run=True, on_step=steps.append)

    assert any("would materialise asset 'openapi' into slot 'api'" in s for s in steps)


def _bound_asset_solution(tmp_path: Path) -> Path:
    """`_asset_slot_solution` plus a second Asset document and deployments that rebind the slot to it:
    `app-alt` binds it directly, `app-leaf` inherits the binding from the partial `app-base`."""
    root = _asset_slot_solution(tmp_path)
    _write(root, "assets/alt/openapi/alt.json", '{"alt": true}')
    _write(
        root,
        "asset-alt.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: asset\nmeta:\n  name: openapi-alt\nspec:\n"
        "  source_path: assets/alt/openapi\n",
    )
    header = "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\n"
    _write(
        root,
        "deployment-alt.yaml",
        header + "meta:\n  name: app-alt\nspec:\n  workspace: main\n  environments:\n    - prd\n"
        "  assets:\n    - slot: api\n      asset: openapi-alt\n",
    )
    _write(
        root,
        "deployment-base.yaml",
        header + "meta:\n  name: app-base\nspec:\n  partial: true\n  workspace: main\n  environments:\n    - prd\n"
        "  assets:\n    - slot: api\n      asset: openapi-alt\n",
    )
    _write(root, "deployment-leaf.yaml", header + "meta:\n  name: app-leaf\nspec:\n  extends: app-base\n")
    return root


def _built_assets(build_path: Path) -> dict:
    return json.loads((build_path / "infra" / "strata.auto.tfvars.json").read_text())["strata"]["assets"]


def test_two_deployments_of_one_workspace_get_different_files_at_the_same_slot_path(tmp_path: Path):
    root = _bound_asset_solution(tmp_path)

    assert build_run(_context(root), "app", tmp_path / "build-default").ok
    assert build_run(_context(root), "app-alt", tmp_path / "build-alt").ok

    default = _built_assets(tmp_path / "build-default")["api"]
    alt = _built_assets(tmp_path / "build-alt")["api"]
    assert default["path"] == alt["path"] == "assets/api"
    assert default["path_from_step"] == alt["path_from_step"]
    assert (default["asset"], default["files"]) == ("openapi", ["openapi.json"])
    assert (alt["asset"], alt["files"]) == ("openapi-alt", ["alt.json"])


def test_build_run_inherits_an_asset_binding_through_extends(tmp_path: Path):
    root = _bound_asset_solution(tmp_path)

    assert build_run(_context(root), "app-leaf", tmp_path / "build").ok

    assert _built_assets(tmp_path / "build")["api"]["asset"] == "openapi-alt"


def test_rebuilding_into_the_same_build_path_drops_the_previous_bindings_files(tmp_path: Path):
    root = _bound_asset_solution(tmp_path)
    build_path = tmp_path / "build"
    assert build_run(_context(root), "app", build_path, clean=False).ok
    _write(build_path, "assets/mine/keep.txt", "mine")

    assert build_run(_context(root), "app-alt", build_path, clean=False).ok

    assert _built_assets(build_path)["api"]["files"] == ["alt.json"]
    assert not (build_path / "assets" / "api" / "openapi.json").exists()
    assert (build_path / "assets" / "mine" / "keep.txt").read_text() == "mine"


def _git_repo(path: Path, versions: dict[str, dict[str, str]]) -> str:
    """A local git repository with one commit and tag per entry of `versions` (tag -> files); returns its URL."""
    path.mkdir(parents=True)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q"], cwd=path, check=True, capture_output=True)
    for tag, files in versions.items():
        for existing in path.iterdir():
            if existing.name != ".git":
                shutil.rmtree(existing) if existing.is_dir() else existing.unlink()
        for relative, content in files.items():
            _write(path, relative, content)
        for command in (["add", "-A"], ["commit", "-q", "-m", tag], ["tag", tag]):
            subprocess.run([*git, *command], cwd=path, check=True, capture_output=True)
    return str(path)


def _located_asset_solution(tmp_path: Path) -> Path:
    """Two customers, each with a repository of their own and an asset document pointing at it, bound
    to the workspace slot `api` by `app-c0062`/`app-c0063`; `app-pinned` is `app-c0062` plus a Version
    pinning that asset to the tag `v2`. No remote is declared in strata.yaml for any of them."""
    root = _asset_slot_solution(tmp_path)
    c0062 = _git_repo(tmp_path / "c0062", {"v1": {"api/one.json": "1"}, "v2": {"api/two.json": "2"}})
    c0063 = _git_repo(tmp_path / "c0063", {"v1": {"api/other.json": "3"}})
    asset = "apiVersion: strata.huybrechts.xyz/v2\nkind: asset\nmeta:\n  name: {name}\nspec:\n  source_path: api\n"
    location = "  location:\n    type: git\n    url: '{url}'\n    reference: v1\n"
    deployment = (
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: {name}\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n{version}"
        "  assets:\n    - slot: api\n      asset: {asset}\n"
    )
    for name, url in (("c0062-api", c0062), ("c0063-api", c0063)):
        _write(root, f"asset-{name}.yaml", asset.format(name=name) + location.format(url=url.replace("\\", "/")))
    _write(root, "dep-c0062.yaml", deployment.format(name="app-c0062", asset="c0062-api", version=""))
    _write(root, "dep-c0063.yaml", deployment.format(name="app-c0063", asset="c0063-api", version=""))
    _write(
        root,
        "dep-pinned.yaml",
        deployment.format(name="app-pinned", asset="c0062-api", version="  version: ring\n"),
    )
    _write(
        root,
        "version-ring.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: version\nmeta:\n  name: ring\nspec:\n"
        "  workspace: main\n  pins:\n    assets:\n      c0062-api: v2\n",
    )
    return root


_NEEDS_GIT = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


@_NEEDS_GIT
def test_customers_with_their_own_repositories_need_no_manifest_remote(tmp_path: Path):
    root = _located_asset_solution(tmp_path)

    assert build_run(_context(root), "app-c0062", tmp_path / "b1").ok
    assert build_run(_context(root), "app-c0063", tmp_path / "b2").ok

    first, second = _built_assets(tmp_path / "b1")["api"], _built_assets(tmp_path / "b2")["api"]
    assert first["path"] == second["path"] == "assets/api"
    assert (first["asset"], first["files"]) == ("c0062-api", ["one.json"])
    assert (second["asset"], second["files"]) == ("c0063-api", ["other.json"])
    assert (root / ".strata" / "assets" / "c0062-api" / "v1").is_dir()


@_NEEDS_GIT
def test_a_version_pin_moves_one_customers_asset_to_another_reference(tmp_path: Path):
    root = _located_asset_solution(tmp_path)

    assert build_run(_context(root), "app-pinned", tmp_path / "build").ok

    assert _built_assets(tmp_path / "build")["api"]["files"] == ["two.json"]
    assert (root / ".strata" / "assets" / "c0062-api" / "v2").is_dir()


def _located_asset_model(reference: str = "v1", *, with_location: bool = True) -> AssetModel:
    spec: dict = {"source_path": "api"}
    if with_location:
        spec["location"] = {"url": "https://example.com/c.git", "reference": reference}
    return AssetModel.model_validate({"meta": {"name": "c0062-api"}, "spec": spec})


def _ring_version(pins: str) -> VersionModel:
    return VersionModel.model_validate(
        yaml.safe_load(
            f"apiVersion: strata.huybrechts.xyz/v2\nkind: version\nmeta:\n  name: ring\nspec:\n  workspace: main\n{pins}"
        )
    )


def test_apply_asset_version_pin_overrides_the_location_reference_only():
    asset = _located_asset_model()

    pinned = apply_asset_version_pin(asset, _ring_version("  pins:\n    assets:\n      c0062-api: v2\n"))

    assert pinned.spec.location.reference == "v2"
    assert pinned.spec.location.url == asset.spec.location.url
    assert asset.spec.location.reference == "v1"


def test_apply_asset_version_pin_is_a_no_op_without_a_version_a_pin_or_a_location():
    asset = _located_asset_model()
    assert apply_asset_version_pin(asset, None) is asset
    assert apply_asset_version_pin(asset, _ring_version("  pins: {}\n")) is asset
    no_location = _located_asset_model(with_location=False)
    assert (
        apply_asset_version_pin(no_location, _ring_version("  pins:\n    assets:\n      c0062-api: v2\n"))
        is no_location
    )


def test_build_run_fails_when_an_assets_on_disk_variant_is_missing(tmp_path: Path):
    root = _asset_solution(tmp_path)
    shutil.rmtree(root / "assets")

    with pytest.raises(SourceSyncError, match="does not exist"):
        build_run(_context(root), "app", tmp_path / "build")


def test_build_run_dry_run_reports_asset_materialisation_without_copying(tmp_path: Path):
    root = _asset_solution(tmp_path)
    build_path = tmp_path / "build"
    steps: list[str] = []

    build_run(_context(root), "app", build_path, dry_run=True, on_step=steps.append)

    assert any("would materialise asset 'openapi'" in s for s in steps)
    assert not (build_path / "assets" / "openapi").exists()


# ---------------------------------------------------------------------------
# materialise_provisioner_sources() (ADR-0029) — a provisioner named only in
# another provisioner's depends_on, never in execution, still gets synced.
# ---------------------------------------------------------------------------


def _terraform_solution_with_dependency(tmp_path: Path) -> Path:
    """Shaped exactly like the reported issue: `tf_main` depends on
    `iac_components`/`iac_primitives`, neither of which has an execution
    step of its own — they're reachable only via `tf_main`'s `depends_on`."""
    root = _terraform_solution(tmp_path)
    _write(root, "components/main.tf", "# shared components module\n")
    _write(root, "primitives/main.tf", "# shared primitives module\n")
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n"
        "    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "      depends_on:\n        - iac_components\n        - iac_primitives\n"
        "    - name: iac_components\n      tool: terraform\n      source:\n        source_path: components\n"
        "    - name: iac_primitives\n      tool: terraform\n      source:\n        source_path: primitives\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    return root


def _terraform_solution_with_shared_dependency(tmp_path: Path) -> Path:
    """Two deployable provisioners (`tf_a`/`tf_b`) both depend on the same
    staging provisioner (`iac_components`) — it must be synced exactly once
    per `build_run()` invocation, not once per consumer."""
    root = _solution(tmp_path)
    _write(root, "infra_a/main.tf", "# root module a\n")
    _write(root, "infra_b/main.tf", "# root module b\n")
    _write(root, "components/main.tf", "# shared components module\n")
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
        "  providers:\n    - p1\n"
        "  provisioners:\n"
        "    - name: tf_a\n      tool: terraform\n      source:\n        source_path: infra_a\n"
        "      depends_on:\n        - iac_components\n"
        "    - name: tf_b\n      tool: terraform\n      source:\n        source_path: infra_b\n"
        "      depends_on:\n        - iac_components\n"
        "    - name: iac_components\n      tool: terraform\n      source:\n        source_path: components\n"
        "  execution:\n"
        "    - name: apply_a\n      provisioner: tf_a\n      resources:\n        - r1\n"
        "    - name: apply_b\n      provisioner: tf_b\n      resources:\n        - r2\n"
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
    return root


def _terraform_solution_with_transitive_dependency(tmp_path: Path) -> Path:
    """A chain: `tf_main` depends on `iac_components`, which itself depends
    on `iac_primitives` — neither dependency has an execution step."""
    root = _terraform_solution(tmp_path)
    _write(root, "components/main.tf", "# shared components module\n")
    _write(root, "primitives/main.tf", "# shared primitives module\n")
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n"
        "    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "      depends_on:\n        - iac_components\n"
        "    - name: iac_components\n      tool: terraform\n      source:\n        source_path: components\n"
        "      depends_on:\n        - iac_primitives\n"
        "    - name: iac_primitives\n      tool: terraform\n      source:\n        source_path: primitives\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    return root


def test_build_run_materialises_a_dependency_provisioner_with_no_execution_step(tmp_path: Path):
    root = _terraform_solution_with_dependency(tmp_path)
    build_path = tmp_path / "build"

    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok
    assert (build_path / "infra" / "main.tf").exists()
    assert (build_path / "components" / "main.tf").exists()
    assert (build_path / "primitives" / "main.tf").exists()


def test_build_run_materialises_a_transitive_dependency_chain(tmp_path: Path):
    root = _terraform_solution_with_transitive_dependency(tmp_path)
    build_path = tmp_path / "build"

    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok
    assert (build_path / "infra" / "main.tf").exists()
    assert (build_path / "components" / "main.tf").exists()
    assert (build_path / "primitives" / "main.tf").exists()


def test_build_run_materialises_a_shared_dependency_only_once(tmp_path: Path, monkeypatch):
    root = _terraform_solution_with_shared_dependency(tmp_path)
    build_path = tmp_path / "build"

    from strata.controllers import build_controller as build_controller_module

    real_sync_source = build_controller_module.sync_source
    calls: list[str] = []

    def _counting_sync_source(root_, build_path_, source, remotes, **kwargs):
        calls.append(source.source_path)
        return real_sync_source(root_, build_path_, source, remotes, **kwargs)

    monkeypatch.setattr(build_controller_module, "sync_source", _counting_sync_source)

    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok
    assert calls.count("components") == 1
    assert calls.count("infra_a") == 1
    assert calls.count("infra_b") == 1


def test_build_run_dry_run_materialises_a_shared_dependency_only_once(tmp_path: Path):
    """Same dedup guarantee as the real-run version above, under --dry-run -
    memoisation must short-circuit on the second visit even though no real
    sync_source() call (and thus no 'already materialised' path) happens."""
    root = _terraform_solution_with_shared_dependency(tmp_path)
    build_path = tmp_path / "build"
    steps: list[str] = []

    build_run(_context(root), "app", build_path, dry_run=True, on_step=steps.append)

    dependency_messages = [s for s in steps if "provisioner 'iac_components'" in s]
    assert len(dependency_messages) == 1


def test_build_run_dry_run_reports_dependency_materialisation(tmp_path: Path):
    root = _terraform_solution_with_dependency(tmp_path)
    build_path = tmp_path / "build"
    steps: list[str] = []

    build_run(_context(root), "app", build_path, dry_run=True, on_step=steps.append)

    assert any("would materialise provisioner 'iac_components' source (dependency," in s for s in steps)
    assert any("would materialise provisioner 'iac_primitives' source (dependency," in s for s in steps)
    assert not build_path.exists()


def test_build_run_never_resolves_integration_for_a_dependency_only_provisioner(tmp_path: Path, monkeypatch):
    root = _terraform_solution_with_dependency(tmp_path)
    build_path = tmp_path / "build"

    from strata.controllers import build_controller as build_controller_module

    real_resolve_integration = build_controller_module.resolve_integration
    resolved_for: list[str] = []

    def _recording_resolve_integration(index, provisioner):
        resolved_for.append(provisioner.name)
        return real_resolve_integration(index, provisioner)

    monkeypatch.setattr(build_controller_module, "resolve_integration", _recording_resolve_integration)

    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok
    assert resolved_for == ["tf_main"]  # iac_components/iac_primitives never go through resolve_integration()


# ---------------------------------------------------------------------------
# build_run()'s `materialised` dict as a unified provisioner -> source_path
# mapping (docs/archive/checkov-integration.md Open Question 2 / Phase 1 —
# a real prerequisite for a future evaluator, not purely additive).
# ---------------------------------------------------------------------------


def _terraform_solution_with_dependency_also_executed(tmp_path: Path) -> Path:
    """`iac_components` is reachable BOTH ways at once: named in `tf_main`'s
    `depends_on` AND the subject of its own execution step — the documented
    'synced twice, harmlessly' case (`materialise_provisioner_sources()`'s
    own docstring)."""
    root = _terraform_solution_with_dependency(tmp_path)
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n"
        "    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "      depends_on:\n        - iac_components\n        - iac_primitives\n"
        "    - name: iac_components\n      tool: terraform\n      source:\n        source_path: components\n"
        "    - name: iac_primitives\n      tool: terraform\n      source:\n        source_path: primitives\n"
        "  execution:\n"
        "    - name: apply_components\n      provisioner: iac_components\n      resources:\n        - r1\n"
        "    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "      depends_on:\n        - apply_components\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    return root


def _capture_materialised_dict(monkeypatch):
    """Capture the real `materialised` dict object `build_run()` builds and
    mutates — by recording what's passed into `materialise_provisioner_
    sources()`, the same dict instance the main step loop now also writes
    into directly."""
    from strata.controllers import build_controller as build_controller_module

    captured: dict[str, dict] = {}
    real_materialise = build_controller_module.materialise_provisioner_sources

    def _capturing_materialise(context, build_path_, remotes, workspace, provisioner, materialised, **kwargs):
        captured["materialised"] = materialised
        return real_materialise(context, build_path_, remotes, workspace, provisioner, materialised, **kwargs)

    monkeypatch.setattr(build_controller_module, "materialise_provisioner_sources", _capturing_materialise)
    return captured


def test_build_run_passes_one_credential_resolver_to_every_provisioner_source_clone(tmp_path: Path, monkeypatch):
    """Dependency-only provisioners (`depends_on`) and execution-step provisioners clone through different
    call sites; both must carry the build's resolver or a private remote silently uses ambient git auth."""
    from strata.controllers import build_controller as build_controller_module
    from strata.controllers.remote_credentials import RemoteCredentialResolver

    root = _terraform_solution_with_dependency(tmp_path)
    real_sync_source = build_controller_module.sync_source
    seen: dict[str, object] = {}

    def _spy(root_, build_path_, source, remotes, **kwargs):
        seen[source.source_path] = kwargs.get("credentials")
        return real_sync_source(root_, build_path_, source, remotes, **kwargs)

    monkeypatch.setattr(build_controller_module, "sync_source", _spy)

    diagnostics = build_run(_context(root), "app", tmp_path / "build")

    assert diagnostics.ok
    assert {"components", "primitives", "infra"} <= set(seen)  # two dependencies and the step's own source
    assert all(isinstance(resolver, RemoteCredentialResolver) for resolver in seen.values())
    assert len({id(resolver) for resolver in seen.values()}) == 1


def test_build_run_captures_every_execution_step_provisioners_source_path(tmp_path: Path, monkeypatch):
    """Regression test for the Checkov Phase 1 prerequisite: an
    execution-step provisioner's own `source_path` used to be a local
    variable, discarded before any future evaluator could read it. It must
    now also land in the same `materialised` dict dependency-only
    provisioners already use."""
    root = _terraform_solution_with_dependency(tmp_path)
    build_path = tmp_path / "build"
    captured = _capture_materialised_dict(monkeypatch)

    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok
    materialised = captured["materialised"]
    # Dependency-only provisioners (pre-existing behaviour).
    assert materialised["iac_components"] == build_path / "components"
    assert materialised["iac_primitives"] == build_path / "primitives"
    # NEW: the execution-step provisioner's own path is captured too.
    assert materialised["tf_main"] == build_path / "infra"


def test_build_run_unified_mapping_survives_a_provisioner_reached_both_ways(tmp_path: Path, monkeypatch):
    """A provisioner that is both a `depends_on` target and the subject of
    its own execution step (the documented double-sync case) ends up with
    exactly one, correct entry in `materialised` — not a crash, not two
    conflicting entries."""
    root = _terraform_solution_with_dependency_also_executed(tmp_path)
    build_path = tmp_path / "build"
    captured = _capture_materialised_dict(monkeypatch)

    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok
    materialised = captured["materialised"]
    assert materialised["iac_components"] == build_path / "components"
    assert materialised["iac_primitives"] == build_path / "primitives"
    assert materialised["tf_main"] == build_path / "infra"
    assert (build_path / "components" / "main.tf").exists()


def _terraform_solution_with_target_path_and_unreached(tmp_path: Path) -> Path:
    """`target_path`, a source-less dependency, and a provisioner no step reaches."""
    root = _terraform_solution_with_dependency(tmp_path)
    _write(root, "spare/main.tf", "# never reached\n")
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n"
        "    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "        target_path: rendered/infra\n"
        "      depends_on:\n        - iac_components\n        - gitops_sync\n"
        "    - name: iac_components\n      tool: terraform\n      source:\n        source_path: components\n"
        "    - name: gitops_sync\n      tool: argocd\n"
        "    - name: tf_spare\n      tool: terraform\n      source:\n        source_path: spare\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    return root


@pytest.mark.parametrize(
    "fixture",
    [
        _terraform_solution_with_dependency,
        _terraform_solution_with_transitive_dependency,
        _terraform_solution_with_shared_dependency,
        _terraform_solution_with_dependency_also_executed,
        _terraform_solution_with_target_path_and_unreached,
    ],
)
def test_expected_source_paths_matches_what_build_run_hands_the_checkov_gate(tmp_path: Path, monkeypatch, fixture):
    """Security-gates plan P9-2: `policy check` re-derives the paths `build run` used, so they must agree."""
    from strata.controllers import build_controller as build_controller_module

    root = fixture(tmp_path)
    build_path = tmp_path / "build"
    handed: dict[str, dict] = {}
    real_evaluate = build_controller_module.evaluate_checkov_policy

    def _spy(build_path_, workspace, provisioner_source_paths, **kwargs):
        handed["paths"] = dict(provisioner_source_paths)
        handed["workspace"] = workspace
        return real_evaluate(build_path_, workspace, provisioner_source_paths, **kwargs)

    monkeypatch.setattr(build_controller_module, "evaluate_checkov_policy", _spy)

    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok, diagnostics.messages()
    assert expected_source_paths(build_path, handed["workspace"]) == handed["paths"]


def test_expected_source_paths_reports_a_missing_directory_as_not_materialised(tmp_path: Path):
    root = _terraform_solution_with_target_path_and_unreached(tmp_path)
    build_path = tmp_path / "build"
    assert build_run(_context(root), "app", build_path).ok
    _, workspace = resolve_deployment_workspace(_context(root), "app")
    shutil.rmtree(build_path / "components")

    paths = expected_source_paths(build_path, workspace)

    assert paths == {"iac_components": None, "gitops_sync": None, "tf_main": build_path / "rendered" / "infra"}


# ---------------------------------------------------------------------------
# build_run() + the `remotes` pin overlay (docs/design/version-pin-overlay.md Phase 3)
# ---------------------------------------------------------------------------


def _terraform_solution_with_remote(
    tmp_path: Path, *, version_doc: str = "", reference_version_from_deployment: bool = True
) -> Path:
    """Like `_terraform_solution()`, but the provisioner's source comes from
    a declared `fetch: strata` git remote instead of a bare local path."""
    root = _solution(tmp_path)
    _write(
        root,
        "strata.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: solution\nmeta:\n  name: test-solution\nspec:\n"
        "  remotes:\n    - name: infra-remote\n      type: git\n"
        "      url: https://example.com/org/infra.git\n      reference: main\n",
    )
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
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n"
        "        remote: infra-remote\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec: {}\n",
    )
    version_line = "  version: prd\n" if (version_doc and reference_version_from_deployment) else ""
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        f"  workspace: main\n  environments:\n    - prd\n{version_line}",
    )
    if version_doc:
        _write(root, "version.yaml", version_doc)
    return root


def _fake_clone_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
    """Fakes `git clone`'s real filesystem side effect (creates `infra/main.tf`
    at the clone destination) so `sync_source()`'s `origin.exists()` check
    passes without a real network call — same technique `test_remote_resolution.py`
    uses, extended to also materialise the files `sync_source()` needs."""
    from strata.utils.transport import CommandResult

    if args[:2] == ["git", "clone"]:
        dest = Path(args[-1])
        (dest / "infra").mkdir(parents=True, exist_ok=True)
        (dest / "infra" / "main.tf").write_text("# root module\n", encoding="utf-8")
    return CommandResult(returncode=0, stdout="", stderr="")


def test_build_run_remote_pin_overrides_the_materialised_checkout_path(tmp_path: Path, monkeypatch):
    from strata.controllers import remote_resolution as remote_resolution_module
    from strata.utils import layout

    root = _terraform_solution_with_remote(
        tmp_path,
        version_doc=(
            "apiVersion: strata.huybrechts.xyz/v2\nkind: version\nmeta:\n  name: prd\nspec:\n"
            "  pins:\n    remotes:\n      infra-remote: v2.0.0\n"
        ),
    )
    monkeypatch.setattr(remote_resolution_module, "run_command", _fake_clone_run_command)

    build_path = tmp_path / "build"
    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok
    # Checked out at the PIN's ref, not the manifest's declared "main".
    pinned_checkout = layout.remote_checkout_path(root, "infra-remote", "v2.0.0")
    assert pinned_checkout.exists()
    declared_checkout = layout.remote_checkout_path(root, "infra-remote", "main")
    assert not declared_checkout.exists()
    assert (build_path / "infra" / "main.tf").exists()


def test_build_run_without_version_uses_the_manifests_declared_reference(tmp_path: Path, monkeypatch):
    from strata.controllers import remote_resolution as remote_resolution_module
    from strata.utils import layout

    root = _terraform_solution_with_remote(tmp_path)  # no version_doc -> no pin
    monkeypatch.setattr(remote_resolution_module, "run_command", _fake_clone_run_command)

    build_path = tmp_path / "build"
    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok
    declared_checkout = layout.remote_checkout_path(root, "infra-remote", "main")
    assert declared_checkout.exists()


def test_build_run_writes_tenant_output_when_deployment_references_one(tmp_path: Path):
    root = _terraform_solution(tmp_path)
    _write(
        root,
        "tenant.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: tenant\nmeta:\n  name: acme\nspec:\n"
        "  display_name: Acme\n  geographies: [europe]\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  tenant: acme\n  environments:\n    - prd\n",
    )

    build_path = tmp_path / "build"
    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok
    strata_path = build_path / "infra" / "strata.auto.tfvars.json"
    assert yaml.safe_load(strata_path.read_text(encoding="utf-8"))["strata"]["tenant"] == {
        "code": "acme",
        "name": "Acme",
        "geographies": ["europe"],
        "onboarded": None,
        "configuration": {},
        "custom": {},
    }


def test_build_run_writes_no_tenant_output_when_deployment_has_no_tenant(tmp_path: Path):
    root = _terraform_solution(tmp_path)

    build_path = tmp_path / "build"
    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok
    strata_path = build_path / "infra" / "strata.auto.tfvars.json"
    assert yaml.safe_load(strata_path.read_text(encoding="utf-8"))["strata"]["tenant"] == {}


def test_build_run_cleans_stale_output_by_default(tmp_path: Path):
    """v1 parity: a document removed from the solution (the resource here)
    must not leave its old, still-auto-loaded .auto.tfvars.json behind."""
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    _write(build_path, "stale/resx_ghost_type.auto.tfvars.json", '{"stale": true}')

    build_run(_context(root), "app", build_path)

    assert not (build_path / "stale" / "resx_ghost_type.auto.tfvars.json").exists()
    assert (build_path / "infra" / "strata.auto.tfvars.json").exists()


def test_build_run_clean_false_preserves_existing_output(tmp_path: Path):
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    _write(build_path, "stale/leftover.txt", "still here")

    build_run(_context(root), "app", build_path, clean=False)

    assert (build_path / "stale" / "leftover.txt").read_text() == "still here"
    assert (build_path / "infra" / "strata.auto.tfvars.json").exists()


def test_build_run_clean_wraps_a_failed_wipe_as_build_clean_error(tmp_path: Path, monkeypatch):
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    build_path.mkdir()

    def _boom(path):
        raise OSError("permission denied")

    monkeypatch.setattr("strata.controllers.build_controller.shutil.rmtree", _boom)

    with pytest.raises(BuildCleanError, match="permission denied"):
        build_run(_context(root), "app", build_path)


def test_build_run_dry_run_writes_nothing(tmp_path: Path):
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"

    diagnostics = build_run(_context(root), "app", build_path, dry_run=True)

    assert diagnostics.ok
    assert not build_path.exists()


def test_build_run_dry_run_does_not_clean_existing_output(tmp_path: Path):
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    stale = build_path / "stale.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale")

    build_run(_context(root), "app", build_path, dry_run=True)

    assert stale.exists()


def test_build_run_dry_run_still_validates_via_real_resolution(tmp_path: Path):
    """No filesystem-mutation branch skips deployment/workspace/integration
    resolution - a dry run still catches a bad deployment name."""
    root = _terraform_solution(tmp_path)
    with pytest.raises(UsageError, match="ghost"):
        build_run(_context(root), "ghost", tmp_path / "build", dry_run=True)


def test_build_run_dry_run_reports_planned_steps(tmp_path: Path):
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    steps: list[str] = []

    build_run(_context(root), "app", build_path, dry_run=True, on_step=steps.append)

    assert any("would materialise provisioner 'apply_infra'" in s for s in steps)
    assert any("would render provisioner 'apply_infra' via TerraformIntegration" in s for s in steps)
    assert not any(s.startswith("materialised") or s.startswith("rendered") for s in steps)


def test_build_run_real_run_reports_steps_too(tmp_path: Path):
    """The same on_step callback improves a real build's progress output,
    not just --dry-run's - the two share one reporting path."""
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"
    steps: list[str] = []

    build_run(_context(root), "app", build_path, on_step=steps.append)

    assert any(s.startswith("materialised provisioner 'apply_infra' source at") for s in steps)
    assert any(s == "rendered provisioner 'apply_infra' via TerraformIntegration" for s in steps)


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
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
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


def test_build_run_writes_a_valid_sbom(tmp_path: Path):
    """docs/design/sbom-generation.md Phase 1 — build_run() writes
    sbom.json unconditionally, right after the workload pipeline."""
    root = _solution(tmp_path)
    _write(root, "infra/main.tf", "# root module\n")
    _write(root, "charts/authentik/Chart.yaml", "name: authentik\nversion: 2024.1.0\n")
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
        "module.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: module\nmeta:\n  name: authentik\nspec:\n"
        "  source:\n    source_path: charts/authentik\n  type: helm\n"
        "  default_labels:\n    app: authentik\n"
        "  services:\n    - name: server\n      image: ghcr.io/goauthentik/server:2024.1.0\n",
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
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
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

    sbom = json.loads((build_path / "sbom.json").read_text(encoding="utf-8"))
    assert sbom["bomFormat"] == "CycloneDX"
    names = {c["name"] for c in sbom["components"]}
    assert "server" in names  # image collector
    assert "authentik" in names  # helm collector (declarative + Chart.yaml)
    # A floating-ish tag ("2024.1.0") is semver-shaped, so no warning expected here.


def test_build_run_dry_run_does_not_write_an_sbom(tmp_path: Path):
    root = _solution(tmp_path)
    _write(root, "infra/main.tf", "# root module\n")
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
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
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
    diagnostics = build_run(_context(root), "app", build_path, dry_run=True)
    assert diagnostics.ok
    assert not (build_path / "sbom.json").exists()


# ---------------------------------------------------------------------------
# build_run() writes policy_results.json for build-phase policies
# (docs/work/audit-trail.md's ManifestPolicyResultModel population design)
# ---------------------------------------------------------------------------


class _FakeScanner:
    """Mirrors test_sbom_controller.py's own `_FakeScanner` — patched onto
    `sbom_controller`'s module namespace, since that's where
    `evaluate_cve_policy()` constructs `CveScannerIntegration()` from."""

    def __init__(self, *, result: CveAuditResultModel):
        self._result = result

    def is_available(self) -> bool:
        return True

    def scan_sbom(self, sbom_path: Path, *, severity_threshold: str) -> CveAuditResultModel:
        return self._result


def _patch_cve_scanner(monkeypatch: pytest.MonkeyPatch, result: CveAuditResultModel) -> None:
    import strata.controllers.sbom_controller as sbom_controller_module

    monkeypatch.setattr(sbom_controller_module, "CveScannerIntegration", lambda: _FakeScanner(result=result))


class _FakeCheckovScanner:
    """Mirrors `_FakeScanner` above, for `checkov_controller`'s own
    `CheckovIntegration()` construction site."""

    def __init__(self, *, result: CheckovScanResultModel):
        self._result = result
        self.scanned_directories: list[Path] = []

    def scan(self, directory, *, framework, skip_checks, include_checks, custom_checks_dir, timeout):
        self.scanned_directories.append(directory)
        return self._result


def _patch_checkov_scanner(monkeypatch: pytest.MonkeyPatch, result: CheckovScanResultModel) -> None:
    import strata.controllers.checkov_controller as checkov_controller_module

    monkeypatch.setattr(checkov_controller_module, "CheckovIntegration", lambda: _FakeCheckovScanner(result=result))


def _minimal_solution(tmp_path: Path, *, cve_policy_yaml: str = "", checkov_policy_yaml: str = "") -> Path:
    root = _solution(tmp_path)
    _write(root, "infra/main.tf", "# root module\n")
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
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n" + cve_policy_yaml + checkov_policy_yaml,
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


def test_build_run_writes_policy_results_when_cve_policy_breaches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    result = CveAuditResultModel(
        scanner="trivy", scanner_version="1.0", sbom_path="sbom.json", total_findings=1, critical=1
    )
    _patch_cve_scanner(monkeypatch, result)
    root = _minimal_solution(
        tmp_path, cve_policy_yaml="  cve_policy:\n    max_severity: HIGH\n    max_count: 0\n    enforcement: deny\n"
    )

    build_path = tmp_path / "build"
    diagnostics = build_run(_context(root), "app", build_path)

    assert not diagnostics.ok
    results = json.loads((build_path / "policy_results.json").read_text(encoding="utf-8"))["policy_results"]
    assert len(results) == 1
    assert results[0]["policy_name"] == "cve_policy"
    assert results[0]["passed"] is False
    assert len(results[0]["violations"]) >= 1


def test_build_run_records_a_warn_enforcement_breach_as_passed_with_violations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    result = CveAuditResultModel(
        scanner="trivy", scanner_version="1.0", sbom_path="sbom.json", total_findings=1, critical=1
    )
    _patch_cve_scanner(monkeypatch, result)
    root = _minimal_solution(
        tmp_path, cve_policy_yaml="  cve_policy:\n    max_severity: HIGH\n    max_count: 0\n    enforcement: warn\n"
    )

    build_path = tmp_path / "build"
    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok  # a warn-mode breach doesn't fail the build itself
    results = json.loads((build_path / "policy_results.json").read_text(encoding="utf-8"))["policy_results"]
    assert len(results) == 1
    assert results[0]["passed"] is True
    assert len(results[0]["violations"]) >= 1


def test_build_run_writes_no_policy_results_file_when_cve_policy_unset(tmp_path: Path):
    root = _minimal_solution(tmp_path)

    build_path = tmp_path / "build"
    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok
    assert not (build_path / "policy_results.json").exists()


def _checkov_result(*severities: str) -> CheckovScanResultModel:
    findings = [
        CheckovFindingModel(check_id=f"CKV_{i}", check_name="n", resource="r", file_path="f", severity=sev)
        for i, sev in enumerate(severities)
    ]
    return CheckovScanResultModel(
        scanner_version="3.2.0", framework="terraform", scanned_path="infra", findings=findings, failed=len(findings)
    )


def test_build_run_fails_on_an_unrated_checkov_finding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Security-gates plan Phase 9b: open-source Checkov reports no severity, and that still counts."""
    _patch_checkov_scanner(monkeypatch, _checkov_result("UNKNOWN"))
    root = _minimal_solution(
        tmp_path, checkov_policy_yaml="  checkov_policy:\n    severity_gate: critical\n    enforcement: deny\n"
    )

    diagnostics = build_run(_context(root), "app", tmp_path / "build")

    assert [d.code for d in diagnostics.errors] == ["checkov_policy_violation"]


def test_build_run_writes_policy_results_when_checkov_policy_breaches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _patch_checkov_scanner(monkeypatch, _checkov_result("CRITICAL"))
    root = _minimal_solution(
        tmp_path, checkov_policy_yaml="  checkov_policy:\n    severity_gate: high\n    enforcement: deny\n"
    )

    build_path = tmp_path / "build"
    diagnostics = build_run(_context(root), "app", build_path)

    assert not diagnostics.ok
    results = json.loads((build_path / "policy_results.json").read_text(encoding="utf-8"))["policy_results"]
    assert len(results) == 1
    assert results[0]["policy_name"] == "checkov_policy"
    assert results[0]["policy_type"] == "checkov"
    assert results[0]["passed"] is False
    assert len(results[0]["violations"]) >= 1


def test_build_run_records_a_warn_enforcement_checkov_breach_as_passed_with_violations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _patch_checkov_scanner(monkeypatch, _checkov_result("CRITICAL"))
    root = _minimal_solution(
        tmp_path, checkov_policy_yaml="  checkov_policy:\n    severity_gate: high\n    enforcement: warn\n"
    )

    build_path = tmp_path / "build"
    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok  # a warn-mode breach doesn't fail the build itself
    results = json.loads((build_path / "policy_results.json").read_text(encoding="utf-8"))["policy_results"]
    assert len(results) == 1
    assert results[0]["passed"] is True
    assert len(results[0]["violations"]) >= 1


def test_build_run_writes_no_policy_results_file_when_checkov_policy_unset(tmp_path: Path):
    root = _minimal_solution(tmp_path)

    build_path = tmp_path / "build"
    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok
    assert not (build_path / "policy_results.json").exists()


def test_build_run_writes_both_cve_and_checkov_policy_results_together(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Regression test for the Phase 2 write_policy_results() fix: a
    second, independent call would have silently discarded whichever
    policy's result was written first — both must land in the same
    policy_results.json from one call."""
    _patch_cve_scanner(
        monkeypatch,
        CveAuditResultModel(scanner="trivy", scanner_version="1.0", sbom_path="sbom.json", total_findings=0),
    )
    _patch_checkov_scanner(monkeypatch, _checkov_result("CRITICAL"))
    root = _minimal_solution(
        tmp_path,
        cve_policy_yaml="  cve_policy:\n    max_severity: HIGH\n    max_count: 0\n    enforcement: deny\n",
        checkov_policy_yaml="  checkov_policy:\n    severity_gate: high\n    enforcement: deny\n",
    )

    build_path = tmp_path / "build"
    diagnostics = build_run(_context(root), "app", build_path)

    assert not diagnostics.ok  # the checkov breach alone fails the build
    results = json.loads((build_path / "policy_results.json").read_text(encoding="utf-8"))["policy_results"]
    policy_names = {r["policy_name"] for r in results}
    assert policy_names == {"cve_policy", "checkov_policy"}
    cve_result = next(r for r in results if r["policy_name"] == "cve_policy")
    checkov_result = next(r for r in results if r["policy_name"] == "checkov_policy")
    assert cve_result["passed"] is True  # no CVE findings
    assert checkov_result["passed"] is False  # a real checkov breach


# ---------------------------------------------------------------------------
# framework: helm end to end (docs/archive/checkov-integration.md Phase 3) —
# reuses test_build_run_renders_helm_workload_modules()'s own fixture shape.
# ---------------------------------------------------------------------------


def _helm_solution(tmp_path: Path, *, checkov_policy_yaml: str = "") -> Path:
    root = _solution(tmp_path)
    _write(root, "infra/main.tf", "# root module\n")
    _write(root, "charts/authentik/Chart.yaml", "name: authentik\n")
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
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n" + checkov_policy_yaml,
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


def test_build_run_checkov_policy_helm_scans_the_real_materialised_chart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """End to end: the Helm module `build_workload_modules()` materialises
    to `build_path/apps/auth` (same fixture as `test_build_run_renders_
    helm_workload_modules`) is the exact directory `evaluate_checkov_policy()`'s
    `helm` branch independently recomputes and scans — not a separate,
    possibly-divergent path."""
    scanner = _FakeCheckovScanner(result=_checkov_result("CRITICAL"))
    import strata.controllers.checkov_controller as checkov_controller_module

    monkeypatch.setattr(checkov_controller_module, "CheckovIntegration", lambda: scanner)
    root = _helm_solution(
        tmp_path, checkov_policy_yaml="  checkov_policy:\n    framework: helm\n    severity_gate: high\n"
    )

    build_path = tmp_path / "build"
    diagnostics = build_run(_context(root), "app", build_path)

    assert not diagnostics.ok
    assert scanner.scanned_directories == [build_path / "apps" / "auth"]
    results = json.loads((build_path / "policy_results.json").read_text(encoding="utf-8"))["policy_results"]
    assert results[0]["policy_name"] == "checkov_policy"
    assert results[0]["passed"] is False


# ---------------------------------------------------------------------------
# build_run() with all 3 newly-wired pin categories at once
# (docs/design/version-pin-overlay.md Phase 5)
# ---------------------------------------------------------------------------


def test_build_run_applies_remotes_charts_and_images_pins_simultaneously(tmp_path: Path, monkeypatch):
    """One `Version` document pinning `remotes`, `charts`, and `images` at
    once, exercised through a real `build_run()` — catches an
    ordering/interaction bug a per-category unit test could miss (e.g. one
    category's overlay accidentally undoing or short-circuiting another's).

    Combines the `remotes`-pin provisioner-source fixture
    (`_terraform_solution_with_remote`) with a namespace carrying both a
    chart-based (helm) and an image-based (compose) module."""
    from strata.controllers import remote_resolution as remote_resolution_module
    from strata.utils import layout

    root = _terraform_solution_with_remote(
        tmp_path,
        version_doc=(
            "apiVersion: strata.huybrechts.xyz/v2\nkind: version\nmeta:\n  name: prd\nspec:\n"
            "  pins:\n"
            "    remotes:\n      infra-remote: v2.0.0\n"
            "    charts:\n      authentik: 2024.2.0\n"
            "    images:\n      redis: redis:7.2\n"
        ),
    )
    _write(root, "charts/authentik/Chart.yaml", "name: authentik\n")
    _write(root, "services/redis/docker-compose.yml", "# stand-in\n")
    _write(
        root,
        "module.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: module\nmeta:\n  name: authentik\nspec:\n"
        "  source:\n    remote: infra-remote\n    chart_name: authentik\n    chart_version: 2024.1.0\n"
        "  type: helm\n  default_labels:\n    app: authentik\n  services:\n    - name: server\n",
    )
    _write(
        root,
        "module-redis.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: module\nmeta:\n  name: redis\nspec:\n"
        "  source:\n    source_path: services/redis\n  type: compose\n"
        "  default_labels:\n    app: redis\n  services:\n    - name: redis\n      image: redis:7\n",
    )
    _write(
        root,
        "namespace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: namespace\nmeta:\n  name: apps\nspec:\n"
        "  default_labels:\n    app: apps\n"
        "  modules:\n    - name: auth\n      module: authentik\n    - name: redis\n      module: redis\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  namespaces:\n    - apps\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n"
        "        remote: infra-remote\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )

    monkeypatch.setattr(remote_resolution_module, "run_command", _fake_clone_run_command)

    build_path = tmp_path / "build"
    diagnostics = build_run(_context(root), "app", build_path)
    assert diagnostics.ok, diagnostics.messages()

    # remotes: provisioner source checked out at the PINNED ref, not "main".
    assert layout.remote_checkout_path(root, "infra-remote", "v2.0.0").exists()
    assert not layout.remote_checkout_path(root, "infra-remote", "main").exists()
    assert (build_path / "infra" / "main.tf").exists()

    # charts: the pinned chart version reached the rendered meta.yaml.
    helm_meta = yaml.safe_load((build_path / "apps" / "auth" / "meta.yaml").read_text())
    assert helm_meta["chartVersion"] == "2024.2.0"

    # images: the pinned image reached the rendered docker-compose.yml.
    compose = yaml.safe_load((build_path / "apps" / "docker-compose.yml").read_text())
    assert compose["services"]["redis"]["image"] == "redis:7.2"


# ---------------------------------------------------------------------------
# build_run()'s --pin (docs/work/version-lifecycle.md Phase 5) — ephemeral,
# per-invocation override of Deployment.spec.version, zero persisted mutation.
# ---------------------------------------------------------------------------


def test_build_run_pin_produces_identical_output_to_hand_editing_spec_version(tmp_path: Path, monkeypatch):
    """The exact "Done when" criterion: a --pin'd build produces identical
    output to hand-editing spec.version and building without the flag."""
    from strata.controllers import remote_resolution as remote_resolution_module
    from strata.utils import layout

    version_doc = (
        "apiVersion: strata.huybrechts.xyz/v2\nkind: version\nmeta:\n  name: prd\nspec:\n"
        "  pins:\n    remotes:\n      infra-remote: v2.0.0\n"
    )
    monkeypatch.setattr(remote_resolution_module, "run_command", _fake_clone_run_command)

    # Hand-edited: deployment.yaml declares spec.version: prd directly.
    hand_edited_root = _terraform_solution_with_remote(tmp_path / "hand-edited", version_doc=version_doc)
    hand_edited_build = tmp_path / "hand-edited-build"
    diagnostics = build_run(_context(hand_edited_root), "app", hand_edited_build)
    assert diagnostics.ok, diagnostics.messages()

    # --pin: deployment.yaml never mentions "prd" at all.
    pinned_root = _terraform_solution_with_remote(
        tmp_path / "pinned", version_doc=version_doc, reference_version_from_deployment=False
    )
    assert "version" not in (pinned_root / "deployment.yaml").read_text(encoding="utf-8")
    pinned_build = tmp_path / "pinned-build"
    diagnostics = build_run(_context(pinned_root), "app", pinned_build, pin="prd")
    assert diagnostics.ok, diagnostics.messages()

    # Identical rendered output in both cases.
    assert (hand_edited_build / "infra" / "main.tf").read_text() == (pinned_build / "infra" / "main.tf").read_text()
    assert layout.remote_checkout_path(pinned_root, "infra-remote", "v2.0.0").exists()


def test_build_run_pin_never_mutates_the_deployment_document_on_disk(tmp_path: Path, monkeypatch):
    """The other half of the "Done when" criterion: the deployment document
    itself is provably untouched on disk afterward."""
    from strata.controllers import remote_resolution as remote_resolution_module

    monkeypatch.setattr(remote_resolution_module, "run_command", _fake_clone_run_command)
    root = _terraform_solution_with_remote(
        tmp_path,
        version_doc=(
            "apiVersion: strata.huybrechts.xyz/v2\nkind: version\nmeta:\n  name: prd\nspec:\n"
            "  pins:\n    remotes:\n      infra-remote: v2.0.0\n"
        ),
        reference_version_from_deployment=False,
    )
    deployment_path = root / "deployment.yaml"
    before = deployment_path.read_text(encoding="utf-8")

    build_run(_context(root), "app", tmp_path / "build", pin="prd")

    assert deployment_path.read_text(encoding="utf-8") == before


def test_build_run_pin_raises_for_an_unknown_version(tmp_path: Path):
    root = _terraform_solution(tmp_path)
    with pytest.raises(UsageError, match="--pin names an unknown version document: 'ghost'"):
        build_run(_context(root), "app", tmp_path / "build", pin="ghost")


def test_build_run_without_pin_uses_the_deployments_own_declared_version(tmp_path: Path, monkeypatch):
    """Control case: omitting --pin entirely behaves exactly as Phase 3/4's
    existing tests already prove — no regression from threading the new
    parameter through resolve_deployment()."""
    from strata.controllers import remote_resolution as remote_resolution_module
    from strata.utils import layout

    monkeypatch.setattr(remote_resolution_module, "run_command", _fake_clone_run_command)
    root = _terraform_solution_with_remote(tmp_path)  # no version_doc -> no pin at all

    diagnostics = build_run(_context(root), "app", tmp_path / "build")

    assert diagnostics.ok
    assert layout.remote_checkout_path(root, "infra-remote", "main").exists()


# ---------------------------------------------------------------------------
# resolved.yaml / --resolve / --env-file (docs/design/build-time-value-categories.md)
# ---------------------------------------------------------------------------


def _solution_with_values(tmp_path: Path, *, extra_environment_spec: str = "") -> Path:
    """Same minimal Terraform solution as `_terraform_solution()`, but the
    environment declares real variable/feature/secret stores instead of
    `spec: {}`."""
    root = _terraform_solution(tmp_path)
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n" + extra_environment_spec,
    )
    return root


def test_build_run_writes_resolved_yaml_with_constant_and_secret_split(tmp_path: Path):
    root = _solution_with_values(
        tmp_path,
        extra_environment_spec=(
            "  variables:\n    - key: REGION\n      store: constant\n      value: westeurope\n"
            "  secrets:\n    - key: DB_PASSWORD\n      store: constant\n      value: hunter2\n"
        ),
    )
    build_path = tmp_path / "build"

    build_run(_context(root), "app", build_path)

    manifest = yaml.safe_load((build_path / "resolved.yaml").read_text())
    assert manifest["variables"]["REGION"] == {"store": "constant", "value": "westeurope"}
    # A secret's value is never written, even for a constant store.
    assert "value" not in manifest["secrets"]["DB_PASSWORD"]
    assert manifest["secrets"]["DB_PASSWORD"]["store"] == "constant"


def test_build_run_writes_flags_and_variables_terraform_output(tmp_path: Path):
    """End to end: a constant-store variable/feature reaches the real
    Terraform provisioner's build_path, not just the graph/manifest."""
    root = _solution_with_values(
        tmp_path,
        extra_environment_spec=(
            "  variables:\n    - key: REGION\n      store: constant\n      value: westeurope\n"
            "  features:\n    - key: NEW_UI\n      store: constant\n      value: true\n"
        ),
    )
    build_path = tmp_path / "build"

    build_run(_context(root), "app", build_path)

    strata = yaml.safe_load((build_path / "infra" / "strata.auto.tfvars.json").read_text())["strata"]
    assert strata["variables"] == {"REGION": "westeurope"}
    assert strata["flags"] == {"NEW_UI": True}


def test_build_run_dry_run_does_not_write_resolved_yaml(tmp_path: Path):
    root = _terraform_solution(tmp_path)
    build_path = tmp_path / "build"

    build_run(_context(root), "app", build_path, dry_run=True)

    assert not build_path.exists()


def test_build_run_without_resolve_never_attempts_integration_backed_values(tmp_path: Path):
    """Without `--resolve`, an integration-backed secret is never touched at
    all — no diagnostics finding, not even an attempt."""
    root = _solution_with_values(
        tmp_path, extra_environment_spec="  secrets:\n    - key: VAULT_SECRET\n      store: vault\n      value: kv/x\n"
    )
    build_path = tmp_path / "build"

    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok


def test_build_run_resolve_flag_validates_every_declared_value(tmp_path: Path):
    """With `--resolve`, the same integration-backed secret IS attempted,
    and its failure is reported — but never written anywhere."""
    root = _solution_with_values(
        tmp_path, extra_environment_spec="  secrets:\n    - key: VAULT_SECRET\n      store: vault\n      value: kv/x\n"
    )
    build_path = tmp_path / "build"

    diagnostics = build_run(_context(root), "app", build_path, resolve=True)

    assert not diagnostics.ok
    assert "no resolver implemented yet" in diagnostics.messages()[0]
    # Still never written to resolved.yaml or anywhere else.
    manifest = yaml.safe_load((build_path / "resolved.yaml").read_text())
    assert "value" not in manifest["secrets"]["VAULT_SECRET"]


def test_build_run_env_file_supplies_a_missing_environment_store_variable(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PHASE3_PROBE_VAR", raising=False)
    root = _solution_with_values(
        tmp_path,
        extra_environment_spec=(
            "  variables:\n    - key: GREETING\n      store: environment\n      value: PHASE3_PROBE_VAR\n"
        ),
    )
    env_file = tmp_path / ".env"
    env_file.write_text("PHASE3_PROBE_VAR=hello-from-file\n")
    build_path = tmp_path / "build"

    build_run(_context(root), "app", build_path, env_files=[env_file])

    manifest = yaml.safe_load((build_path / "resolved.yaml").read_text())
    assert manifest["variables"]["GREETING"] == {"store": "environment", "value": "hello-from-file"}


def test_build_run_env_file_never_overrides_a_real_env_var(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PHASE3_PROBE_VAR2", "real-shell-value")
    root = _solution_with_values(
        tmp_path,
        extra_environment_spec=(
            "  variables:\n    - key: GREETING\n      store: environment\n      value: PHASE3_PROBE_VAR2\n"
        ),
    )
    env_file = tmp_path / ".env"
    env_file.write_text("PHASE3_PROBE_VAR2=from-file-should-lose\n")
    build_path = tmp_path / "build"

    build_run(_context(root), "app", build_path, env_files=[env_file])

    manifest = yaml.safe_load((build_path / "resolved.yaml").read_text())
    assert manifest["variables"]["GREETING"]["value"] == "real-shell-value"


# ---------------------------------------------------------------------------
# resolved.yaml's `promotion` section (docs/work/promotion.md Phase 4)
# ---------------------------------------------------------------------------


def _terraform_solution_with_version(tmp_path: Path, version_doc: str) -> Path:
    """Like `_terraform_solution()`, but `deployment.yaml` names a
    `kind: version` document (`version.yaml`) via `spec.version`."""
    root = _terraform_solution(tmp_path)
    _write(root, "version.yaml", version_doc)
    deployment_path = root / "deployment.yaml"
    deployment_path.write_text(deployment_path.read_text(encoding="utf-8") + "  version: prd\n", encoding="utf-8")
    return root


def test_build_run_writes_promotion_section_for_a_tagged_version(tmp_path: Path):
    root = _terraform_solution_with_version(
        tmp_path,
        "apiVersion: strata.huybrechts.xyz/v2\nkind: version\nmeta:\n  name: prd\nspec:\n"
        "  workspace: main\n  promotion:\n    ring: prd\n    order: 3\n    wave: canary\n  pins: {}\n",
    )
    build_path = tmp_path / "build"

    build_run(_context(root), "app", build_path)

    manifest = yaml.safe_load((build_path / "resolved.yaml").read_text())
    assert manifest["promotion"] == {
        "workspace": "main",
        "ring": "prd",
        "order": 3,
        "wave": "canary",
        "version": "prd",
    }


def test_build_run_promotion_section_omits_wave_when_unset(tmp_path: Path):
    root = _terraform_solution_with_version(
        tmp_path,
        "apiVersion: strata.huybrechts.xyz/v2\nkind: version\nmeta:\n  name: prd\nspec:\n"
        "  workspace: main\n  promotion:\n    ring: dev\n    order: 1\n  pins: {}\n",
    )
    build_path = tmp_path / "build"

    build_run(_context(root), "app", build_path)

    manifest = yaml.safe_load((build_path / "resolved.yaml").read_text())
    assert manifest["promotion"] == {"workspace": "main", "ring": "dev", "order": 1, "version": "prd"}
    assert "wave" not in manifest["promotion"]


def test_build_run_omits_promotion_section_for_an_untagged_version(tmp_path: Path):
    """Worked example A: a Version document with no `spec.promotion` at all
    — no section written, not a null/empty one."""
    root = _terraform_solution_with_version(
        tmp_path,
        "apiVersion: strata.huybrechts.xyz/v2\nkind: version\nmeta:\n  name: prd\nspec:\n  pins: {}\n",
    )
    build_path = tmp_path / "build"

    build_run(_context(root), "app", build_path)

    manifest = yaml.safe_load((build_path / "resolved.yaml").read_text())
    assert "promotion" not in manifest


def test_build_run_omits_promotion_section_when_deployment_has_no_version(tmp_path: Path):
    root = _terraform_solution(tmp_path)  # no version document, no spec.version at all
    build_path = tmp_path / "build"

    build_run(_context(root), "app", build_path)

    manifest = yaml.safe_load((build_path / "resolved.yaml").read_text())
    assert "promotion" not in manifest


# ---------------------------------------------------------------------------
# output.template (ADR-0023 D3, docs/design/value-token-resolution.md option C)
# ---------------------------------------------------------------------------


def test_build_run_output_template_valid_writes_nothing_but_succeeds(tmp_path: Path):
    root = _solution_with_values(
        tmp_path,
        extra_environment_spec="  variables:\n    - key: REGION\n      store: constant\n      value: westeurope\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "      output:\n        template: variables.json.j2\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(root, "variables.json.j2", '{"region": "{{ variables.REGION }}"}')
    build_path = tmp_path / "build"

    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok
    assert not (build_path / "infra" / "workspace.auto.tfvars.json").exists()
    assert (build_path / "infra" / "main.tf").exists()  # source is still synced


def test_build_run_output_template_with_bad_reference_fails(tmp_path: Path):
    root = _solution_with_values(
        tmp_path,
        extra_environment_spec="  variables:\n    - key: REGION\n      store: constant\n      value: westeurope\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "      output:\n        template: variables.json.j2\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(root, "variables.json.j2", '{"region": "{{ variables.REGOIN }}"}')
    build_path = tmp_path / "build"

    with pytest.raises(UsageError, match="variables.REGOIN"):
        build_run(_context(root), "app", build_path)


def test_build_run_output_template_missing_file_fails(tmp_path: Path):
    root = _terraform_solution(tmp_path)
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "      output:\n        template: ghost.json.j2\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    build_path = tmp_path / "build"

    with pytest.raises(UsageError, match="ghost.json.j2"):
        build_run(_context(root), "app", build_path)


def test_build_run_dry_run_still_catches_a_bad_output_template_reference(tmp_path: Path):
    """Cheap and local — dry run should catch this too, matching --resolve's
    own "dry run still catches a bad value" rule (build_run()'s own docstring)."""
    root = _solution_with_values(
        tmp_path,
        extra_environment_spec="  variables:\n    - key: REGION\n      store: constant\n      value: westeurope\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "      output:\n        template: variables.json.j2\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(root, "variables.json.j2", '{"region": "{{ variables.REGOIN }}"}')
    build_path = tmp_path / "build"

    with pytest.raises(UsageError, match="variables.REGOIN"):
        build_run(_context(root), "app", build_path, dry_run=True)

    assert not build_path.exists()  # still no filesystem mutation


def test_build_run_dry_run_reports_output_template_would_validate(tmp_path: Path):
    root = _solution_with_values(
        tmp_path,
        extra_environment_spec="  variables:\n    - key: REGION\n      store: constant\n      value: westeurope\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "      output:\n        template: variables.json.j2\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(root, "variables.json.j2", '{"region": "{{ variables.REGION }}"}')
    build_path = tmp_path / "build"
    steps: list[str] = []

    build_run(_context(root), "app", build_path, dry_run=True, on_step=steps.append)

    assert any("would validate output.template" in s for s in steps)
    assert not build_path.exists()


# NOTE: "deployment has no workspace" is not separately testable through a
# real, loadable solution — `DeploymentSpecModel.validate_complete_unless_partial()`
# already requires `workspace` for any complete, non-partial deployment, so
# `context.require_valid()` would never pass with one missing. `build_run()`'s
# own `if deployment.spec.workspace is None` check is a defensive backstop
# for that reason, not a reachable path via a valid solution.

# ---------------------------------------------------------------------------
# build_run() + clone credentials (docs/work/remotes-at-scale.md C2)
# ---------------------------------------------------------------------------

_GIT_TOKEN = "ghp_buildtimetoken123"


def _credentialed_remote_solution(tmp_path: Path, *, secret_store: str = "environment") -> Path:
    """`_terraform_solution_with_remote()` whose remote names an integration with a token read from `secret_store`."""
    root = _terraform_solution_with_remote(tmp_path)
    _write(
        root,
        "strata.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: solution\nmeta:\n  name: test-solution\nspec:\n"
        "  remotes:\n    - name: infra-remote\n      type: git\n"
        "      url: https://example.com/org/infra.git\n      reference: main\n      integration: customer-git\n",
    )
    _write(
        root,
        "integrations/customer-git.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: integration\nmeta:\n  name: customer-git\nspec:\n"
        "  type: git\n  capabilities: [sources]\n  authentication:\n    method: token\n"
        "    token:\n      token: CUSTOMER_GIT_TOKEN\n      username: build-bot\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        f"  secrets:\n    - key: CUSTOMER_GIT_TOKEN\n      store: {secret_store}\n      value: BUILD_TEST_GIT_PAT\n",
    )
    return root


def _capturing_clone(calls: list[dict]):
    def _run(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls.append({"args": list(args), "env": dict(env or {})})
        return _fake_clone_run_command(args, cwd=cwd, env=env, timeout=timeout, input=input)

    return _run


def test_build_run_clones_with_the_integrations_credentials_and_never_writes_them(tmp_path: Path, monkeypatch):
    from strata.controllers import remote_resolution as remote_resolution_module

    monkeypatch.setenv("BUILD_TEST_GIT_PAT", _GIT_TOKEN)
    calls: list[dict] = []
    monkeypatch.setattr(remote_resolution_module, "run_command", _capturing_clone(calls))
    root = _credentialed_remote_solution(tmp_path)

    build_path = tmp_path / "build"
    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok
    clone = next(call for call in calls if call["args"][:2] == ["git", "clone"])
    assert clone["env"]["STRATA_GIT_ASKPASS_TOKEN"] == _GIT_TOKEN
    assert not any(_GIT_TOKEN in arg for call in calls for arg in call["args"])
    assert (build_path / "infra" / "main.tf").exists()
    leaked = [p for p in build_path.rglob("*") if p.is_file() and _GIT_TOKEN in p.read_text(errors="ignore")]
    assert leaked == []


def test_build_run_dry_run_and_cached_checkouts_resolve_no_credentials(tmp_path: Path, monkeypatch):
    from strata.controllers import remote_resolution as remote_resolution_module
    from strata.utils import layout

    monkeypatch.delenv("BUILD_TEST_GIT_PAT", raising=False)  # resolving the credential would fail
    calls: list[dict] = []
    monkeypatch.setattr(remote_resolution_module, "run_command", _capturing_clone(calls))
    root = _credentialed_remote_solution(tmp_path)

    build_run(_context(root), "app", tmp_path / "dry", dry_run=True)

    checkout = layout.remote_checkout_path(root, "infra-remote", "main")
    (checkout / "infra").mkdir(parents=True)
    (checkout / "infra" / "main.tf").write_text("# cached\n", encoding="utf-8")
    diagnostics = build_run(_context(root), "app", tmp_path / "build")

    assert diagnostics.ok
    assert calls == []


def test_build_run_without_the_credential_value_fails_naming_the_variable(tmp_path: Path, monkeypatch):
    from strata.controllers import remote_resolution as remote_resolution_module
    from strata.controllers.remote_credentials import RemoteCredentialError

    monkeypatch.delenv("BUILD_TEST_GIT_PAT", raising=False)
    calls: list[dict] = []
    monkeypatch.setattr(remote_resolution_module, "run_command", _capturing_clone(calls))
    root = _credentialed_remote_solution(tmp_path)

    with pytest.raises(RemoteCredentialError, match="BUILD_TEST_GIT_PAT"):
        build_run(_context(root), "app", tmp_path / "build")

    assert calls == []


def test_build_run_integration_backed_credential_needs_resolve(tmp_path: Path, monkeypatch):
    from strata.controllers import remote_resolution as remote_resolution_module
    from strata.controllers.remote_credentials import RemoteCredentialError

    monkeypatch.setattr(remote_resolution_module, "run_command", _capturing_clone([]))
    root = _credentialed_remote_solution(tmp_path, secret_store="infisical")

    with pytest.raises(RemoteCredentialError, match="--resolve"):
        build_run(_context(root), "app", tmp_path / "build")


@pytest.mark.parametrize("resolve", [False, True])
def test_build_run_creates_the_resolver_with_the_resolve_flag_and_pin(tmp_path: Path, monkeypatch, resolve: bool):
    from strata.controllers import build_controller as build_controller_module
    from strata.controllers import remote_resolution as remote_resolution_module

    monkeypatch.setenv("BUILD_TEST_GIT_PAT", _GIT_TOKEN)
    monkeypatch.setattr(remote_resolution_module, "run_command", _capturing_clone([]))
    created: list[dict] = []
    real = build_controller_module.RemoteCredentialResolver

    def _spy(context, deployment_name, **kwargs):
        created.append({"deployment": deployment_name, **kwargs})
        return real(context, deployment_name, **kwargs)

    monkeypatch.setattr(build_controller_module, "RemoteCredentialResolver", _spy)
    root = _credentialed_remote_solution(tmp_path)

    build_run(_context(root), "app", tmp_path / "build", resolve=resolve)

    assert created == [{"deployment": "app", "allow_stores": resolve, "pin": None}]
