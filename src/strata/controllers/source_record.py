#!/usr/bin/env python3
"""What a build took from each git remote, for `resolved.yaml`'s `solution` and `remotes` keys.

docs/design/remote-commit-provenance.md, "Collecting what the build took". One `SourceRecord` per `build run`, passed as
`record=` beside `credentials=` wherever a source is resolved:

- `resolved()` registers a `type: git` remote's checkout when `source_sync._resolve_repo()` locates it;
- `took()` adds a path copied from a checkout, and ignores a checkout nothing registered (the solution root and
  `type: local` remotes, which the `solution` record covers);
- `provenance()` runs `checkout_state()` once per remote at the end of the build and warns per R3's build column.

The read side is here too: `read_build_record()` (the audit manifest's copy, Phase 3) and `check_build_record()`
(`spec.audit.require_remote_commits` at deploy, Phase 4, D14).
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from strata.controllers.checkout_state import CheckoutState, checkout_state
from strata.models.audit_manifest_model import ManifestRepositoryModel
from strata.models.solution_model import RemoteType, SolutionRemoteModel
from strata.utils import layout
from strata.utils.diagnostics import Diagnostics

#: The key prefix for an asset's own `spec.location`; `:` cannot occur in a remote name.
ASSET_KEY_PREFIX = "asset:"

_COMMIT_UNAVAILABLE = "audit_remote_commit_unavailable"
_DIRTY = "remote_checkout_dirty"
_MISSING = "build_remote_provenance_missing"
_SOLUTION_LABEL = "The solution repository"


@dataclass
class _Remote:
    url: str
    ref: str | None
    checkout: Path
    paths: list[str] = field(default_factory=list)


class SourceRecord:
    """The git remotes one build took files from, and which paths."""

    def __init__(self) -> None:
        self._remotes: dict[str, _Remote] = {}

    def resolved(self, key: str, remote: SolutionRemoteModel, checkout: Path) -> None:
        """Register `remote`'s `checkout` under `key` (the remote's name, or `asset:<name>`). Only `type: git`."""
        if remote.type != RemoteType.GIT or key in self._remotes:
            return
        # Path mode: the pipeline owns the ref, so none is written (docs/design/remotes.md).
        ref = None if remote.path is not None else remote.reference
        self._remotes[key] = _Remote(remote.url, ref, checkout.resolve())

    def took(self, checkout: Path, path: str, *, glob: bool = False) -> None:
        """Add `path`, relative to `checkout`, to every remote registered at `checkout`.

        `glob=True` for a pattern: `:(glob)` matches as `pathlib.glob()` copied it. Otherwise `:(literal)`.
        """
        pathspec = f":({'glob' if glob else 'literal'}){path}"
        located = checkout.resolve()
        for remote in self._remotes.values():
            if remote.checkout == located and pathspec not in remote.paths:
                remote.paths.append(pathspec)

    def provenance(self, root: Path, build_path: Path, commit: str | None, diagnostics: Diagnostics) -> dict[str, Any]:
        """`{"solution": {commit, dirty}, "remotes": {key: {url, ref, commit, dirty}}}` for `resolved.yaml`.

        Args:
            root: The solution root.
            build_path: This build's output, left out of the solution's dirty check when it is under `root`.
            commit: The solution's commit (`built_commit_sha()`), which the SBOM records too.
            diagnostics: Receives `audit_remote_commit_unavailable` and `remote_checkout_dirty` warnings.

        `remotes` is `{}` rather than absent when nothing was taken, so a reader can tell this build from one made
        before the record existed.
        """
        remotes: dict[str, dict[str, Any]] = {}
        for key, remote in sorted(self._remotes.items()):
            if not remote.paths:
                continue
            state = checkout_state(remote.checkout, remote.paths)
            remotes[key] = {"url": remote.url, "ref": remote.ref, "commit": state.commit, "dirty": state.dirty}
            _warn(_label(key), remote.checkout, state, diagnostics)

        exclude = [layout.STRATA_DIR]
        for path in (build_path, *(remote.checkout for remote in self._remotes.values())):
            relative = _relative(path, root)
            if relative is not None:
                exclude.append(relative)
        state = checkout_state(root, ["."], exclude=exclude, enclosing=True)
        # The commit is the SBOM's (a CI variable first); `git` only answers `dirty`. A solution outside git gets
        # nulls but no warning, as the SBOM does: it is the default case for an example or a test, not a gap in
        # one record (require_remote_commits, Phase 4, makes it an error).
        state = CheckoutState(commit, state.dirty if state.commit else None, state.problem)
        if commit is not None:
            _warn(_SOLUTION_LABEL, root, state, diagnostics)
        return {"solution": {"commit": commit, "dirty": state.dirty}, "remotes": remotes}


@dataclass(frozen=True)
class BuildRecord:
    """`resolved.yaml`'s `solution` and `remotes` keys, as `SourceRecord.provenance()` wrote them."""

    solution_commit: str | None
    solution_dirty: bool | None
    remotes: dict[str, ManifestRepositoryModel]


