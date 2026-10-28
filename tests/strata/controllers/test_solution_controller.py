#!/usr/bin/env python3
"""Tests for SolutionController — root discovery, indexing and resolution."""

import json
import shutil
from pathlib import Path

import pytest

from strata.controllers.build_controller import build_run
from strata.controllers.deployment_resolution import resolve_deployment_chains
from strata.controllers.solution_context import open_solution
from strata.controllers.solution_controller import (
    DocumentRef,
    SolutionController,
    find_solution_root,
)
from strata.models.common_models import PlatformKind
from strata.models.workspace_model import WorkspaceModel
from strata.services.workspace_service import effective_assets, effective_providers

MANIFEST = """\
apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec:
  configuration: configuration.yaml
"""

CONFIGURATION = """\
apiVersion: strata.huybrechts.xyz/v2
kind: configuration
meta:
  name: test-config
spec:
  providers: [azure]
"""

NAMESPACE_DOC = """\
apiVersion: strata.huybrechts.xyz/v2
kind: namespace
meta:
  name: main-ns
spec:
  lifecycle: {}
"""


def _solution(tmp_path: Path) -> Path:
    """A minimal valid solution tree."""
    (tmp_path / "strata.yaml").write_text(MANIFEST, encoding="utf-8")
    (tmp_path / "configuration.yaml").write_text(CONFIGURATION, encoding="utf-8")
    nested = tmp_path / "deep" / "nested"
    nested.mkdir(parents=True)
    (nested / "namespace.yaml").write_text(NAMESPACE_DOC, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Root discovery
# ---------------------------------------------------------------------------


def test_find_solution_root_walks_up_from_nested_directory(tmp_path):
    """The root is found from any depth below it, like go.mod/Cargo.toml."""
    root = _solution(tmp_path)
    assert find_solution_root(root / "deep" / "nested") == root.resolve()


def test_find_solution_root_returns_none_outside_a_solution(tmp_path):
    """A directory with no strata.yaml above it is not inside a solution."""
    assert find_solution_root(tmp_path) is None


def test_from_directory_returns_none_outside_a_solution(tmp_path):
    """The convenience constructor mirrors find_solution_root."""
    assert SolutionController.from_directory(tmp_path) is None


# ---------------------------------------------------------------------------
# Discovery and indexing
# ---------------------------------------------------------------------------


def test_load_indexes_documents_regardless_of_folder_layout(tmp_path):
    """Kind comes from the document's own field — folders carry no meaning."""
    controller = SolutionController(_solution(tmp_path))
    result = controller.load()
    assert result.ok, result.messages()
    assert controller.solution is not None
    assert controller.solution.meta.name == "test-solution"
    assert controller.index.names_of(PlatformKind.CONFIGURATION) == {"test-config"}
    assert controller.index.names_of(PlatformKind.NAMESPACE) == {"main-ns"}


def test_load_indexes_asset_document(tmp_path):
    """A standalone `kind: asset` document (docs/design/asset-management.md
    Phase 1) round-trips through discovery/validation like any other kind."""
    root = _solution(tmp_path)
    (root / "asset.yaml").write_text(
        "apiVersion: strata.huybrechts.xyz/v2\n"
        "kind: asset\n"
        "meta:\n"
        "  name: openapi\n"
        "spec:\n"
        "  source_path: assets/contoso/{deploy_tier}/openapi\n",
        encoding="utf-8",
    )

    controller = SolutionController(root)
    result = controller.load()
    assert result.ok, result.messages()
    assert controller.index.names_of(PlatformKind.ASSET) == {"openapi"}
    entry = controller.index.get(PlatformKind.ASSET, "openapi")
    assert entry is not None
    assert entry.model.spec.source_path == "assets/contoso/{deploy_tier}/openapi"  # type: ignore[attr-defined]


def test_load_reports_missing_manifest(tmp_path):
    """A root without strata.yaml is an error, not a crash."""
    result = SolutionController(tmp_path).load()
    assert not result.ok
    assert any("strata.yaml" in m for m in result.messages())


def test_load_reports_unparseable_yaml_with_the_offending_line(tmp_path):
    """libyaml's own message omits the line; the diagnostic must still show it."""
    root = _solution(tmp_path)
    (root / "broken.yaml").write_text("apiVersion: strata.huybrechts.xyz/v2\nkind: a: b\n", encoding="utf-8")

    result = SolutionController(root).load()

    assert not result.ok
    finding = next(d for d in result.errors if d.code == "unparseable")
    assert "broken.yaml" in (finding.source or "")
    assert "kind: a: b" in finding.message


def test_load_never_constructs_python_objects_from_yaml_tags(tmp_path):
    root = _solution(tmp_path)
    marker = tmp_path / "must-not-exist"
    (root / "evil.yaml").write_text(
        f"apiVersion: strata.huybrechts.xyz/v2\nkind: namespace\nmeta: !!python/object/apply:os.mkdir ['{marker.as_posix()}']\n",
        encoding="utf-8",
    )

    result = SolutionController(root).load()

    assert not marker.exists()
    assert any(d.code == "unparseable" for d in result.errors)


def test_load_skips_non_strata_yaml_silently(tmp_path):
    """Helm values, CI pipelines and k8s manifests must not break discovery."""
    root = _solution(tmp_path)
    (root / "docker-compose.yaml").write_text("services:\n  web:\n    image: nginx\n", encoding="utf-8")
    (root / "ci.yml").write_text("stages:\n  - build\n", encoding="utf-8")

    result = SolutionController(root).load()
    assert result.ok, result.messages()


def test_load_errors_on_strata_document_with_unknown_kind(tmp_path):
    """A strata apiVersion with a bogus kind is a typo, not a foreign file."""
    root = _solution(tmp_path)
    (root / "typo.yaml").write_text(
        "apiVersion: strata.huybrechts.xyz/v2\nkind: frobnicator\nmeta:\n  name: x\n", encoding="utf-8"
    )

    result = SolutionController(root).load()
    assert not result.ok
    assert any("unknown kind 'frobnicator'" in m for m in result.messages())


def test_load_indexes_multi_document_files(tmp_path):
    """A single file may hold several documents separated by '---'."""
    root = _solution(tmp_path)
    second = NAMESPACE_DOC.replace("main-ns", "other-ns")
    (root / "both.yaml").write_text(NAMESPACE_DOC.replace("main-ns", "a-ns") + "---\n" + second, encoding="utf-8")

    controller = SolutionController(root)
    result = controller.load()
    assert result.ok, result.messages()
    assert controller.index.names_of(PlatformKind.NAMESPACE) == {"a-ns", "other-ns", "main-ns"}


def test_load_rejects_duplicate_identity_naming_both_files(tmp_path):
    """Duplicate (kind, name) is a hard error — and must say where both are."""
    root = _solution(tmp_path)
    (root / "copy.yaml").write_text(NAMESPACE_DOC, encoding="utf-8")

    result = SolutionController(root).load()
    assert not result.ok
    duplicate = [m for m in result.messages() if "Duplicate" in m]
    assert duplicate
    assert "copy.yaml" in duplicate[0]
    assert "namespace.yaml" in duplicate[0]


def test_load_stops_at_a_nested_solution_boundary(tmp_path):
    """A nested strata.yaml marks a different solution — do not index into it."""
    root = _solution(tmp_path)
    vendor = root / "vendor" / "other"
    vendor.mkdir(parents=True)
    (vendor / "strata.yaml").write_text(MANIFEST.replace("test-solution", "other-solution"), encoding="utf-8")
    (vendor / "leak.yaml").write_text(NAMESPACE_DOC.replace("main-ns", "should-not-appear"), encoding="utf-8")

    controller = SolutionController(root)
    result = controller.load()
    assert result.ok, result.messages()
    assert "should-not-appear" not in controller.index.names_of(PlatformKind.NAMESPACE)


def test_load_skips_ignored_directories(tmp_path):
    """Tool caches and runtime state are never descended into."""
    root = _solution(tmp_path)
    for ignored in (".git", "build", ".strata"):
        directory = root / ignored
        directory.mkdir()
        (directory / "stray.yaml").write_text(NAMESPACE_DOC.replace("main-ns", f"in-{ignored}"), encoding="utf-8")

    controller = SolutionController(root)
    controller.load()
    names = controller.index.names_of(PlatformKind.NAMESPACE)
    assert names == {"main-ns"}


# ---------------------------------------------------------------------------
# spec.discovery.exclude
# ---------------------------------------------------------------------------


def _solution_excluding(tmp_path: Path, *patterns: str) -> Path:
    """A solution whose manifest declares extra exclude patterns."""
    root = _solution(tmp_path)
    rendered = "\n".join(f"      - {p!r}" for p in patterns)
    (root / "strata.yaml").write_text(MANIFEST + f"  discovery:\n    exclude:\n{rendered}\n", encoding="utf-8")
    return root


def test_exclude_skips_a_matching_directory(tmp_path):
    """Scaffolding directories can be excluded from discovery."""
    root = _solution_excluding(tmp_path, "templates/**")
    templates = root / "templates"
    templates.mkdir()
    (templates / "scaffold.yaml").write_text(NAMESPACE_DOC.replace("main-ns", "scaffolded"), encoding="utf-8")

    controller = SolutionController(root)
    result = controller.load()
    assert result.ok, result.messages()
    assert controller.index.names_of(PlatformKind.NAMESPACE) == {"main-ns"}


def test_exclude_matches_the_bare_directory_name_too(tmp_path):
    """'templates' works as well as 'templates/**'."""
    root = _solution_excluding(tmp_path, "templates")
    templates = root / "templates"
    templates.mkdir()
    (templates / "scaffold.yaml").write_text(NAMESPACE_DOC.replace("main-ns", "scaffolded"), encoding="utf-8")

    controller = SolutionController(root)
    controller.load()
    assert controller.index.names_of(PlatformKind.NAMESPACE) == {"main-ns"}


def test_exclude_skips_an_individual_file(tmp_path):
    """Patterns match files, not just directories."""
    root = _solution_excluding(tmp_path, "*.generated.yaml")
    (root / "thing.generated.yaml").write_text(NAMESPACE_DOC.replace("main-ns", "generated"), encoding="utf-8")

    controller = SolutionController(root)
    controller.load()
    assert controller.index.names_of(PlatformKind.NAMESPACE) == {"main-ns"}


def test_exclude_cannot_re_enable_built_in_ignores(tmp_path):
    """User patterns are additive — they never shrink the built-in floor."""
    root = _solution_excluding(tmp_path, "nothing-matching/**")
    git_dir = root / ".git"
    git_dir.mkdir()
    (git_dir / "stray.yaml").write_text(NAMESPACE_DOC.replace("main-ns", "in-git"), encoding="utf-8")

    controller = SolutionController(root)
    controller.load()
    assert "in-git" not in controller.index.names_of(PlatformKind.NAMESPACE)


def test_no_discovery_block_means_no_extra_exclusions(tmp_path):
    """spec.discovery is optional; omitting it changes nothing."""
    root = _solution(tmp_path)
    extra = root / "templates"
    extra.mkdir()
    (extra / "scaffold.yaml").write_text(NAMESPACE_DOC.replace("main-ns", "scaffolded"), encoding="utf-8")

    controller = SolutionController(root)
    controller.load()
    assert controller.index.names_of(PlatformKind.NAMESPACE) == {"main-ns", "scaffolded"}


def test_load_rejects_nested_solution_document_in_the_tree(tmp_path):
    """Only the root manifest may be a 'solution' document."""
    root = _solution(tmp_path)
    (root / "second-solution.yaml").write_text(MANIFEST.replace("test-solution", "extra"), encoding="utf-8")

    result = SolutionController(root).load()
    assert not result.ok
    assert any("nested 'solution' document" in m for m in result.messages())


def test_load_reports_invalid_document_with_its_path(tmp_path):
    """Schema errors carry the file they came from — provenance replaces `file:`."""
    root = _solution(tmp_path)
    (root / "bad.yaml").write_text(
        "apiVersion: strata.huybrechts.xyz/v2\nkind: namespace\nmeta:\n  name: broken\nspec:\n  type: kubernetes\n",
        encoding="utf-8",
    )

    result = SolutionController(root).load()
    assert not result.ok
    assert any("bad.yaml" in m for m in result.messages())


# ---------------------------------------------------------------------------
# Index API
# ---------------------------------------------------------------------------


def test_index_get_returns_none_for_unknown_name(tmp_path):
    """A miss is a None, not an exception."""
    controller = SolutionController(_solution(tmp_path))
    controller.load()
    assert controller.index.get(PlatformKind.NAMESPACE, "nope") is None


def test_index_entry_carries_its_source_path(tmp_path):
    """Every entry knows the file it came from."""
    root = _solution(tmp_path)
    controller = SolutionController(root)
    controller.load()
    entry = controller.index.get(PlatformKind.NAMESPACE, "main-ns")
    assert entry is not None
    assert entry.source.name == "namespace.yaml"


def test_document_ref_str_includes_remote_when_set():
    """The remote slot is unused today but renders when present (ADR-0015)."""
    assert str(DocumentRef(kind=PlatformKind.NAMESPACE, name="main")) == "namespace/main"
    assert str(DocumentRef(kind=PlatformKind.NAMESPACE, name="main", remote="shared")) == "@shared/namespace/main"


# ---------------------------------------------------------------------------
# The shipped example solution
# ---------------------------------------------------------------------------


def test_document_index_names_of_tracks_each_added_document_per_kind():
    from strata.controllers.solution_controller import DocumentIndex, DocumentRef, IndexEntry
    from strata.models.asset_model import AssetModel

    index = DocumentIndex()
    assert index.names_of(PlatformKind.ASSET) == set()

    for name in ("a-api", "b-api"):
        model = AssetModel.model_validate({"meta": {"name": name}, "spec": {"source_path": "api"}})
        index.add(IndexEntry(ref=DocumentRef(kind=PlatformKind.ASSET, name=name), model=model, source=Path(name)))

    assert index.names_of(PlatformKind.ASSET) == {"a-api", "b-api"}
    assert index.names_of(PlatformKind.TENANT) == set()


def _repo_config_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "config"


@pytest.mark.skipif(not _repo_config_dir().exists(), reason="example config not present")
def test_shipped_example_solution_loads_cleanly():
    """The example solution under config/ must stay valid as models evolve."""
    controller = SolutionController(_repo_config_dir())
    result = controller.load()
    assert result.ok, result.messages()
    assert controller.index.names_of(PlatformKind.WORKSPACE) == {"main"}
    assert controller.index.names_of(PlatformKind.RESOURCE) == {
        "storage-account",
        "linux-vm",
        "key-vault",
        "app-gateway-waf",
    }


@pytest.mark.skipif(not _repo_config_dir().exists(), reason="example config not present")
def test_shipped_example_rebinds_the_provider_slot_for_the_second_region():
    """The same workspace targets westeurope by default and northeurope through the
    `region-north` base — the example doubles as the provider-slot dogfood."""
    controller = SolutionController(_repo_config_dir())
    assert controller.load().ok
    resolved, diagnostics = resolve_deployment_chains(controller.index)
    assert diagnostics.ok, diagnostics.messages()
    workspace_entry = controller.index.get(PlatformKind.WORKSPACE, "main")
    assert workspace_entry is not None
    workspace = workspace_entry.model
    assert isinstance(workspace, WorkspaceModel)

    assert effective_providers(workspace, resolved["prd-deployment"]) == {"azure": "azure-main"}
    assert effective_providers(workspace, resolved["prd-north-deployment"]) == {"azure": "azure-north"}
    assert "region-north" not in resolved  # a partial base is never deployable on its own


@pytest.mark.skipif(not _repo_config_dir().exists(), reason="example config not present")
def test_shipped_example_binds_the_asset_slot_per_customer_without_a_manifest_remote():
    """One `api` slot, three deployments, three different Asset documents; the customer
    assets carry their own `location`, so no `remotes:` entry in strata.yaml names them."""
    controller = SolutionController(_repo_config_dir())
    assert controller.load().ok
    resolved, diagnostics = resolve_deployment_chains(controller.index)
    assert diagnostics.ok, diagnostics.messages()
    workspace_entry = controller.index.get(PlatformKind.WORKSPACE, "main")
    assert workspace_entry is not None
    workspace = workspace_entry.model
    assert isinstance(workspace, WorkspaceModel)

    assert effective_assets(workspace, resolved["prd-deployment"]) == {"api": "api-default"}
    assert effective_assets(workspace, resolved["prd-c0062-deployment"]) == {"api": "c0062-api"}
    assert effective_assets(workspace, resolved["prd-c0063-deployment"]) == {"api": "c0063-api"}
    assert controller.solution is not None
    remote_names = {remote.name for remote in controller.solution.spec.remotes or []}
    assert not remote_names & {"c0062-api", "c0063-api"}


@pytest.fixture
def terraform_on_path(monkeypatch):
    """The example requires terraform >= 1.7 at build preflight; CI runners do not have it."""
    import strata.integrations.base as base_module
    from strata.utils.transport import CommandResult

    real_which, real_run = shutil.which, base_module.run_command

    def _which(cmd: str) -> str | None:
        return f"/usr/bin/{cmd}" if cmd == "terraform" else real_which(cmd)

    def _run(args, **kwargs):
        if Path(args[0]).stem == "terraform":
            return CommandResult(returncode=0, stdout="Terraform v1.9.2", stderr="")
        return real_run(args, **kwargs)

    monkeypatch.setattr(base_module.shutil, "which", _which)
    monkeypatch.setattr(base_module, "run_command", _run)


@pytest.mark.skipif(not _repo_config_dir().exists(), reason="example config not present")
def test_shipped_example_builds_offline_and_projects_the_default_asset(tmp_path, terraform_on_path):
    """The default deployment's asset comes from the local `infra` stand-in, so the example
    builds end to end with no network; the customer deployments need their own repositories."""
    root = tmp_path / "config"
    shutil.copytree(_repo_config_dir(), root, ignore=shutil.ignore_patterns(".strata", "build"))
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()

    assert build_run(context, "prd-deployment", tmp_path / "build").ok

    tfvars = json.loads((tmp_path / "build" / "terraform" / "main" / "strata.auto.tfvars.json").read_text())
    assert tfvars["strata"]["assets"]["api"] == {
        "asset": "api-default",
        "path": "assets/api",
        "path_from_step": "../../assets/api",
        "files": ["openapi.json"],
    }
