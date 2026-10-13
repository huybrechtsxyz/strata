#!/usr/bin/env python3
"""Tests for `source_sync.sync_source` (ADR-0022 D3)."""

from pathlib import Path

import pytest

from strata.controllers.source_sync import SourceSyncError, sync_asset, sync_module_source, sync_source
from strata.models.asset_model import AssetModel
from strata.models.common_models import SourceModel
from strata.models.solution_model import RemoteType, SolutionRemoteModel


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


def test_module_source_missing_path_raises_clear_error(tmp_path: Path):
    root = tmp_path / "solution"
    root.mkdir()
    source = SourceModel(source_path="does/not/exist")
    with pytest.raises(SourceSyncError, match="does not exist"):
        sync_module_source(root, tmp_path / "build" / "apps" / "authentik", source, remotes={})
