#!/usr/bin/env python3
"""Structured diagnostics — how every layer reports what it found.

One accumulating bag, pushed bottom-up: a service reports on one document, a
controller merges the bags from every document it loaded, and the command
layer renders the result once. Nothing re-derives, re-parses or re-classifies
what a lower layer already knew.

**Why not `list[str]`.** v2 previously flattened pydantic's structured errors
with `str(err)`, producing output like::

    main.yaml: {'type': 'missing', 'loc': ('spec', 'provisioners'),
                'msg': 'Field required', 'input': {'providers': [...]}}

which is unreadable for a human and unparseable for a machine — it is a Python
repr, not JSON — and buries the field path in prose. The information was
already structured; stringifying destroyed it. `--output json` then has nothing
useful to emit, and exit-code classification has to reconstruct severity from
the shape of the object afterwards (v1 did this with `hasattr` probes).

**Why not two lists.** Separate `messages`/`errors` lists encode severity
*positionally* — by which list you appended to. That gives warnings no home
(too noisy for errors, invisible in messages), splits related findings about
one document across two collections, and multiplies the API: v1 carried
`has_/get_/clear_` for each list, and a third severity would have meant three
more methods. One list with a severity field keeps related findings adjacent
and in the order they were discovered.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    """How much a finding matters."""

    ERROR = "error"
    """Invalid — the run must fail."""

    WARNING = "warning"
    """Suspicious but survivable: a pin matching nothing, a shadowed override."""

    INFO = "info"
    """Worth reporting, never a failure: what a value resolved to and why."""


@dataclass(frozen=True)
class Diagnostic:
    """A single finding, with enough structure to render or machine-read.

    Immutable: a finding is a record of something observed, so nothing should
    edit it after the fact.
    """

    severity: Severity
    message: str
    source: str | None = None
    """Where it was found — usually a file path or document reference."""

    location: str | None = None
    """Path within the document, e.g. `spec.execution.0.provisioner`."""

    code: str | None = None
    """Stable machine-readable classifier, e.g. pydantic's `missing`."""

    def __str__(self) -> str:
        """Render as `source: location: message [code]`, omitting empty parts."""
        parts = [part for part in (self.source, self.location) if part]
        parts.append(self.message)
        rendered = ": ".join(parts)
        return f"{rendered} [{self.code}]" if self.code else rendered

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable mapping, omitting absent fields."""
        result: dict[str, Any] = {"severity": self.severity.value, "message": self.message}
        for name in ("source", "location", "code"):
            value = getattr(self, name)
            if value is not None:
                result[name] = value
        return result


@dataclass
class Diagnostics:
    """An accumulating collection of findings.

    Deliberately has no `__bool__`. `if diagnostics:` would read as either
    "has findings" or "is ok" depending on the reader, and those are opposites
    — callers must say `.ok` or `len(...)` explicitly.
    """

    items: list[Diagnostic] = field(default_factory=list)

    # -- adding -----------------------------------------------------------

    def add(
        self,
        severity: Severity,
        message: str,
        *,
        source: str | None = None,
        location: str | None = None,
        code: str | None = None,
    ) -> None:
        """Record one finding."""
        self.items.append(
            Diagnostic(severity=severity, message=message, source=source, location=location, code=code)
        )

    def error(self, message: str, **kwargs: Any) -> None:
        """Record an error — the run must fail."""
        self.add(Severity.ERROR, message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        """Record a warning — suspicious, but not fatal."""
        self.add(Severity.WARNING, message, **kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        """Record an informational finding — never a failure."""
        self.add(Severity.INFO, message, **kwargs)

    def extend(self, other: "Diagnostics", *, source: str | None = None) -> None:
        """Merge another bag into this one — the bottom-up push.

        Args:
            other: Findings from a lower layer.
            source: Attributed to any finding that does not already name one,
                so a service can report on a document without knowing which
                file the controller read it from.
        """
        for item in other.items:
            if source is not None and item.source is None:
                self.items.append(
                    Diagnostic(
                        severity=item.severity,
                        message=item.message,
                        source=source,
                        location=item.location,
                        code=item.code,
                    )
                )
            else:
                self.items.append(item)

    # -- querying ---------------------------------------------------------

    @property
    def ok(self) -> bool:
        """True when nothing recorded an error.

        Derived rather than tracked, so "reported success while holding an
        error" is unrepresentable.
        """
        return not self.errors

    @property
    def errors(self) -> list[Diagnostic]:
        """Every error, in discovery order."""
        return self.of(Severity.ERROR)

    @property
    def warnings(self) -> list[Diagnostic]:
        """Every warning, in discovery order."""
        return self.of(Severity.WARNING)

    @property
    def infos(self) -> list[Diagnostic]:
        """Every informational finding, in discovery order."""
        return self.of(Severity.INFO)

    def of(self, severity: Severity) -> list[Diagnostic]:
        """Return every finding of one severity, in discovery order."""
        return [item for item in self.items if item.severity is severity]

    def messages(self, severity: Severity | None = None) -> list[str]:
        """Return rendered strings, optionally filtered by severity.

        The bridge for callers (and tests) that only want text.
        """
        source = self.items if severity is None else self.of(severity)
        return [str(item) for item in source]

    def to_list(self) -> list[dict[str, Any]]:
        """Return a JSON-serialisable list of every finding."""
        return [item.to_dict() for item in self.items]

    def __len__(self) -> int:
        """Total number of findings, regardless of severity."""
        return len(self.items)

    def __iter__(self) -> Iterator[Diagnostic]:
        """Iterate findings in discovery order."""
        return iter(self.items)
