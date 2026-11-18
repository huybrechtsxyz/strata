#!/usr/bin/env python3
"""What a git checkout holds: the commit it is at, and whether the files a build takes from it have changes.

docs/work/remote-commit-provenance.md, "`checkout_state()` (Phase 1)". The record answers "which tree of this
repository did the deployment use?" for an audit. A commit alone does not answer it when the files on disk differ
from the commit, hence `dirty`.

Read only: never raises, never writes (`--no-optional-locks` keeps `git status` from refreshing the index of a
checkout someone else owns). What to do with an unreadable checkout, and how to word it, is the caller's: only the
caller knows the remote's name and what the record is for.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from strata.utils.transport import run_command

_GIT_TIMEOUT = 30


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
