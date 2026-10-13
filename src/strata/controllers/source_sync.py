#!/usr/bin/env python3
"""Materialising a `SourceModel` into a build directory (ADR-0022 D3).

**Destination mirrors the source's own internal repo-relative path, not the
provisioner/step name.** This is the one real gap ADR-0022 D3 found while
checking cross-repo evidence: a real workspace
(`config-deploy`'s `stacks/spoke/workspace.yaml`) declares a
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

**A copy here is byte-for-byte, always — never a template render
(ADR-0025).** v1 Jinja2-rendered every copied file in place with a
`STRATA_*`/`variables`/`features` context
(`base_builder._apply_templates_to_dir()`); v2 deliberately does not.
Strata supplies *input to* IaC (`.auto.tfvars.json`, `values.yaml`,
`STRATA_*` env vars — written alongside the source) and never rewrites the
source itself, which is frequently vendored third-party content the
deployment does not own. Do not add a substitution pass to this module;
ADR-0023's `output.template` (generate a *new* file) is the intended
escape hatch if a real need for rendering appears.
"""

import shutil
from pathlib import Path
from typing import Any

from strata.controllers.remote_resolution import resolve_remote
from strata.models.asset_model import AssetModel
from strata.models.common_models import SourceModel
from strata.models.solution_model import SolutionRemoteModel
from strata.utils.errors import SystemError
from strata.utils.path_conventions import fill_pattern, segment_tokens
from strata.utils.path_safety import resolve_within
from strata.utils.value_tokens import has_value_tokens

#: Characters that make an asset's `source_path` a glob pattern rather than a
#: literal path. Deliberately excludes '{'/'}': those are strata's own
#: `{name}` segment tokens (already filled before any of this runs), not shell
#: brace expansion, which `pathlib.glob()` does not support either way.
_GLOB_MAGIC = "*?["


def _has_glob_magic(pattern: str) -> bool:
    """True when `pattern` is a glob rather than a literal path."""
    return any(char in pattern for char in _GLOB_MAGIC)


def _glob_base(pattern: str) -> str:
    """The leading run of `pattern`'s segments that contain no wildcard — the
    directory matched paths are made relative to when copying.

    `assets/openapi/**/*.json` -> `assets/openapi`, so a match at
    `assets/openapi/nested/deep.json` keeps its `nested/deep.json` shape in the
    build output instead of being flattened onto a same-named sibling.
    Returns `""` for a pattern that starts with a wildcard, meaning the repo
    root itself.
    """
    segments: list[str] = []
    for segment in pattern.split("/"):
        if _has_glob_magic(segment):
            break
        segments.append(segment)
    return "/".join(segments)


class SourceSyncError(SystemError):
    """A `SourceModel` could not be materialised into the build directory."""


def describe_source(source: SourceModel) -> str:
    """One-line human-readable summary of `source`, for `--dry-run` reporting.

    Never touches disk or a remote — string formatting only, so it's safe to
    call in place of the real `sync_source()`/`sync_module_source()` when a
    caller wants to report what *would* be materialised without doing it.
    """
    if source.chart_name is not None:
        version = f" {source.chart_version}" if source.chart_version else ""
        return f"chart '{source.chart_name}'{version} from remote '{source.remote}'"
    remote = f"remote '{source.remote}', " if source.remote else ""
    return f"{remote}source_path '{source.source_path}'"


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

    # `source_path`/`target_path` are already validated as relative at the model layer, so
    # this only catches what a string check cannot: a symlink inside the repo pointing out
    # of it (`copytree` follows symlinks by default).
    try:
        resolve_within(repo_root, origin)
    except ValueError as exc:
        raise SourceSyncError(f"Source path '{source.source_path}': {exc}") from exc

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


