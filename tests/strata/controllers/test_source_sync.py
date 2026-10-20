#!/usr/bin/env python3
"""Tests for `source_sync.sync_source` (ADR-0022 D3)."""

import shutil
import subprocess
from pathlib import Path

import pytest

from strata.controllers.remote_resolution import RemoteResolutionError
from strata.controllers.source_sync import (
    SourceSyncError,
    resolved_asset_info,
    sync_asset,
    sync_module_source,
    sync_source,
)
from strata.models.asset_model import AssetModel
from strata.models.common_models import SourceModel
from strata.models.solution_model import RemoteType, SolutionRemoteModel
from strata.utils import layout


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_source_materialises_mirroring_its_own_source_path(tmp_path: Path):
    """No remote (same-repo source) - destination mirrors source_path exactly."""
    root = tmp_path / "solution"
    _write(root / "deploy" / "terraform" / "main.tf", "# root module")
    build_path = tmp_path / "build"

    source = SourceModel(source_path="deploy/terraform")
    destination = sync_source(root, build_path, source, remotes={})

    assert destination == build_path / "deploy" / "terraform"
    assert (destination / "main.tf").read_text() == "# root module"


def test_target_path_overrides_the_default_placement(tmp_path: Path):
    root = tmp_path / "solution"
    _write(root / "deploy" / "terraform" / "main.tf", "# root module")
    build_path = tmp_path / "build"

    source = SourceModel(source_path="deploy/terraform", target_path="terraform")
    destination = sync_source(root, build_path, source, remotes={})

    assert destination == build_path / "terraform"


def test_sibling_provisioners_relative_composition_resolves_correctly(tmp_path: Path):
    """The exact real gap ADR-0022 D3 found: a spoke root composing a shared
    module library via a relative '../../core/terraform/...' path must
    resolve correctly once both are independently materialised - no
    cross-provisioner awareness needed in sync_source() itself."""
    root = tmp_path / "solution"
    _write(root / "spoke" / "terraform" / "main.tf", 'module "aks" { source = "../../core/terraform/components/aks" }')
    _write(root / "core" / "terraform" / "components" / "aks" / "main.tf", "# aks component")
    build_path = tmp_path / "build"

    spoke_dest = sync_source(root, build_path, SourceModel(source_path="spoke/terraform"), remotes={})
    core_dest = sync_source(root, build_path, SourceModel(source_path="core/terraform"), remotes={})

    # From spoke's materialised root.tf, "../../core/terraform/components/aks"
    # must resolve to exactly where core_modules landed.
    resolved = (spoke_dest / "../../core/terraform/components/aks").resolve()
    assert resolved == (core_dest / "components" / "aks").resolve()


def test_local_type_remote_is_resolved_before_copying(tmp_path: Path):
    """LOCAL remotes reject '..' traversal (their own validator) - so the
    remote necessarily points at a subdirectory of the solution root, not a
    sibling folder outside it."""
    solution_root = tmp_path / "solution"
    _write(solution_root / "vendor" / "infra-repo" / "infra" / "main.tf", "# external module")
    build_path = tmp_path / "build"

    remote = SolutionRemoteModel(name="infra-repo", type=RemoteType.LOCAL, url="vendor/infra-repo")

    source = SourceModel(remote="infra-repo", source_path="infra")
    destination = sync_source(solution_root, build_path, source, remotes={"infra-repo": remote})

    assert (destination / "main.tf").read_text() == "# external module"


def test_chart_based_source_is_out_of_scope(tmp_path: Path):
    source = SourceModel(remote="charts", chart_name="authentik")
    with pytest.raises(SourceSyncError, match="chart-based"):
        sync_source(tmp_path, tmp_path / "build", source, remotes={})


def test_unknown_remote_name_raises_clear_error(tmp_path: Path):
    source = SourceModel(remote="ghost", source_path="infra")
    with pytest.raises(SourceSyncError, match="not declared"):
        sync_source(tmp_path, tmp_path / "build", source, remotes={})


def test_missing_source_path_on_disk_raises_clear_error(tmp_path: Path):
    root = tmp_path / "solution"
    root.mkdir()
    source = SourceModel(source_path="does/not/exist")
    with pytest.raises(SourceSyncError, match="does not exist"):
        sync_source(root, tmp_path / "build", source, remotes={})