def read_build_record(build_path: Path) -> BuildRecord | None:
    """The build's record of what it took, or `None` for a build made before it existed.

    `build run` writes `solution` and `remotes` together (`remotes` as `{}` when nothing came from a remote). So a
    `resolved.yaml` that lacks either, is missing, or does not read as the record is treated as an older build.
    Never raises.
    """
    try:
        resolved = yaml.safe_load((build_path / "resolved.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(resolved, dict):
        return None
    solution, remotes = resolved.get("solution"), resolved.get("remotes")
    if not isinstance(solution, dict) or not isinstance(remotes, dict):
        return None
    commit, dirty = solution.get("commit"), solution.get("dirty")
    if not isinstance(commit, str | None) or not isinstance(dirty, bool | None):
        return None
    try:
        return BuildRecord(
            solution_commit=commit,
            solution_dirty=dirty,
            remotes={str(key): ManifestRepositoryModel.model_validate(entry) for key, entry in remotes.items()},
        )
    except ValidationError:
        return None


def check_build_record(build_path: Path) -> Diagnostics:
    """`spec.audit.require_remote_commits` at deploy (D14): an error for each gap in the build's record.

    Run before the deploy changes anything. Reads only `resolved.yaml`, never a checkout: the deploy runs the build
    output, so the record is the evidence. A `dirty` the build could not read counts as dirty.
    """
    diagnostics = Diagnostics()
    record = read_build_record(build_path)
    if record is None:
        diagnostics.error(
            f"'{build_path / 'resolved.yaml'}' does not record which commits the build took its files from, and "
            "spec.audit.require_remote_commits is on.",
            code=_MISSING,
            hint="Build again with this version of strata ('strata build run'), then deploy that build.",
        )
        return diagnostics
    _require(_SOLUTION_LABEL, record.solution_commit, record.solution_dirty, diagnostics)
    for key, repository in sorted(record.remotes.items()):
        _require(_label(key), repository.commit, repository.dirty, diagnostics)
    return diagnostics


def _require(label: str, commit: str | None, dirty: bool | None, diagnostics: Diagnostics) -> None:
    if commit is None:
        diagnostics.error(
            f"{label}: the build recorded no commit, and spec.audit.require_remote_commits is on.",
            code=_COMMIT_UNAVAILABLE,
            hint="Build from a git checkout, not a copy or an archive, then deploy that build. The build's warnings "
            "say which folder.",
        )
    elif dirty is not False:
        state = "had uncommitted changes" if dirty else "could not be checked for uncommitted changes"
        diagnostics.error(
            f"{label}: the files the build took {state} at commit {commit[:12]}, and "
            "spec.audit.require_remote_commits is on.",
            code=_DIRTY,
            hint="Commit the change, build again from a clean checkout, then deploy that build.",
        )


def _label(key: str) -> str:
    if key.startswith(ASSET_KEY_PREFIX):
        return f"Asset '{key.removeprefix(ASSET_KEY_PREFIX)}' (spec.location)"
    return f"Remote '{key}'"


def _relative(path: Path, root: Path) -> str | None:
    """`path` relative to `root` as a pathspec, or `None` when it is not strictly under `root`."""
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None
    return None if relative == "." else relative


def _warn(label: str, checkout: Path, state: CheckoutState, diagnostics: Diagnostics) -> None:
    if state.commit is None:
        diagnostics.warning(
            f"{label}: {state.problem}, so resolved.yaml records no commit for it.",
            code=_COMMIT_UNAVAILABLE,
            hint=f"Build from a git checkout of '{checkout}', not a copy or an archive, so its commit is recorded.",
        )
    elif state.dirty is None:
        diagnostics.warning(
            f"{label}: {state.problem}, so resolved.yaml does not say whether the files taken had changes.",
            code=_DIRTY,
        )
    elif state.dirty:
        diagnostics.warning(
            f"{label}: files the build took from '{checkout}' have uncommitted changes, so commit "
            f"{state.commit[:12]} does not match what was built.",
            code=_DIRTY,
            hint=f"Commit the change in '{checkout}', or build from a clean checkout.",
        )
