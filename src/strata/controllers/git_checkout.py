#!/usr/bin/env python3
"""Shared git checkout sync — clone-if-absent, then fetch + reset to a known
remote ref (docs/design/audit-commands.md's "Read mechanism — reuse, not
reimplement, the push side's fetch+reset").

Extracted from `audit_push.py`'s own private `_ensure_checkout()` (Phase 4 of
audit-trail.md's Layer 2 Implementation Plan) once a second caller needed the
exact same behaviour: `strata audit status`'s read-only checkout
(docs/design/audit-commands.md's Phase 3, `controllers/audit_read.py`) needs
the identical clone/fetch/reset sequence, with none of `audit_push.py`'s
write-specific steps (`_configure_identity()`, `git add`, `git commit`,
`git push`). Same "fetch and push are one capability" instinct
`remote_resolution.py`'s own docstring already states for credential
handling, applied here to the sync mechanics themselves — not duplicated
logic between a push module and a read module.

**`fetch`/`env` (found missing on review, 2026-10-08)** — `remote_resolution.
resolve_remote()` already honours both `RemoteFetch.EXTERNAL` (never attempts
its own clone; raises naming the expected checkout path) and `auth`/
`resolved_values`-derived credentials for a plain read. This module's two
callers (`audit_push.py`, `audit_read.py`) did neither — every audit git
operation always attempted an ambient, unauthenticated `git clone` of
`remote.url` regardless of `remote.fetch`/`remote.integration`, silently
ignoring both. `fetch` defaults to `RemoteFetch.STRATA` and `env` to `None`,
so every pre-existing call site (none of which knew about either) keeps
today's behaviour exactly.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from strata.models.solution_model import RemoteFetch
from strata.utils.transport import run_command

_GIT_TIMEOUT = 300


@dataclass(frozen=True)
class SyncResult:
    """The outcome of a checkout sync attempt. Never raised — each caller
    (`audit_push.py`'s `PushResult`, `audit_read.py`'s `AuditReadError`)
    decides how to surface a failure in its own vocabulary."""

    success: bool
    detail: str = ""


def ensure_synced_checkout(
    url: str,
    branch: str,
    checkout_path: Path,
    *,
    fetch: RemoteFetch = RemoteFetch.STRATA,
    env: Mapping[str, str] | None = None,
) -> SyncResult:
    """Clone if absent, then always fetch + reset to a known-good remote ref —
    `origin/<branch>` when it already exists, else the remote's default branch.

    Never leaves stale local-only commits from a previous failed attempt
    sitting on HEAD — every call starts from a ref the remote actually has.
    Used identically by a push destination (always refreshed immediately
    before writing) and a read-only checkout (always refreshed immediately
    before globbing) — neither trusts an existing checkout indefinitely,
    unlike `resolve_remote()`'s pinned-ref read-path caching, which
    deliberately does (a pinned ref's content cannot change out from under
    it; a mutable branch tip can).

    Args:
        fetch: `remote.fetch` — `RemoteFetch.EXTERNAL` means CI/a developer
            is responsible for placing `checkout_path` themselves; this
            function must never attempt its own clone in that case, matching
            `remote_resolution.resolve_remote()`'s own handling of the same
            field for a plain (non-audit) remote read.
        env: Merged into every network-touching git subprocess call
            (`clone`/`fetch`) — `remote.integration`'s resolved credentials,
            via `strata.integrations.git_push.credential_env()`. `None`
            (default) preserves today's ambient-only behaviour exactly.
    """
    if not checkout_path.exists():
        if fetch is RemoteFetch.EXTERNAL:
            return SyncResult(
                False,
                f"fetch: external, expected an existing checkout at '{checkout_path}', but nothing is there. "
                "Make sure your CI checkout step (or a manual git clone/copy) places it at exactly this path "
                "before running strata.",
            )
        checkout_path.parent.mkdir(parents=True, exist_ok=True)
        clone_result = run_command(["git", "clone", url, str(checkout_path)], env=env, timeout=_GIT_TIMEOUT)
        if not clone_result.is_successful:
            return SyncResult(False, f"'git clone {url}' failed: {clone_result.stderr.strip()}")

    fetch_result = run_command(["git", "fetch", "origin"], cwd=checkout_path, env=env, timeout=_GIT_TIMEOUT)
    if not fetch_result.is_successful:
        return SyncResult(False, f"'git fetch origin' failed: {fetch_result.stderr.strip()}")

    reset_ref = f"origin/{branch}"
    verify_result = run_command(
        ["git", "rev-parse", "--verify", reset_ref], cwd=checkout_path, env=env, timeout=_GIT_TIMEOUT
    )
    if not verify_result.is_successful:
        # branch doesn't exist upstream yet — fall back to the remote's default branch
        default_verify = run_command(
            ["git", "rev-parse", "--verify", "origin/HEAD"], cwd=checkout_path, env=env, timeout=_GIT_TIMEOUT
        )
        if not default_verify.is_successful:
            # a brand-new, completely empty remote (no commits at all) — nothing to
            # reset to; the fresh clone/existing checkout is already the correct
            # starting state.
            return SyncResult(True)
        reset_ref = "origin/HEAD"

    reset_result = run_command(["git", "reset", "--hard", reset_ref], cwd=checkout_path, env=env, timeout=_GIT_TIMEOUT)
    if not reset_result.is_successful:
        return SyncResult(False, f"'git reset --hard {reset_ref}' failed: {reset_result.stderr.strip()}")
    return SyncResult(True)