def test_single_file_source_is_copied_not_treated_as_a_directory(tmp_path: Path):
    root = tmp_path / "solution"
    _write(root / "deploy" / "compose.yml", "services: {}")
    build_path = tmp_path / "build"

    source = SourceModel(source_path="deploy/compose.yml")
    destination = sync_source(root, build_path, source, remotes={})

    assert destination == build_path / "deploy" / "compose.yml"
    assert destination.read_text() == "services: {}"


# ---------------------------------------------------------------------------
# sync_module_source() — ADR-0022 D6/D7's workload-module variant
# ---------------------------------------------------------------------------


def test_module_source_copies_flat_into_module_dir_not_by_source_path(tmp_path: Path):
    """Unlike sync_source(), destination is always module_dir itself - never
    derived from source.source_path/.target_path."""
    root = tmp_path / "solution"
    _write(root / "charts" / "authentik" / "Chart.yaml", "name: authentik")
    module_dir = tmp_path / "build" / "apps" / "authentik"

    sync_module_source(root, module_dir, SourceModel(source_path="charts/authentik"), remotes={})

    assert (module_dir / "Chart.yaml").read_text() == "name: authentik"


def test_module_source_chart_based_creates_module_dir_but_copies_nothing(tmp_path: Path):
    """Unlike sync_source() (which raises for provisioners - no real example
    uses chart-based sourcing there), a registry chart is Helm's real
    primary use case for a module - nothing to copy, meta.yaml carries the
    chart coordinates instead. module_dir must still be created - nothing
    else does, and values.yaml/meta.yaml must land somewhere (a real,
    previously-reachable FileNotFoundError this test now guards against)."""
    module_dir = tmp_path / "build" / "apps" / "authentik"
    source = SourceModel(remote="goauthentik", chart_name="authentik")

    sync_module_source(tmp_path, module_dir, source, remotes={})

    assert module_dir.is_dir()
    assert list(module_dir.iterdir()) == []


def test_module_source_same_document_attached_twice_does_not_collide(tmp_path: Path):
    """The exact real gap this function's destination convention exists to
    avoid: the same Module document (same source.source_path) attached
    under two different reference names must land in two different
    directories."""
    root = tmp_path / "solution"
    _write(root / "charts" / "authentik" / "Chart.yaml", "name: authentik")
    source = SourceModel(source_path="charts/authentik")

    dir_a = tmp_path / "build" / "apps" / "auth-primary"
    dir_b = tmp_path / "build" / "apps" / "auth-secondary"
    sync_module_source(root, dir_a, source, remotes={})
    sync_module_source(root, dir_b, source, remotes={})

    assert (dir_a / "Chart.yaml").exists()
    assert (dir_b / "Chart.yaml").exists()
    assert dir_a != dir_b


# ---------------------------------------------------------------------------
# sync_asset() — docs/design/asset-management.md Phase 3
# ---------------------------------------------------------------------------


def _asset(source_path: str, remote: str | None = None) -> AssetModel:
    spec: dict[str, object] = {"source_path": source_path}
    if remote is not None:
        spec["remote"] = remote
    return AssetModel.model_validate({"meta": {"name": "openapi"}, "spec": spec})


def test_asset_fills_source_path_from_properties_and_copies_a_directory(tmp_path: Path):
    root = tmp_path / "solution"
    _write(root / "assets" / "contoso" / "prod" / "openapi" / "openapi.json", "{}")
    build_path = tmp_path / "build"

    destination = sync_asset(
        root,
        build_path,
        _asset("assets/{customer}/{deploy_tier}/openapi"),
        properties={"customer": "contoso", "deploy_tier": "prod"},
        remotes={},
    )

    assert destination == build_path / "assets" / "openapi"
    assert (destination / "openapi.json").read_text() == "{}"


def test_asset_single_file_source_lands_inside_the_destination_directory(tmp_path: Path):
    root = tmp_path / "solution"
    _write(root / "assets" / "cert.pem", "---cert---")
    build_path = tmp_path / "build"

    destination = sync_asset(
        root, build_path, _asset("assets/{deploy_tier}.pem"), properties={"deploy_tier": "cert"}, remotes={}
    )

    assert destination == build_path / "assets" / "openapi"
    assert (destination / "cert.pem").read_text() == "---cert---"


