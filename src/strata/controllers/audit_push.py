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
established there.

**Real credentials + `fetch: external` (found missing entirely on review,
2026-10-08)** — this module used to be ambient-auth-only and ignored
`remote.fetch` outright, unlike `integrations/git_push.py` (the GitOps push
path, added *after* this module and explicitly documented as mirroring it)
and `controllers/remote_resolution.py`, both of which already honour
`remote.integration`/`fetch: external` for their own git operations. `auth`/
`resolved_values` (below) thread a resolved `AuthenticationModel` through to
`ensure_synced_checkout()`/the `git push` call the exact same way
`git_push.py`'s `push_file()` does; `fetch: external` is honoured by
`git_checkout.ensure_synced_checkout()` itself now, shared by this module's
and `audit_read.py`'s calls alike. The caller (`audit_run.py`) resolves
`remote.integration` -> `IntegrationModel.spec.authentication` and the
deployment's resolved values — this module only consumes the result, same
division of responsibility `deploy_controller.py` already uses for GitOps
steps.

Uses `git push origin HEAD:<branch>` (v1's real refspec trick) rather than
checking out a local branch by name — pushes whatever commit is currently
checked out to the named remote branch regardless of local branch state,
creating the branch on first push. Combined with always resetting to
`origin/<branch>` (or the remote's default when the branch doesn't exist
yet, or nothing at all for a genuinely brand-new empty remote — found by
a real end-to-end test against a real empty bare repo, not assumed)
immediately beforehand, this needs no local branch bookkeeping at all.

The clone/fetch/reset sequence itself (`_ensure_checkout()`, originally
private here) moved to `controllers/git_checkout.py::ensure_synced_checkout()`
once `strata audit status`'s read-only checkout
(docs/design/audit-commands.md) needed the identical behaviour with none
of this module's write-specific steps (`_configure_identity()`, `git add`,
`git commit`, `git push`) — shared, not duplicated.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from strata.controllers.git_checkout import ensure_synced_checkout
from strata.integrations.git_push import credential_env
from strata.models.audit_model import AuditGitSinkTargetModel
from strata.models.auth_models import AuthenticationModel
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
    *,
    auth: AuthenticationModel | None = None,
    resolved_values: Mapping[str, str] | None = None,
) -> PushResult:
    """Push `files` (dest filename -> local source path) into `sink`'s repo,
    under `sink.path / relative_path`, on branch `sink.branch`.

    Never raises for an ordinary push failure — an unknown remote, a
    non-git remote, or any git subprocess failure is folded into
    `PushResult.success = False` with a human-readable `detail`.

    Args:
        auth: `remote.integration`'s resolved `AuthenticationModel`, when the
            caller has one — turned into `GIT_ASKPASS`/SSH env overrides via
            `git_push.credential_env()`. `None` (default) preserves the
            previous ambient-only behaviour exactly.
        resolved_values: Fully-resolved deploy-time values `auth`'s key
            references resolve against (`git_push.prepare_git_credentials()`).
            Ignored when `auth` is `None`.
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
    with credential_env(auth, resolved_values, None) as env:
        ensured = ensure_synced_checkout(remote.url, sink.branch, checkout_path, fetch=remote.fetch, env=env)
        if not ensured.success:
            return PushResult(False, ensured.detail)

        configured = _configure_identity(checkout_path, actor, env=env)
        if not configured.success:
            return configured

        dest_dir = checkout_path / sink.path / relative_path
        dest_dir.mkdir(parents=True, exist_ok=True)
        for filename, source_path in files.items():
            (dest_dir / filename).write_bytes(source_path.read_bytes())

        add_result = run_command(["git", "add", "-A"], cwd=checkout_path, env=env, timeout=_GIT_TIMEOUT)
        if not add_result.is_successful:
            return PushResult(False, f"'git add' failed: {add_result.stderr.strip()}")

        status_result = run_command(["git", "status", "--porcelain"], cwd=checkout_path, env=env, timeout=_GIT_TIMEOUT)
        if not status_result.stdout.strip():
            return PushResult(True, "nothing to commit — files already match the last pushed copy")

        commit_result = run_command(
            ["git", "commit", "-m", f"audit: {relative_path.as_posix()}"],
            cwd=checkout_path,
            env=env,
            timeout=_GIT_TIMEOUT,
        )
        if not commit_result.is_successful:
            return PushResult(False, f"'git commit' failed: {commit_result.stderr.strip()}")

        push_result = run_command(
            ["git", "push", "origin", f"HEAD:{sink.branch}"], cwd=checkout_path, env=env, timeout=_GIT_TIMEOUT
        )
        if not push_result.is_successful:
            return PushResult(False, f"'git push' failed: {push_result.stderr.strip()}")

    return PushResult(True)


def _configure_identity(checkout_path: Path, actor: str, *, env: Mapping[str, str] | None = None) -> PushResult:
    """Set a local (not global) git identity so `git commit` never fails on a
    CI runner with no git identity configured — `actor` is `resolve_actor()`'s
    output, so the commit author reflects who actually triggered the run."""
    for key, value in (("user.name", actor), ("user.email", f"{actor}@strata-audit.local")):
        result = run_command(["git", "config", key, value], cwd=checkout_path, env=env, timeout=_GIT_TIMEOUT)
        if not result.is_successful:
            return PushResult(False, f"'git config {key}' failed: {result.stderr.strip()}")
    return PushResult(True)
