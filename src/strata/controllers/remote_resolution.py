#!/usr/bin/env python3
"""Resolving a `SolutionRemoteModel` to a real filesystem path (docs/design/remotes.md).

**Why the checkout path never needs a stored side-file.**
`layout.remote_checkout_path(root, remote, reference)` is a pure function of
already-known values — the exact bug `layout.py`'s own docstring records
(v1 stored a remote's checkout location in *both* `config/remotes.yaml` and
`.strata/solution.json`, and required humans to keep them equal) cannot
recur here, because there is only ever one path a given `(remote, reference)`
pair can resolve to. No repo-map file, no second thing to keep in sync.

**`type`/`fetch` are already split for a reason found in real evidence, not
invented here** (see `RemoteFetch`'s own docstring in `solution_model.py`):
v1 conflated the two, so a real `config/remotes.yaml` had to declare a git
repository as `type: bundled` purely to make strata skip its own fetch in a
CI environment with no git credentials — silently losing ref-pinning and
dirty-tree gating as a side effect of a type lie. `type: git` +
`fetch: external` states the same real case honestly instead.

**Real credentials (docs/design/gitops-integration.md Implementation Plan
Phase 4)** — `auth`/`resolved_values` are optional, default to `None`, and
preserve today's ambient-only clone behaviour exactly when omitted (every
existing caller). When given, `_git_clone()` threads them through
`strata.integrations.git_push.prepare_git_credentials()` — the same
primitive `push_file()`/`remove_file()` use, confirming fetch and push are
one capability, not two. Importing from `strata.integrations` here is
allowed (ADR-0003: a higher layer, `controllers/`, may import a lower one).
"""

from collections.abc import Mapping
from pathlib import Path

from strata.integrations.errors import IntegrationError
from strata.integrations.git_push import credential_env
from strata.models.auth_models import AuthenticationModel
from strata.models.solution_model import RemoteFetch, RemoteType, SolutionRemoteModel
from strata.utils import layout
from strata.utils.errors import SystemError
from strata.utils.transport import run_command

_GIT_CLONE_TIMEOUT = 300
_GIT_CHECKOUT_TIMEOUT = 60


class RemoteResolutionError(SystemError):
    """A remote could not be resolved to a real, existing filesystem path.

    A `SystemError`, not a `UsageError`/`ValidationError` — every case this
    raises for (a missing `fetch: external` checkout, a failed git clone) is
    "the environment is not set up as declared", not a bad CLI invocation or
    an invalid document.
    """


def resolve_remote(
    root: Path,
    remote: SolutionRemoteModel | None,
    *,
    auth: AuthenticationModel | None = None,
    resolved_values: Mapping[str, str] | None = None,
) -> Path:
    """Resolve `remote` to an existing directory on disk.

    Args:
        root: The solution root (where `strata.yaml` lives).
        remote: The declared remote, or `None` — `SourceModel.remote` being
            unset means "this solution's own repository" (ADR-0018), which
            resolves to `root` itself, no materialisation involved.
        auth: `remote.integration`'s resolved `AuthenticationModel`, when the
            caller has one and the remote needs non-ambient credentials
            (docs/design/gitops-integration.md Phase 4). `None` (default)
            preserves today's ambient-only clone behaviour exactly.
        resolved_values: Fully-resolved deploy-time values `auth`'s key
            references resolve against — see `git_push.
            prepare_git_credentials()`. Ignored when `auth` is `None`.

    Returns:
        An existing directory. Never a path that might not exist yet — every
        branch below either finds one or raises.

    Raises:
        RemoteResolutionError: `fetch: external` names a path nothing has
            checked out yet, the remote's `type` has no fetch mechanism
            built (`oci`/`helm` — see docs/design/remotes.md), or the git
            clone/checkout itself failed.
    """
    if remote is None:
        return root

    if remote.type == RemoteType.LOCAL:
        # A 'local' remote is never materialised under .strata/remotes/ —
        # its `url` already *is* the solution-relative location (matches the
        # real `config` remote in config-deploy's remotes.yaml: `type:
        # bundled` in v1 terms, `url: "."`).
        return (root / remote.url).resolve()

    checkout_path = layout.remote_checkout_path(root, remote.name, remote.reference)
    if checkout_path.exists():
        # Already there — whether strata cloned it earlier (ref is baked
        # into the path, so an existing directory cannot be stale) or CI/a
        # developer placed it here for `fetch: external`. Either way, trust
        # it; this function's job is locating a checkout, not verifying one.
        return checkout_path

    if remote.fetch == RemoteFetch.EXTERNAL:
        raise RemoteResolutionError(
            f"Remote '{remote.name}' is fetch: external, expected an existing checkout at "
            f"'{checkout_path}', but nothing is there. Make sure your CI checkout step (or a "
            "manual git clone/copy) places it at exactly this path before running strata."
        )

    if remote.type != RemoteType.GIT:
        raise RemoteResolutionError(
            f"Remote '{remote.name}' has type '{remote.type.value}', which strata cannot fetch "
            "yet (only 'git' is implemented) — see docs/design/remotes.md."
        )

    return _git_clone(remote, checkout_path, auth=auth, resolved_values=resolved_values)


def _git_clone(
    remote: SolutionRemoteModel,
    checkout_path: Path,
    *,
    auth: AuthenticationModel | None = None,
    resolved_values: Mapping[str, str] | None = None,
) -> Path:
    """Clone `remote.url` at `remote.reference` into `checkout_path`.

    Ambient-only by default (`auth=None`, SSH agent/credential helper/
    `~/.gitconfig`, whatever git already has configured) — a private remote
    with no local git auth configured fails here with git's own error
    message, not a silent hang. `auth`/`resolved_values` (docs/design/
    gitops-integration.md Phase 4) opt into real, non-ambient credentials via
    `git_push.prepare_git_credentials()` — the one point fetch and push
    actually share executable logic, not just a conceptual capability.

    Raises:
        RemoteResolutionError: the clone/checkout itself failed, or `auth`
            names a key reference that doesn't resolve against
            `resolved_values` (`IntegrationError`, caught and converted here
            — a plain `Exception`, not a `StrataError`, that would otherwise
            escape as a raw traceback instead of this module's own
            established error type).
    """
    checkout_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with credential_env(auth, resolved_values, None) as env:
            clone_result = run_command(
                ["git", "clone", remote.url, str(checkout_path)], env=env, timeout=_GIT_CLONE_TIMEOUT
            )
            if not clone_result.is_successful:
                raise RemoteResolutionError(
                    f"Remote '{remote.name}': 'git clone {remote.url}' failed: {clone_result.stderr.strip()}"
                )
            if remote.reference is not None:
                checkout_result = run_command(
                    ["git", "checkout", remote.reference], cwd=checkout_path, env=env, timeout=_GIT_CHECKOUT_TIMEOUT
                )
                if not checkout_result.is_successful:
                    raise RemoteResolutionError(
                        f"Remote '{remote.name}': 'git checkout {remote.reference}' failed: "
                        f"{checkout_result.stderr.strip()}"
                    )
    except IntegrationError as exc:
        raise RemoteResolutionError(f"Remote '{remote.name}': {exc}") from exc
    return checkout_path