def test_asset_resolves_against_a_declared_remote(tmp_path: Path):
    solution_root = tmp_path / "solution"
    _write(solution_root / "vendor" / "assets-repo" / "openapi" / "openapi.json", "{}")
    build_path = tmp_path / "build"
    remote = SolutionRemoteModel(name="assets-repo", type=RemoteType.LOCAL, url="vendor/assets-repo")

    destination = sync_asset(
        solution_root,
        build_path,
        _asset("openapi", remote="assets-repo"),
        properties={},
        remotes={"assets-repo": remote},
    )

    assert (destination / "openapi.json").read_text() == "{}"


def test_asset_unknown_remote_name_raises_clear_error(tmp_path: Path):
    with pytest.raises(SourceSyncError, match="not declared"):
        sync_asset(tmp_path, tmp_path / "build", _asset("openapi", remote="ghost"), properties={}, remotes={})


def test_asset_missing_token_value_raises_clear_error(tmp_path: Path):
    """A `{name}` token with no matching properties key is a clear build-time
    error, not a silent empty-string substitution."""
    with pytest.raises(SourceSyncError, match="Unknown token"):
        sync_asset(tmp_path, tmp_path / "build", _asset("assets/{deploy_tier}/openapi"), properties={}, remotes={})


def test_asset_missing_on_disk_variant_raises_clear_error(tmp_path: Path):
    root = tmp_path / "solution"
    root.mkdir()
    with pytest.raises(SourceSyncError, match="does not exist"):
        sync_asset(
            root,
            tmp_path / "build",
            _asset("assets/{deploy_tier}/openapi"),
            properties={"deploy_tier": "prod"},
            remotes={},
        )


def test_asset_unresolved_value_token_in_filling_property_raises_dedicated_diagnostic(tmp_path: Path):
    """A property value that still contains an unresolved '${var:}'-style
    token (deploy-time-only, docs/design/value-token-resolution.md) must
    fail with a clear diagnostic, not a confusing 'path does not exist'
    error (Remaining Work #2)."""
    root = tmp_path / "solution"
    root.mkdir()
    with pytest.raises(SourceSyncError, match="unresolved '\\$\\{\\.\\.\\.\\}' token"):
        sync_asset(
            root,
            tmp_path / "build",
            _asset("assets/{deploy_tier}/openapi"),
            properties={"deploy_tier": "${var:TIER}"},
            remotes={},
        )


@pytest.mark.parametrize(
    "value",
    [
        pytest.param({"size": "large"}, id="dict"),
        pytest.param(["eu", "us"], id="list"),
        pytest.param(True, id="bool-true"),
        pytest.param(False, id="bool-false"),
    ],
)
def test_asset_non_scalar_filling_property_raises_clear_error(tmp_path: Path, value: object):
    """A dict/list/bool property would stringify into a nonsense path
    segment ("assets/{'size': 'large'}/openapi", "assets/True/openapi") and
    only fail later as a confusing "path does not exist" — reject it up
    front instead (review finding 4). `bool` is rejected deliberately even
    though `isinstance(True, int)` is True in Python."""
    root = tmp_path / "solution"
    root.mkdir()
    with pytest.raises(SourceSyncError, match="cannot be a path segment"):
        sync_asset(
            root,
            tmp_path / "build",
            _asset("assets/{deploy_tier}/openapi"),
            properties={"deploy_tier": value},
            remotes={},
        )


@pytest.mark.parametrize(
    ("value", "expected_segment"),
    [
        pytest.param("prod", "prod", id="str"),
        pytest.param(3, "3", id="int"),
        pytest.param(1.5, "1.5", id="float"),
    ],
)
def test_asset_scalar_filling_property_is_accepted(tmp_path: Path, value: object, expected_segment: str):
    """str/int/float all fill a segment — a numeric tier/ring/version is a
    legitimate path segment, so only dict/list/bool are rejected."""
    root = tmp_path / "solution"
    _write(root / "assets" / expected_segment / "openapi" / "openapi.json", "{}")
    build_path = tmp_path / "build"

    destination = sync_asset(
        root,
        build_path,
        _asset("assets/{deploy_tier}/openapi"),
        properties={"deploy_tier": value},
        remotes={},
    )

    assert (destination / "openapi.json").read_text() == "{}"


# Review finding 2 — a filled `{name}` token can inject traversal that no
# validation of the *declared* source_path string could have caught.


