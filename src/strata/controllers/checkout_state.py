#!/usr/bin/env python3
"""What a git checkout holds: the commit it is at, the tag or branch it is on, and whether the files a build takes
from it have changes.

docs/design/remote-commit-provenance.md, "Reading a checkout". The record answers "which tree of this
repository did the deployment use?" for an audit. A commit alone does not answer it when the files on disk differ
from the commit, hence `dirty`.

Read only: never raises, never writes (`--no-optional-locks` keeps `git status` from refreshing the index of a
checkout someone else owns). What to do with an unreadable checkout, and how to word it, is the caller's: only the
caller knows the remote's name and what the record is for.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from packaging.version import Version

from strata.utils.transport import run_command
from strata.utils.version_constraints import is_version

_GIT_TIMEOUT = 30
#: A release tag starts with `major.minor`, optionally after a `v`.
_RELEASE_TAG = re.compile(r"^[vV]?\d+\.\d+")


@dataclass(frozen=True)
class CheckoutState:
    """A checkout's commit and whether the checked paths have changes.

    `dirty` is `None` when it was not asked for (`paths=None`) or could not be read; `problem` says why `commit` or
    a requested `dirty` is missing.
    """

    commit: str | None
    dirty: bool | None
    problem: str | None = None


def checkout_state(
    checkout: Path,
    paths: Sequence[str] | None = None,
    *,
    exclude: Sequence[str] = (),
    enclosing: bool = False,
) -> CheckoutState:
    """The commit `checkout` is at and, when `paths` is given, whether those paths have uncommitted changes.

    Args:
        checkout: The folder to read. It must hold `.git` itself (a folder, or a file for a worktree or
            submodule); otherwise `git` would walk up and report the enclosing repository's commit.
        paths: Git pathspecs, relative to `checkout`, to check for changes: the `source_path`s a build took from it.
            Empty means the whole checkout. `None` skips the check. Pathspec magic such as `:(literal)` or
            `:(glob)` is passed through.
        exclude: Pathspecs left out of the check, such as the solution's own build output.
        enclosing: Read the repository `checkout` is inside, which is what the solution root wants: `strata.yaml`
            may sit in a folder of its repository. Paths stay relative to `checkout`.

    Untracked files count as changes, because a build copies them; ignored files do not (D11).
    """
    if not enclosing and not (checkout / ".git").exists():
        return CheckoutState(None, None, f"'{checkout}' is not a git checkout")

    head = run_command(["git", "rev-parse", "HEAD"], cwd=checkout, timeout=_GIT_TIMEOUT)
    commit = head.stdout.strip() if head.is_successful else ""
    if not commit:
        return CheckoutState(None, None, f"'git rev-parse HEAD' failed in '{checkout}': {head.stderr.strip()}")
    if paths is None:
        return CheckoutState(commit, None)

    pathspecs = [*(paths or ["."]), *(f":(exclude){path}" for path in exclude)]
    status = run_command(
        ["git", "--no-optional-locks", "status", "--porcelain", "--", *pathspecs], cwd=checkout, timeout=_GIT_TIMEOUT
    )
    if not status.is_successful:
        return CheckoutState(commit, None, f"'git status' failed in '{checkout}': {status.stderr.strip()}")
    return CheckoutState(commit, bool(status.stdout.strip()))


def checkout_ref(checkout: Path, *, enclosing: bool = False) -> str | None:
    """The tag `checkout`'s HEAD is exactly on, else the branch it has checked out, else `None`.

    docs/design/remote-commit-provenance.md, "The record in `resolved.yaml`": a pipeline that checks a repository out
    at a release tag can then answer "which version ran" from the record. With several tags on HEAD, the highest
    version wins (`checkout_tags()`), so `v1.4.0` beats `v1.4.0-rc1` and a moving `latest`. A tag the clone
    did not fetch is not seen (an Azure DevOps checkout fetches tags only with `fetchTags: true`). A detached HEAD
    on no tag, as a pipeline checkout at a commit is, gives `None`: the commit already says which tree it was.

    Args:
        checkout: The folder to read, holding `.git` itself unless `enclosing`.
        enclosing: Read the repository `checkout` is inside, as `checkout_state()` does for the solution root.
    """
    if not enclosing and not (checkout / ".git").exists():
        return None
    tags = checkout_tags(checkout, enclosing=enclosing).tags
    return tags[0] if tags else checkout_branch(checkout)


@dataclass(frozen=True)
class CheckoutTags:
    """The tags a checkout's HEAD is on, or why they could not be read (`problem`, with `tags` empty)."""

    tags: list[str]
    problem: str | None = None


def is_release_tag(tag: str) -> bool:
    """Whether `tag` names a release version: `v1.4.0`, `1.4`, `v2.0.0-rc1`, not a build number such as `20241001`.

    A bare number is a PEP 440 version too, so without the `major.minor` rule a CI build tag would outrank the
    release tag on the same commit.
    """
    return _RELEASE_TAG.match(tag) is not None and is_version(tag)


def checkout_tags(checkout: Path, *, enclosing: bool = False) -> CheckoutTags:
    """Every tag `checkout`'s HEAD is exactly on: release versions first, highest first, then the others in git's
    order.

    Versions are compared as PEP 440, not by git's `version:refname`, which puts `v1.4.0-rc1` above `v1.4.0`. A
    failing `git` (not on PATH, a timeout, "dubious ownership" of another user's checkout) is a `problem`, not "no
    tags": a caller that guards on the tag must not read the failure as a branch.
    """
    if not enclosing and not (checkout / ".git").exists():
        return CheckoutTags([], f"'{checkout}' is not a git checkout")
    listed = run_command(
        ["git", "tag", "--points-at", "HEAD", "--sort=-version:refname"], cwd=checkout, timeout=_GIT_TIMEOUT
    )
    if not listed.is_successful:
        return CheckoutTags([], f"'git tag --points-at HEAD' failed in '{checkout}': {listed.stderr.strip()}")
    tags = listed.stdout.split()
    versions = sorted((tag for tag in tags if is_release_tag(tag)), key=Version, reverse=True)
    return CheckoutTags([*versions, *(tag for tag in tags if not is_release_tag(tag))])


def checkout_branch(checkout: Path) -> str | None:
    """The branch `checkout` has checked out, or `None` for a detached HEAD or a failing `git`."""
    branch = run_command(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], cwd=checkout, timeout=_GIT_TIMEOUT)
    return (branch.stdout.strip() or None) if branch.is_successful else None
