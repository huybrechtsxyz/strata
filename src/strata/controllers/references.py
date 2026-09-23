#!/usr/bin/env python3
"""Cross-document reference checking.

Since ADR-0015 every link between documents is a `(kind, name)` identity
reference rather than a file path, which makes each one a dictionary lookup
against the index. Nothing was doing those lookups, so a solution naming a
provider, workspace or environment that does not exist validated cleanly.

Reference rules come from `strata.models.reference_fields`: a `References`
marker attached to the field itself via `Annotated`, discovered by walking
each document's own model class. See that module's docstring for why this
replaced a hand-written table — in short, the table already had a real gap
(`ModuleReferenceModel.module`, embedded in both Topology and Namespace,
under neither kind) that colocating the reference with the field closes.

**Deliberately unannotated: topology components and namespaces.** Those name
workspace-local instances (`WorkspaceResourceModel.name`), not documents: an
instance `web-storage` may be built from a Resource document called
`storage-account`. `WorkspaceService.validate_topology_references` checks
them in the right scope; checking them here would invent failures.
"""

from collections.abc import Iterator
from difflib import get_close_matches
from typing import Any

from strata.controllers.solution_controller import DocumentIndex
from strata.models.reference_fields import extract_references
from strata.models.solution_model import SolutionModel
from strata.utils.diagnostics import Diagnostics

#: How many suggestions to offer for an unresolved name.
_MAX_SUGGESTIONS = 3


def _walk(value: Any, segments: list[str], prefix: str) -> Iterator[tuple[str, str]]:
    """Yield `(location, name)` for every reference the path reaches."""
    if not segments:
        if isinstance(value, str):
            yield prefix, value
        elif isinstance(value, list):
            for position, item in enumerate(value):
                if isinstance(item, str):
                    yield f"{prefix}.{position}", item
        return

    head, *rest = segments
    iterate = head.endswith("[]")
    attribute = head[:-2] if iterate else head
    child = getattr(value, attribute, None)
    if child is None:
        return

    location = f"{prefix}.{attribute}" if prefix else attribute
    if iterate:
        for position, item in enumerate(child):
            yield from _walk(item, rest, f"{location}.{position}")
    else:
        yield from _walk(child, rest, location)


def references_in(model: Any, path: str) -> Iterator[tuple[str, str]]:
    """Yield `(location, name)` for a dotted path on a document."""
    yield from _walk(model, path.split("."), "")


def _unresolved(name: str, known: set[str], target: str) -> str:
    """Explain a miss, suggesting a correction when one is plausible."""
    close = get_close_matches(name, known, n=_MAX_SUGGESTIONS, cutoff=0.6)
    if close:
        return f"unknown {target} '{name}' — did you mean {' or '.join(repr(c) for c in close)}?"
    if not known:
        return f"unknown {target} '{name}' — no {target} documents exist in this solution"
    return f"unknown {target} '{name}'. Available: {sorted(known)}"


def validate_references(index: DocumentIndex, solution: SolutionModel | None = None) -> Diagnostics:
    """Check every declared reference resolves to a document that exists.

    Args:
        index: The loaded `DocumentIndex`.
        solution: The manifest, for resolving remote names. Remote checks are
            skipped when it is absent.

    Returns:
        One error per unresolved reference, located at the field that declared
        it and attributed to the document it came from.
    """
    diagnostics = Diagnostics()
    remote_names = {remote.name for remote in (solution.spec.remotes or [])} if solution else None

    for entry in index.all():
        for rule in extract_references(type(entry.model)):
            for location, name in references_in(entry.model, rule.path):
                if rule.kind is None:
                    if remote_names is None:
                        continue
                    if name not in remote_names:
                        diagnostics.error(
                            _unresolved(name, remote_names, "remote"),
                            source=str(entry.source),
                            location=location,
                            code="unknown_remote",
                        )
                    continue

                known = index.names_of(rule.kind)
                if name not in known:
                    diagnostics.error(
                        _unresolved(name, known, rule.kind.value),
                        source=str(entry.source),
                        location=location,
                        code="unknown_reference",
                    )

    return diagnostics