@pytest.mark.parametrize(
    ("source_path", "value"),
    [
        pytest.param("assets/{deploy_tier}/openapi", "../../../..", id="parent-traversal"),
        pytest.param("{deploy_tier}/openapi", "/etc", id="absolute"),
    ],
)
def test_asset_token_value_cannot_escape_the_remote_root(tmp_path: Path, source_path: str, value: str):
    root = tmp_path / "solution"
    root.mkdir()
    with pytest.raises(SourceSyncError, match="resolves outside"):
        sync_asset(
            root,
            tmp_path / "build",
            _asset(source_path),
            properties={"deploy_tier": value},
            remotes={},
        )


def test_asset_glob_match_cannot_escape_the_remote_root(tmp_path: Path):
    """Same escape, but through a glob — the containment check has to run per
    match, since the pattern itself never names the escaping path literally."""
    root = tmp_path / "solution"
    root.mkdir()
    _write(tmp_path / "outside" / "secret.json", "{}")

    with pytest.raises(SourceSyncError, match="resolves outside"):
        sync_asset(
            root,
            tmp_path / "build",
            _asset("{deploy_tier}/*.json"),
            properties={"deploy_tier": "../outside"},
            remotes={},
        )


def test_asset_glob_escape_is_detected_before_the_previous_slot_directory_is_cleared(tmp_path: Path):
    root = tmp_path / "solution"
    root.mkdir()
    _write(tmp_path / "outside" / "secret.json", "{}")
    build_path = tmp_path / "build"
    _write(build_path / "assets" / "api" / "old.json", "{}")

    with pytest.raises(SourceSyncError, match="resolves outside"):
        sync_asset(
            root,
            build_path,
            _asset("{deploy_tier}/*.json"),
            properties={"deploy_tier": "../outside"},
            remotes={},
            slot="api",
        )

    assert (build_path / "assets" / "api" / "old.json").exists()


# Review finding 3 — glob-aware source_path, preserving structure.


def _glob_tree(tmp_path: Path) -> Path:
    root = tmp_path / "solution"
    _write(root / "assets" / "openapi" / "api.json", "{}")
    _write(root / "assets" / "openapi" / "api.xml", "<x/>")
    _write(root / "assets" / "openapi" / "nested" / "deep.json", "{}")
    return root


def test_asset_glob_selects_only_matching_files(tmp_path: Path):
    """`*.json` takes the json and leaves the xml — the author selects, strata
    no longer guesses."""
    root = _glob_tree(tmp_path)
    destination = sync_asset(root, tmp_path / "build", _asset("assets/openapi/*.json"), properties={}, remotes={})

    assert (destination / "api.json").exists()
    assert not (destination / "api.xml").exists()
    assert not (destination / "nested").exists()


def test_asset_recursive_glob_preserves_nested_structure(tmp_path: Path):
    """`**/*.json` keeps each match's path relative to the pattern's last
    wildcard-free directory, so two same-named files in different directories
    cannot silently overwrite each other."""
    root = _glob_tree(tmp_path)
    destination = sync_asset(root, tmp_path / "build", _asset("assets/openapi/**/*.json"), properties={}, remotes={})

    assert (destination / "api.json").exists()
    assert (destination / "nested" / "deep.json").exists()
    assert not (destination / "deep.json").exists()  # not flattened


def test_asset_glob_combines_with_a_filled_token(tmp_path: Path):
    root = tmp_path / "solution"
    _write(root / "assets" / "prod" / "openapi" / "api.json", "{}")
    _write(root / "assets" / "prod" / "openapi" / "api.xml", "<x/>")

    destination = sync_asset(
        root,
        tmp_path / "build",
        _asset("assets/{deploy_tier}/openapi/*.json"),
        properties={"deploy_tier": "prod"},
        remotes={},
    )

    assert (destination / "api.json").exists()
    assert not (destination / "api.xml").exists()


def test_asset_glob_matching_nothing_raises_clear_error(tmp_path: Path):
    root = _glob_tree(tmp_path)
    with pytest.raises(SourceSyncError, match="matched no files"):
        sync_asset(root, tmp_path / "build", _asset("assets/openapi/*.yaml"), properties={}, remotes={})


