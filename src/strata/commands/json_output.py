#!/usr/bin/env python3
"""Machine-readable output.

A command run produces exactly **one** JSON document on stdout — on success,
on validation failure, on usage error, on crash. The real pipelines depend on
this: they capture stdout, branch on the exit code, and then pipe the *same*
captured text to `jq` for detail. v1 did not always manage it, which is why
haven's workflows carry `|| echo "(no parseable error detail)"` as a fallback.
That fallback is a defect to remove, not a pattern to copy.

Consequences of that rule:

- **stdout is JSON and nothing else.** Header, progress and log lines go to
  stderr, so `JsonReporter` simply does not print them rather than "styling
  them off".
- **The exit code stays the signal.** `ok` exists for when the document is
  stored or forwarded and the exit status is long gone.

Framing is separated from content — `build_envelope()` returns data,
`JsonReporter` decides how to write it. A future `--output ndjson` for
streaming commands (v1 had one for deploy progress) writes per-event lines
followed by this same envelope as the final line, without changing anything
here.
"""

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TextIO

from strata.utils.diagnostics import Diagnostics, Severity
from strata.utils.layout import display_path


def build_envelope(
    *,
    command: str,
    ok: bool,
    diagnostics: Diagnostics,
    root: Path | None = None,
    context: Mapping[str, str] | None = None,
    document_count: int | None = None,
    data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the result document.

    Args:
        command: The command name, e.g. `validate`.
        ok: Whether the run succeeded.
        diagnostics: Findings to report.
        root: Solution root, used to shorten diagnostic sources.
        context: The same facts the console header shows, passed through
            verbatim so the two renderings cannot describe different runs.
        document_count: How many documents were examined, if known.
        data: Command-specific payload. `values get` fills this; `validate`
            has none.

    Returns:
        A JSON-serialisable mapping.
    """
    from strata.utils.version import get_version

    summary: dict[str, Any] = {
        # Stable keys, always present: a consumer should not have to test for
        # a field's existence just because a count happened to be zero.
        "errors": len(diagnostics.of(Severity.ERROR)),
        "warnings": len(diagnostics.of(Severity.WARNING)),
        "info": len(diagnostics.of(Severity.INFO)),
    }
    if document_count is not None:
        summary["documents"] = document_count

    findings = []
    for item in diagnostics:
        entry = item.to_dict()
        if item.source is not None:
            entry["source"] = display_path(item.source, root)
        findings.append(entry)

    return {
        "ok": ok,
        "command": command,
        "version": get_version(),
        "context": dict(context or {}),
        "summary": summary,
        "diagnostics": findings,
        "data": dict(data or {}),
    }


def format_json(envelope: Mapping[str, Any], *, indent: int | None = 2) -> str:
    """Render an envelope as text.

    Args:
        envelope: The document to render.
        indent: Pretty-print width, or None for a single line — which is what
            a future NDJSON writer needs.
    """
    return json.dumps(envelope, indent=indent, ensure_ascii=False)


class JsonReporter:
    """Collects a run and emits it as one JSON document.

    Mirrors `ConsoleReporter`'s interface so a command can hold either without
    branching on the output format. The difference is only *when* output
    happens: the console streams as it goes, this buffers and emits once at
    the end, because a half-written JSON document is worse than none.
    """

    def __init__(
        self,
        command: str,
        *,
        stream: TextIO | None = None,
        indent: int | None = 2,
    ) -> None:
        self.command = command
        self.stream = stream
        self.indent = indent
        self._context: dict[str, str] = {}
        self._diagnostics = Diagnostics()
        self._root: Path | None = None
        self._document_count: int | None = None
        self.data: dict[str, Any] = {}

    def header(self, context: Mapping[str, str] | None = None) -> None:
        """Record run context. Prints nothing — stdout must stay parseable."""
        self._context = dict(context or {})

    def step(self, message: str) -> None:
        """Progress is not part of a single-document result.

        Deliberately a no-op rather than buffered: nothing would consume it,
        and per-step reporting is what `--output ndjson` will be for.
        """

    def diagnostics(
        self,
        diagnostics: Diagnostics,
        *,
        root: Path | None = None,
        document_count: int | None = None,
    ) -> str:
        """Record findings for the final document.

        Returns an empty string: the console's summary line has no meaning
        here, and the signature stays compatible with `ConsoleReporter`.
        """
        self._diagnostics = diagnostics
        self._root = root
        self._document_count = document_count
        return ""

    def footer(self, ok: bool, summary: str = "") -> None:
        """Emit the one and only document."""
        envelope = build_envelope(
            command=self.command,
            ok=ok,
            diagnostics=self._diagnostics,
            root=self._root,
            context=self._context,
            document_count=self._document_count,
            data=self.data,
        )
        print(format_json(envelope, indent=self.indent), file=self.stream or sys.stdout)
