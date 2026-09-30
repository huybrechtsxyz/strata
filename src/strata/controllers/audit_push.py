#!/usr/bin/env python3
"""Push audit files to a durable git destination — the `git` sink arm's
transport (docs/design/audit-trail.md's "Durable push destination"/"The
endpoints").

Phase 4 of that doc's Layer 2 Implementation Plan. A new, push-specific
resolution — deliberately NOT reusing `resolve_remote()`
(`controllers/remote_resolution.py`):

- Keyed by `(remote, branch)` (`layout.audit_push_checkout_path()`), not
  `(remote, reference)` like `resolve_remote()` — a push destination is a
  mutable branch tip, refreshed before every write, not a pinned read
  source.
- Always fetches + resets before writing — never trusts an existing
  checkout indefinitely the way `resolve_remote()`'s read-path caching
  deliberately does ("this function's job is locating a checkout, not
  verifying one" does not apply here).
- Adds `add`/`commit`/`push`, which `resolve_remote()` never does at all.

Reuses the git-via-subprocess transport pattern (`run_command`) and the
`SolutionRemoteModel.integration` credential-indirection field already
established there. No separate credential resolution exists yet — same as
`remote_resolution.py`'s own current state: relies entirely on git's own
ambient auth (SSH agent, credential helper). `remote.integration` is
resolved by name but not yet consulted for actual credential material,
matching that file's own documented gap.

Uses `git push origin HEAD:<branch>` (v1's real refspec trick) rather than
checking out a local branch by name — pushes whatever commit is currently
checked out to the named remote branch regardless of local branch state,
creating the branch on first push. Combined with always resetting to
`origin/<branch>` (or the remote's default when the branch doesn't exist
yet, or nothing at all for a genuinely brand-new empty remote — found by
a real end-to-end test against a real empty bare repo, not assumed)
immediately beforehand, this needs no local branch bookkeeping at all.
"""

from dataclasses import dataclass
from pathlib import Path

from strata.models.audit_model import AuditGitSinkTargetModel
from strata.models.solution_model import RemoteType, SolutionModel
from strata.utils import layout
from strata.utils.transport import run_command

_GIT_TIMEOUT = 300


@dataclass(frozen=True)
class PushResult:
    """The outcome of a push attempt. Never raised — a caller (Phase 5) decides
    whether a failure warns or fails the run, per the sink's own `required` flag."""

    success: bool
    detail: str = ""


def push_audit_files(
    root: Path,
    sink: AuditGitSinkTargetModel,
    solution: SolutionModel | None,
    files: dict[str, Path],
    relative_path: Path,
    actor: str,
) -> PushResult:
    """Push `files` (dest filename -> local source path) into `sink`'s repo,
    under `sink.path / relative_path`, on branch `sink.branch`.

    Never raises for an ordinary push failure — an unknown remote, a
    non-git remote, or any git subprocess failure is folded into
    `PushResult.success = False` with a human-readable `detail`.
    """
    remotes_by_name = {remote.name: remote for remote in (solution.spec.remotes or [])} if solution else {}
    remote = remotes_by_name.get(sink.remote)
    if remote is None:
        return PushResult(False, f"sink remote '{sink.remote}' is not declared in this solution's spec.remotes")
    if remote.type != RemoteType.GIT:
        return PushResult(
            False, f"remote '{remote.name}' has type '{remote.type.value}', but an audit push needs 'git'"
        )

    checkout_path = layout.audit_push_checkout_path(root, remote.name, sink.branch)
    ensured = _ensure_checkout(remote.url, sink.branch, checkout_path)
    if not ensured.success:
        return ensured

    configured = _configure_identity(checkout_path, actor)
    if not configured.success:
        return configured

    dest_dir = checkout_path / sink.path / relative_path
    dest_dir.mkdir(parents=True, exist_ok=True)
    for filename, source_path in files.items():
        (dest_dir / filename).write_bytes(source_path.read_bytes())

    add_result = run_command(["git", "add", "-A"], cwd=checkout_path, timeout=_GIT_TIMEOUT)
    if not add_result.is_successful:
        return PushResult(False, f"'git add' failed: {add_result.stderr.strip()}")

    status_result = run_command(["git", "status", "--porcelain"], cwd=checkout_path, timeout=_GIT_TIMEOUT)
    if not status_result.stdout.strip():
        return PushResult(True, "nothing to commit — files already match the last pushed copy")

    commit_result = run_command(
        ["git", "commit", "-m", f"audit: {relative_path.as_posix()}"], cwd=checkout_path, timeout=_GIT_TIMEOUT
    )
    if not commit_result.is_successful:
        return PushResult(False, f"'git commit' failed: {commit_result.stderr.strip()}")

    push_result = run_command(["git", "push", "origin", f"HEAD:{sink.branch}"], cwd=checkout_path, timeout=_GIT_TIMEOUT)
    if not push_result.is_successful:
        return PushResult(False, f"'git push' failed: {push_result.stderr.strip()}")

    return PushResult(True)


def _ensure_checkout(url: str, branch: str, checkout_path: Path) -> PushResult:
    """Clone if absent, then always fetch + reset to a known-good remote ref —
    `origin/<branch>` when it already exists, else the remote's default branch.

    Never leaves stale local-only commits from a previous failed attempt
    sitting on HEAD — every call starts from a ref the remote actually has.
    """
    if not checkout_path.exists():
        checkout_path.parent.mkdir(parents=True, exist_ok=True)
        clone_result = run_command(["git", "clone", url, str(checkout_path)], timeout=_GIT_TIMEOUT)
        if not clone_result.is_successful:
            return PushResult(False, f"'git clone {url}' failed: {clone_result.stderr.strip()}")

    fetch_result = run_command(["git", "fetch", "origin"], cwd=checkout_path, timeout=_GIT_TIMEOUT)
    if not fetch_result.is_successful:
        return PushResult(False, f"'git fetch origin' failed: {fetch_result.stderr.strip()}")

    reset_ref = f"origin/{branch}"
    verify_result = run_command(["git", "rev-parse", "--verify", reset_ref], cwd=checkout_path, timeout=_GIT_TIMEOUT)
    if not verify_result.is_successful:
        # branch doesn't exist upstream yet — fall back to the remote's default branch
        default_verify = run_command(
            ["git", "rev-parse", "--verify", "origin/HEAD"], cwd=checkout_path, timeout=_GIT_TIMEOUT
        )
        if not default_verify.is_successful:
            # a brand-new, completely empty remote (no commits at all) — nothing to
            # reset to; the fresh clone/existing checkout is already the correct
            # starting state.
            return PushResult(True)
        reset_ref = "origin/HEAD"

    reset_result = run_command(["git", "reset", "--hard", reset_ref], cwd=checkout_path, timeout=_GIT_TIMEOUT)
    if not reset_result.is_successful:
        return PushResult(False, f"'git reset --hard {reset_ref}' failed: {reset_result.stderr.strip()}")
    return PushResult(True)


def _configure_identity(checkout_path: Path, actor: str) -> PushResult:
    """Set a local (not global) git identity so `git commit` never fails on a
    CI runner with no git identity configured — `actor` is `resolve_actor()`'s
    output, so the commit author reflects who actually triggered the run."""
    for key, value in (("user.name", actor), ("user.email", f"{actor}@strata-audit.local")):
        result = run_command(["git", "config", key, value], cwd=checkout_path, timeout=_GIT_TIMEOUT)
        if not result.is_successful:
            return PushResult(False, f"'git config {key}' failed: {result.stderr.strip()}")
    return PushResult(True)