def sync_asset(
    root: Path,
    build_path: Path,
    asset: AssetModel,
    properties: dict[str, Any],
    remotes: dict[str, SolutionRemoteModel],
) -> Path:
    """Copy `asset`'s files into `build_path/assets/<name>/`, filling its
    `source_path` template against `properties` first (docs/design/
    asset-management.md Phase 3).

    Structurally parallel to `sync_source()` above — same remote
    resolution, same byte-for-byte copy, same error class — with three
    differences: `source_path` is templated (`fill_pattern()`) rather than
    used literally, it may be a glob rather than one path, and the
    destination is always a directory keyed by the asset's own name, never
    path-mirrored the way a provisioner's source is (an asset has no
    sibling-composition need to preserve — see this module's own
    docstring's "Destination mirrors..." paragraph, which does not apply
    here).

    A `source_path` with no wildcard names one file or directory and
    behaves exactly as it always has. One containing `*`/`**`/`?`/`[abc]`
    selects a subset instead, and **every match keeps its path relative to
    the pattern's last wildcard-free directory** — `openapi/**/*.json`
    lands `openapi/nested/deep.json` at `assets/<name>/nested/deep.json`,
    so two same-named files in different directories cannot silently
    overwrite each other.

    Every resolved match is checked back against the resolved remote root
    (`resolve_within()`): a filled `{name}` token or a `..` inside a glob
    can both produce a path outside it, which no validation of the
    *declared* string could have caught.

    Args:
        root: The solution root (where `strata.yaml` lives).
        build_path: The build output root for this `build run` invocation.
        asset: The `AssetModel` to materialise.
        properties: The deployment's already build-time-resolved
            `spec.properties` (`ResolvedWorkspaceGraph.properties`) —
            fills `asset.spec.source_path`'s `{name}` segment tokens.
        remotes: Every declared remote, keyed by name.

    Returns:
        The directory `asset`'s files were copied into
        (`build_path/assets/<asset.meta.name>/`).

    Raises:
        SourceSyncError: a `{name}` token has no matching `properties` key,
            a property used to fill one is not a `str`/`int`/`float` (a
            `dict`/`list`/`bool` would stringify into a nonsense path
            segment), a property used to fill one still contains an
            unresolved `${...}` Value token (resolves only at deploy time
            — see docs/design/value-token-resolution.md — so
            `fill_pattern()` can never substitute it), the declared
            `remote` is not declared in this solution, the filled path
            does not exist (or a glob matched nothing) under the resolved
            remote, a match resolves outside the remote root, or the copy
            itself failed.
    """
    for token in segment_tokens(asset.spec.source_path):
        value = properties.get(token)
        if value is None:
            continue  # `fill_pattern()` below raises the clearer "Unknown token" error for this
        # `bool` is checked first and rejected on purpose: `isinstance(True, int)` is True in
        # Python, so it would otherwise pass the scalar check and fill a literal 'True' segment.
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise SourceSyncError(
                f"Asset '{asset.meta.name}': property '{token}' used to fill source_path is a "
                f"{type(value).__name__} ({value!r}), which cannot be a path segment — only a "
                "string or a number can fill a '{name}' token."
            )
        if isinstance(value, str) and has_value_tokens(value):
            raise SourceSyncError(
                f"Asset '{asset.meta.name}': property '{token}' used to fill source_path still contains "
                f"an unresolved '${{...}}' token ({value!r}) — Value tokens resolve at deploy time, not "
                "build time (docs/design/value-token-resolution.md), so this cannot be filled now."
            )

    try:
        filled_path = fill_pattern(asset.spec.source_path, {k: str(v) for k, v in properties.items() if v is not None})
    except ValueError as exc:
        raise SourceSyncError(f"Asset '{asset.meta.name}': {exc}") from exc

    remote = None
    if asset.spec.remote is not None:
        remote = remotes.get(asset.spec.remote)
        if remote is None:
            raise SourceSyncError(
                f"Asset '{asset.meta.name}' names remote '{asset.spec.remote}', which is not declared in this solution."
            )

    repo_root = resolve_remote(root, remote)
    destination = build_path / "assets" / asset.meta.name

    if not _has_glob_magic(filled_path):
        origin = repo_root / filled_path
        try:
            resolve_within(repo_root, origin)
        except ValueError as exc:
            raise SourceSyncError(f"Asset '{asset.meta.name}': {exc}") from exc
        if not origin.exists():
            raise SourceSyncError(
                f"Asset '{asset.meta.name}': path '{filled_path}' does not exist under '{repo_root}'."
            )
        destination.mkdir(parents=True, exist_ok=True)
        try:
            if origin.is_dir():
                shutil.copytree(origin, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(origin, destination / origin.name)
        except OSError as exc:
            raise SourceSyncError(f"Failed to copy '{origin}' to '{destination}': {exc}") from exc
        return destination

    try:
        matches = sorted(repo_root.glob(filled_path))
    except NotImplementedError as exc:
        # pathlib refuses an absolute pattern outright; findings 1/2's checks normally
        # reject one first, so this only guards a caller that bypassed the model.
        raise SourceSyncError(f"Asset '{asset.meta.name}': pattern '{filled_path}' is not relative: {exc}") from exc
    if not matches:
        raise SourceSyncError(
            f"Asset '{asset.meta.name}': pattern '{filled_path}' matched no files under '{repo_root}'."
        )

    base_dir = repo_root / _glob_base(filled_path)
    destination.mkdir(parents=True, exist_ok=True)
    for match in matches:
        try:
            resolve_within(repo_root, match)
        except ValueError as exc:
            raise SourceSyncError(f"Asset '{asset.meta.name}': {exc}") from exc
        target = destination / match.relative_to(base_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if match.is_dir():
                shutil.copytree(match, target, dirs_exist_ok=True)
            else:
                shutil.copy2(match, target)
        except OSError as exc:
            raise SourceSyncError(f"Failed to copy '{match}' to '{target}': {exc}") from exc

    return destination


def sync_module_source(
    root: Path,
    module_dir: Path,
    source: SourceModel,
    remotes: dict[str, SolutionRemoteModel],
) -> None:
    """Materialise a workload module's chart source into `module_dir`
    (ADR-0022 D6/D7), if it has one to copy.

    Unlike `sync_source()` (provisioner sources, ADR-0022 D3), a
    chart-based `source` here is a normal, expected mode — a registry chart
    pull (e.g. `authentik` from a Helm repo) is Helm's real primary use
    case, per `SourceModel`'s own docstring example — so it is not an
    error: `HelmIntegration._render_meta()` writes the chart coordinates
    into `meta.yaml` instead, for the deployer to pull directly. `module_dir`
    is still created in this case (nothing else does, and `values.yaml`/
    `meta.yaml` must land somewhere) — only the copy itself is skipped.

    Destination is always `module_dir` itself — never derived from
    `source.source_path`/`.target_path` the way `sync_source()` computes a
    provisioner's destination. That convention exists to preserve sibling
    provisioners' relative layout (D3); a Helm chart has no such
    cross-module relative composition to preserve, and `module_dir` must
    stay collision-free even when the *same* Module document is attached to
    a namespace twice under different reference names
    (`ModuleReferenceModel`'s own docstring) — `source.source_path` would
    be identical for both attachments, `module_dir` (keyed by the unique
    reference name) is not.

    Args:
        root: The solution root (where `strata.yaml` lives).
        module_dir: Where this module's files should land — already keyed
            by its unique reference name, computed by the caller
            (`workload_controller.build_workload_modules()`). Created here
            unconditionally, even for a chart-based `source` with nothing
            to copy.
        source: The module's `spec.source`.
        remotes: Every declared remote, keyed by name.

    Raises:
        SourceSyncError: `source` names a remote that is not declared, its
            `source_path` does not exist under the resolved remote, or the
            copy itself failed.
    """
    if source.chart_name is not None:
        module_dir.mkdir(parents=True, exist_ok=True)
        return

    remote = None
    if source.remote is not None:
        remote = remotes.get(source.remote)
        if remote is None:
            raise SourceSyncError(f"Source names remote '{source.remote}', which is not declared in this solution.")

    repo_root = resolve_remote(root, remote)
    assert source.source_path is not None  # guaranteed by SourceModel.validate_source_mode for git-based sources
    origin = repo_root / source.source_path

    try:
        resolve_within(repo_root, origin)
    except ValueError as exc:
        raise SourceSyncError(f"Source path '{source.source_path}': {exc}") from exc

    if not origin.exists():
        raise SourceSyncError(f"Source path '{source.source_path}' does not exist under '{repo_root}'.")

    module_dir.mkdir(parents=True, exist_ok=True)
    try:
        if origin.is_dir():
            shutil.copytree(origin, module_dir, dirs_exist_ok=True)
        else:
            shutil.copy2(origin, module_dir / origin.name)
    except OSError as exc:
        raise SourceSyncError(f"Failed to copy '{origin}' to '{module_dir}': {exc}") from exc
