#!/usr/bin/env python3
"""Filesystem layout — the single owner of every path strata derives.

**Every derived path belongs here.** Nothing outside this module should join
a path segment, hardcode a directory name, or decide where something lands on
disk. Call a function here instead, and add one when a new location is needed.

This exists because of a specific v1 failure. v1 recorded a remote's checkout
location in *both* `config/remotes.yaml` and `.strata/solution.json`, and
required humans to keep the two equal. When they diverged, builds failed with
"has not been fetched yet" against a repository that was already fetched
(ADR-0015). The bug was not the value — it was that two places owned it.

The same shape was already forming in v2: `.strata` appeared in the discovery
walk's ignore list while ADR-0019 placed remote checkouts underneath it. Two
independent constants that had to agree, in different modules. Here they are
one constant, and `DEFAULT_IGNORED_DIRS` is *derived* from `STRATA_DIR`, so
the walk cannot stop ignoring the directory the checkouts live in.

Functions are pure: they compute paths and never touch the filesystem. That
keeps them trivially testable and callable before anything exists on disk.
"""

from pathlib import Path

#: The solution manifest filename — both the root marker and the recursion
#: boundary for discovery (a nested one means a different solution).
MANIFEST_FILENAME = "strata.yaml"

#: Runtime state directory, relative to the solution root. Runtime-only and
#: never committed (ADR-0015), so it is always excluded from discovery.
STRATA_DIR = ".strata"

#: Subdirectory of `STRATA_DIR` holding materialised remotes.
REMOTES_DIRNAME = "remotes"

#: File suffixes discovery treats as candidate documents.
YAML_SUFFIXES = (".yaml", ".yml")

#: Directories never descended into, regardless of configuration. Tool caches,
#: virtualenvs, build output, and the runtime state directory. This is the
#: floor; `spec.discovery.exclude` adds to it and cannot remove from it, so a
#: user-supplied pattern can never re-enable scanning `.git`.
#:
#: Deliberately NOT here: `.archive/`, `repos/` and similar. Those are local
#: conventions observed in particular repositories, not universals — a
#: solution that wants them skipped declares them in `spec.discovery.exclude`.
#: `repos/` in particular is already handled structurally: a checked-out
#: remote carries its own `strata.yaml`, which stops recursion.
DEFAULT_IGNORED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        STRATA_DIR,
        "build",
        "dist",
    }
)


def manifest_path(root: Path) -> Path:
    """Return the solution manifest path for `root`."""
    return root / MANIFEST_FILENAME


def strata_dir(root: Path) -> Path:
    """Return the runtime state directory for `root`."""
    return root / STRATA_DIR


def remotes_dir(root: Path) -> Path:
    """Return the directory holding all materialised remotes."""
    return strata_dir(root) / REMOTES_DIRNAME


def remote_checkout_path(root: Path, remote: str, reference: str | None) -> Path:
    """Return where a remote materialises on disk, keyed by resolved ref.

    Keyed by ref because version pins make materialisation *deployment*-scoped:
    two deployments in one solution, selecting different Version documents, can
    need the same remote at two refs (ADR-0019). A single directory per remote
    would have them silently overwrite each other, with the winner decided by
    execution order.

    A remote with no reference (helm indexes, local paths) resolves to a
    fixed `_unpinned` segment rather than sitting directly under the remote
    name, so a ref named `_unpinned` cannot collide with it and every checkout
    is at the same depth.

    Args:
        root: Solution root.
        remote: The remote's declared name.
        reference: Resolved git/OCI ref, or None when the remote has no ref.

    Returns:
        The checkout directory. Not created — this function is pure.
    """
    return remotes_dir(root) / remote / (reference or "_unpinned")
