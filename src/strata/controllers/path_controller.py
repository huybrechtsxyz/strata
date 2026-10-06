#!/usr/bin/env python3
"""Resolving `(kind, name)` to the real file strata loaded it from.

`strata path get`/`strata path list` need no resolution beyond Phase 1
(schema-valid loading) — the same reason `graph_command` never calls
`require_valid()`: finding where a document lives, or enumerating every
document of one kind, is meaningful even when some unrelated document
elsewhere in the solution has a Phase 2 problem. This is also the exact
resolution `version set` (docs/work/version-lifecycle.md Phase 4) will
reuse internally rather than inventing a second path-finding mechanism
inside that command — one mechanism, not two, even inside strata.
"""

from dataclasses import dataclass

from strata.controllers.solution_controller import DocumentIndex
from strata.models.common_models import PlatformKind


@dataclass(frozen=True)
class PathResult:
    """One `(kind, name)`'s resolved location, or its confirmed absence.

    `path` is `None` exactly when `exists` is `False` — never a guessed or
    placeholder path for something that isn't indexed.
    """

    kind: str
    name: str
    path: str | None
    exists: bool


def get_path(index: DocumentIndex, kind: PlatformKind, name: str) -> PathResult:
    """Resolve one `(kind, name)` to its real file path, or report absence.

    Absence is a normal, reportable outcome here — not an exception — so a
    caller (the CLI command, or `version set` internally) can decide what
    "not found" means for its own situation rather than catching one.
    """
    entry = index.get(kind, name)
    if entry is None:
        return PathResult(kind=kind.value, name=name, path=None, exists=False)
    return PathResult(kind=kind.value, name=name, path=str(entry.source), exists=True)


def list_paths(index: DocumentIndex, kind: PlatformKind, *, workspace: str | None = None) -> list[PathResult]:
    """Every indexed document of `kind`, by name — always `exists=True`
    (nothing absent could appear in a listing of what is actually indexed),
    in the same name-sorted order `DocumentIndex.all_of()` already returns.

    `workspace`, when given, restricts the result to documents whose own
    `spec.workspace` matches — meaningful only for kinds whose spec model
    actually declares that field (today: `deployment`, `version`). A kind
    with no such field simply has nothing that can ever match a non-`None`
    filter, so it comes back empty rather than raising — the same "skip
    rather than guess a policy that was never declared" rule
    `_check_workspace_topology_components()` already applies elsewhere.
    """
    entries = index.all_of(kind)
    if workspace is not None:
        entries = [entry for entry in entries if _document_workspace(entry.model) == workspace]
    return [PathResult(kind=kind.value, name=entry.ref.name, path=str(entry.source), exists=True) for entry in entries]


def _document_workspace(model: object) -> str | None:
    """`model.spec.workspace`, or `None` when either the document has no
    `spec` or its `spec` declares no `workspace` field at all."""
    spec = getattr(model, "spec", None)
    return getattr(spec, "workspace", None)