def test_asset_without_a_wildcard_still_copies_a_whole_directory(tmp_path: Path):
    """Backward compatibility — a wildcard-free source_path behaves exactly as
    it did before globs were supported."""
    root = _glob_tree(tmp_path)
    destination = sync_asset(root, tmp_path / "build", _asset("assets/openapi"), properties={}, remotes={})

    assert (destination / "api.json").exists()
    assert (destination / "api.xml").exists()
    assert (destination / "nested" / "deep.json").exists()


def test_module_source_unknown_remote_raises_clear_error(tmp_path: Path):
    source = SourceModel(remote="ghost", source_path="charts/authentik")
    with pytest.raises(SourceSyncError, match="not declared"):
        sync_module_source(tmp_path, tmp_path / "build" / "apps" / "authentik", source, remotes={})


def test_resolved_asset_info_lists_files_recursively_and_sorted(tmp_path: Path):
    build_path = tmp_path / "build"
    _write(build_path / "assets" / "api" / "b.json", "{}")
    _write(build_path / "assets" / "api" / "nested" / "deep.json", "{}")
    _write(build_path / "assets" / "api" / "a.json", "{}")

    info = resolved_asset_info(build_path, "api", "api-doc")

    assert info.path == "assets/api"
    assert info.asset == "api-doc"
    assert info.files == ["a.json", "b.json", "nested/deep.json"]


def test_resolved_asset_info_matches_what_sync_asset_materialised(tmp_path: Path):
    root = _glob_tree(tmp_path)
    build_path = tmp_path / "build"
    sync_asset(root, build_path, _asset("assets/openapi"), properties={}, remotes={})

    assert resolved_asset_info(build_path, "openapi", "openapi").files == ["api.json", "api.xml", "nested/deep.json"]


def test_resolved_asset_info_missing_directory_points_at_build_run(tmp_path: Path):
    with pytest.raises(SourceSyncError, match="Run `strata build run` first"):
        resolved_asset_info(tmp_path / "build", "api", "api-doc")


def test_asset_lands_under_its_slot_not_its_document_name(tmp_path: Path):
    root = _glob_tree(tmp_path)
    build_path = tmp_path / "build"

    destination = sync_asset(root, build_path, _asset("assets/openapi"), properties={}, remotes={}, slot="api")

    assert destination == build_path / "assets" / "api"
    assert (destination / "api.json").exists()
    assert not (build_path / "assets" / "openapi").exists()


def test_asset_slot_directory_is_cleared_of_a_previous_builds_files(tmp_path: Path):
    """Rebinding a slot into a reused build path must not leave the old document's files behind."""
    root = _glob_tree(tmp_path)
    build_path = tmp_path / "build"
    _write(build_path / "assets" / "api" / "stale.json", "{}")
    _write(build_path / "assets" / "api" / "old" / "deep.json", "{}")

    destination = sync_asset(root, build_path, _asset("assets/openapi/*.json"), properties={}, remotes={}, slot="api")

    assert sorted(p.name for p in destination.rglob("*") if p.is_file()) == ["api.json"]


def test_asset_sync_clears_only_its_own_slot_directory(tmp_path: Path):
    root = _glob_tree(tmp_path)
    build_path = tmp_path / "build"
    _write(build_path / "assets" / "other" / "keep.txt", "mine")
    _write(build_path / "assets" / "loose.txt", "mine")

    sync_asset(root, build_path, _asset("assets/openapi"), properties={}, remotes={}, slot="api")

    assert (build_path / "assets" / "other" / "keep.txt").read_text() == "mine"
    assert (build_path / "assets" / "loose.txt").read_text() == "mine"


def test_asset_sync_failure_leaves_the_previous_slot_directory_intact(tmp_path: Path):
    """The wipe happens only after the new source was found, so a typo does not destroy a good build."""
    root = _glob_tree(tmp_path)
    build_path = tmp_path / "build"
    _write(build_path / "assets" / "api" / "old.json", "{}")

    with pytest.raises(SourceSyncError, match="does not exist"):
        sync_asset(root, build_path, _asset("assets/missing"), properties={}, remotes={}, slot="api")

    assert (build_path / "assets" / "api" / "old.json").exists()


# ---------------------------------------------------------------------------
# sync_asset() from the asset's own `location` (docs/design/asset-management.md)
# ---------------------------------------------------------------------------

