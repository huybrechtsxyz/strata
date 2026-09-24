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
"""

from pathlib import Path

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


def resolve_remote(root: Path, remote: SolutionRemoteModel | None) -> Path:
    """Resolve `remote` to an existing directory on disk.

    Args:
        root: The solution root (where `strata.yaml` lives).
        remote: The declared remote, or `None` — `SourceModel.remote` being
            unset means "this solution's own repository" (ADR-0018), which
            resolves to `root` itself, no materialisation involved.

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
        # real `config` remote in cfg-int-deployment's remotes.yaml: `type:
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

    return _git_clone(remote, checkout_path)


def _git_clone(remote: SolutionRemoteModel, checkout_path: Path) -> Path:
    """Clone `remote.url` at `remote.reference` into `checkout_path`.

    No credential handling yet (no `SourceIntegration`/`"sources"` capability
    ABC exists — ADR-0021 D9, docs/design/remotes.md's own remaining work) —
    relies entirely on whatever git already has configured (SSH agent,
    credential helper). A private remote with no local git auth configured
    fails here with git's own error message, not a silent hang.
    """
    checkout_path.parent.mkdir(parents=True, exist_ok=True)
    clone_result = run_command(["git", "clone", remote.url, str(checkout_path)], timeout=_GIT_CLONE_TIMEOUT)
    if not clone_result.is_successful:
        raise RemoteResolutionError(
            f"Remote '{remote.name}': 'git clone {remote.url}' failed: {clone_result.stderr.strip()}"
        )
    if remote.reference is not None:
        checkout_result = run_command(
            ["git", "checkout", remote.reference], cwd=checkout_path, timeout=_GIT_CHECKOUT_TIMEOUT
        )
        if not checkout_result.is_successful:
            raise RemoteResolutionError(
                f"Remote '{remote.name}': 'git checkout {remote.reference}' failed: "
                f"{checkout_result.stderr.strip()}"
            )
    return checkout_path
