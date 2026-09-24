#!/usr/bin/env python3
"""Materialising a `SourceModel` into a build directory (ADR-0022 D3).

**Destination mirrors the source's own internal repo-relative path, not the
provisioner/step name.** This is the one real gap ADR-0022 D3 found while
checking cross-repo evidence: a real workspace
(`cfg-int-deployment`'s `stacks/spoke/workspace.yaml`) declares a
"copy-only" provisioner (`core_modules`) that stages a shared Terraform
module library so the real provisioner's `.tf` code can compose it via a
relative path (`source = "../../core/terraform/components/aks"`). That only
resolves correctly if `core_modules`'s materialised location and the main
provisioner's materialised location preserve the *same relative offset*
they have inside the source repository — placing each provisioner under
`build_path/<its own step name>/` (the naive scheme) would break this,
since step names have no relationship to the repo's real directory layout.
Placing each one under `build_path/<source_path>/` instead (mirroring the
repo layout directly) makes sibling composition resolve correctly with zero
cross-provisioner awareness needed in this module — each call is
independent, exactly the "no new design" framing ADR-0022 D3 itself uses.

`target_path` (when a document sets it) overrides this default — the
escape hatch for a source that wants a different on-disk name than its
own `source_path`.

Chart-based sources (`SourceModel.chart_name` set) are explicitly out of
scope here — a chart pull (Helm's own registry mechanism) is not a file
copy, and no real provisioner example uses chart-based sourcing.
"""

import shutil
from pathlib import Path

from strata.controllers.remote_resolution import resolve_remote
from strata.models.common_models import SourceModel
from strata.models.solution_model import SolutionRemoteModel
from strata.utils.errors import SystemError


class SourceSyncError(SystemError):
    """A `SourceModel` could not be materialised into the build directory."""


def sync_source(
    root: Path,
    build_path: Path,
    source: SourceModel,
    remotes: dict[str, SolutionRemoteModel],
) -> Path:
    """Copy `source`'s files into `build_path`, resolving its remote first.

    Args:
        root: The solution root (where `strata.yaml` lives).
        build_path: The build output root for this `build run` invocation.
        source: The `SourceModel` to materialise (a provisioner's `.source`,
            or `ModuleFileModel.source`).
        remotes: Every declared remote, keyed by name — built once per
            `build run` invocation and reused across every `sync_source()`
            call, the same way `ResolvedWorkspaceGraph` is assembled once
            and passed down (ADR-0022 D1a).

    Returns:
        The directory `source`'s files were copied into.

    Raises:
        SourceSyncError: `source` is chart-based (out of scope here), its
            declared `remote` name is not in `remotes`, or the copy itself
            failed (missing `source_path` inside the resolved repo, an I/O
            error).
    """
    if source.chart_name is not None:
        raise SourceSyncError(
            "sync_source() only materialises git/local sources — chart-based sources "
            "(chart_name set) are pulled by the deployer itself (e.g. `helm pull`), not copied."
        )

    remote = None
    if source.remote is not None:
        remote = remotes.get(source.remote)
        if remote is None:
            raise SourceSyncError(f"Source names remote '{source.remote}', which is not declared in this solution.")

    repo_root = resolve_remote(root, remote)
    assert source.source_path is not None  # guaranteed by SourceModel.validate_source_mode for git-based sources
    origin = repo_root / source.source_path
    destination = build_path / (source.target_path or source.source_path)

    if not origin.exists():
        raise SourceSyncError(f"Source path '{source.source_path}' does not exist under '{repo_root}'.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        if origin.is_dir():
            shutil.copytree(origin, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(origin, destination)
    except OSError as exc:
        raise SourceSyncError(f"Failed to copy '{origin}' to '{destination}': {exc}") from exc

    return destination