_NEEDS_GIT = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _git_repo(path: Path, versions: dict[str, dict[str, str]]) -> str:
    """A local git repository with one commit and tag per entry of `versions` (tag -> files); returns its URL."""
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    for tag, files in versions.items():
        for existing in path.iterdir():
            if existing.name != ".git":
                shutil.rmtree(existing) if existing.is_dir() else existing.unlink()
        for relative, content in files.items():
            _write(path / relative, content)
        _git(path, "add", "-A")
        _git(path, "commit", "-q", "-m", tag)
        _git(path, "tag", tag)
    return str(path)


def _located_asset(
    name: str, url: str, reference: str, *, fetch: str = "strata", source_path: str = "api"
) -> AssetModel:
    return AssetModel.model_validate(
        {
            "meta": {"name": name},
            "spec": {
                "source_path": source_path,
                "location": {"type": "git", "url": url, "reference": reference, "fetch": fetch},
            },
        }
    )


@_NEEDS_GIT
def test_asset_location_is_cloned_at_its_reference_under_the_asset_checkout_root(tmp_path: Path):
    url = _git_repo(tmp_path / "customer", {"v1": {"api/a.json": "one"}, "v2": {"api/b.json": "two"}})
    root = tmp_path / "sln"
    root.mkdir()

    destination = sync_asset(root, tmp_path / "build", _located_asset("c0062-api", url, "v1"), {}, {}, slot="api")

    assert sorted(p.name for p in destination.iterdir()) == ["a.json"]
    assert layout.asset_checkout_path(root, "c0062-api", "v1").is_dir()
    assert not layout.remotes_dir(root).exists()


@_NEEDS_GIT
def test_two_assets_with_their_own_repositories_get_their_own_files(tmp_path: Path):
    first = _git_repo(tmp_path / "c1", {"v1": {"api/one.json": "1"}})
    second = _git_repo(tmp_path / "c2", {"v1": {"api/two.json": "2"}})
    root = tmp_path / "sln"
    root.mkdir()

    one = sync_asset(root, tmp_path / "b1", _located_asset("c1-api", first, "v1"), {}, {}, slot="api")
    two = sync_asset(root, tmp_path / "b2", _located_asset("c2-api", second, "v1"), {}, {}, slot="api")

    assert [p.name for p in one.iterdir()] == ["one.json"]
    assert [p.name for p in two.iterdir()] == ["two.json"]


@_NEEDS_GIT
def test_two_slots_bound_to_one_location_asset_share_one_checkout(tmp_path: Path):
    url = _git_repo(tmp_path / "customer", {"v1": {"api/a.json": "one"}})
    root = tmp_path / "sln"
    root.mkdir()
    asset = _located_asset("c0062-api", url, "v1")

    first = sync_asset(root, tmp_path / "build", asset, {}, {}, slot="api")
    second = sync_asset(root, tmp_path / "build", asset, {}, {}, slot="api-copy")

    assert [p.name for p in first.iterdir()] == [p.name for p in second.iterdir()] == ["a.json"]
    assert [p.name for p in (layout.strata_dir(root) / "assets" / "c0062-api").iterdir()] == ["v1"]


def test_asset_location_fetch_external_uses_the_checkout_placed_by_ci(tmp_path: Path):
    root = tmp_path / "sln"
    placed = layout.asset_checkout_path(root, "c0062-api", "v1")
    _write(placed / "api" / "a.json", "from CI")
    asset = _located_asset("c0062-api", "https://example.invalid/never-cloned.git", "v1", fetch="external")

    destination = sync_asset(root, tmp_path / "build", asset, {}, {})

    assert (destination / "a.json").read_text() == "from CI"


def test_asset_location_fetch_external_without_a_checkout_names_the_expected_path(tmp_path: Path):
    root = tmp_path / "sln"
    root.mkdir()
    asset = _located_asset("c0062-api", "https://example.invalid/x.git", "v1", fetch="external")

    with pytest.raises(RemoteResolutionError) as raised:
        sync_asset(root, tmp_path / "build", asset, {}, {})

    assert str(layout.asset_checkout_path(root, "c0062-api", "v1")) in str(raised.value)


def test_module_source_missing_path_raises_clear_error(tmp_path: Path):
    root = tmp_path / "solution"
    root.mkdir()
    source = SourceModel(source_path="does/not/exist")
    with pytest.raises(SourceSyncError, match="does not exist"):
        sync_module_source(root, tmp_path / "build" / "apps" / "authentik", source, remotes={})
