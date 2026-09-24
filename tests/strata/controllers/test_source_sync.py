#!/usr/bin/env python3
"""Tests for `source_sync.sync_source` (ADR-0022 D3)."""

from pathlib import Path

import pytest

from strata.controllers.source_sync import SourceSyncError, sync_source
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
