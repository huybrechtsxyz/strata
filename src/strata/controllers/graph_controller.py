#!/usr/bin/env python3
"""Building the cross-document reference graph — every edge, not just the broken ones.

`strata.controllers.references.validate_references` walks the same data and
stops at the interesting subset for validation: references that do *not*
resolve. A graph needs the opposite bias — every edge, resolved or not — so a
consumer (the `graph` command, an eventual VS Code tree view) can render the
whole shape of a solution rather than only its defects.

Reuses `extract_references()`/`references_in()` unchanged (the same field
metadata and value-walk `validate_references` uses) rather than re-deriving
reference locations a second way — two independently-maintained walks over
the same models is exactly the drift `reference_fields.py`'s own docstring
describes replacing.
"""

from dataclasses import dataclass

from strata.controllers.references import references_in
from strata.controllers.solution_controller import DocumentIndex
from strata.models.reference_fields import extract_references


@dataclass(frozen=True)
class GraphEdge:
    """One reference, from the document that declared it to what it names.

    `resolved` is computed against the index at build time rather than left
    for a consumer to re-derive — a tree view wants to colour a dangling
    reference immediately, not reimplement the lookup.
    """

    source_kind: str
    source_name: str
    source_path: str
    location: str
    target_kind: str
    target_name: str
    resolved: bool


def build_reference_graph(index: DocumentIndex) -> list[GraphEdge]:
    """Return every cross-document reference edge in `index`.

    Remote references (`RemoteReference`, `rule.kind is None`) are omitted —
    a remote names an entry in the solution manifest, not a document in this
    graph, and `resolved` would need a second, unrelated lookup to mean
    anything.

    Args:
        index: The loaded `DocumentIndex` (Phase 1 schema validation only —
            a document that failed to load was never added, so it simply
            contributes no edges, the same degradation `validate_references`
            relies on).

    Returns:
        Edges in discovery order, matching `index.all()` — stable for a
        consumer that diffs two runs, and not sorted for the same reason
        `Diagnostics` is not: findings about one document stay adjacent to
        where they were found.
    """
    edges: list[GraphEdge] = []
    for entry in index.all():
        for rule in extract_references(type(entry.model)):
            if rule.kind is None:
                continue
            for location, name in references_in(entry.model, rule.path):
                edges.append(
                    GraphEdge(
                        source_kind=entry.ref.kind.value,
                        source_name=entry.ref.name,
                        source_path=str(entry.source),
                        location=location,
                        target_kind=rule.kind.value,
                        target_name=name,
                        resolved=name in index.names_of(rule.kind),
                    )
                )
    return edges
