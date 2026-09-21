#!/usr/bin/env python3
"""Canonical detection/parsing for the ``@repo_name/...`` cross-repo reference convention.

Ported from v1's real `strata/utils/system.py` — created there specifically
because roughly 15 call sites across v1's codebase independently re-detected
this convention via raw ``str.startswith("@")`` instead of one shared
predicate (v1's real ADR-0073, "embedded string syntax inventory and creep
prevention"). These two helpers are the single, canonical detection/split
point for the ``@repo_name/...`` convention specifically.

Deliberately does **not** cover the unrelated ``@module/service`` cross-module
dependency syntax (`ModuleSpecModel.validate_depends_on` in `module_model.py`),
which reuses the bare ``@`` character for a different meaning — v1 documents
this distinction explicitly, and callers there must keep their own detection.

Only the pure detection/parsing helpers are ported here — v1's `resolve_path()`
(actual filesystem resolution given a ``repo_map``) and `resolve_work_path()`
(``.strata/`` workspace-root discovery) are deliberately NOT ported yet: both
need concepts (a repo-name-to-path map, a CLI/work-path notion) that don't
exist anywhere in v2's models-only layer — no solution-loading machinery
exists to build a `repo_map` from yet.
"""


def is_cross_repo_ref(value: str | None) -> bool:
    """Return True if *value* looks like an ``@repo_name/...`` cross-repo file reference.

    The single detection point for this convention — call this instead of
    ``value.startswith("@")`` directly, so every site agrees on what counts as
    a cross-repo reference. Does not validate that the repo actually exists;
    that's a resolution concern for a future solution-loading layer.

    Args:
        value: Candidate string, or ``None``.

    Returns:
        bool: True if *value* is non-empty and starts with ``@``.
    """
    return value is not None and value.startswith("@")


def split_repo_ref(value: str | None) -> dict[str, str] | None:
    """Split an ``@repo_name/relative/path`` reference into its parts.

    Args:
        value: Candidate string, or ``None``.

    Returns:
        ``{"repo_name": ..., "rest": ...}`` if *value* is a cross-repo
        reference (``rest`` is ``""`` for a bare ``@repo_name`` with no
        trailing path), or ``None`` if *value* is not a cross-repo reference
        at all. Does not validate the repo name against a real repo
        registry — that is a resolution concern, not a parsing concern.
    """
    if not is_cross_repo_ref(value):
        return None
    assert value is not None  # narrows for mypy — is_cross_repo_ref() already confirmed this
    repo_name, _, rest = value[1:].partition("/")
    return {"repo_name": repo_name, "rest": rest}
