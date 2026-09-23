#!/usr/bin/env python3
"""Rendering diagnostics and run reports for a terminal.

Presentation only — it decides nothing. What counts as a failure lives in
`Diagnostics.ok`, and what a process returns lives in
`strata.commands.exit_codes`. This module turns findings into text.

A run reads top to bottom as *what ran → what happened → what was found*:

    header       which command, against which solution, when
    steps        progress while it runs
    diagnostics  grouped by document
    footer       pass/fail, totals, elapsed

v1 showed the same context and was right to — version, timestamp, invocation
and directory are exactly what a bug report needs. What it got wrong was
density: four 80-character rules, a tagline repeated every run, and a
"Thank you for using Strata CLI!" footer carrying no information. Here the
chrome is thin and everything printed is a fact about *this* run.

Three choices worth stating:

**Grouped by document.** A validation run reports on many files, and the
question an author asks is "what is wrong with *this* file". Grouping keeps
every finding for one document together under one header, which is why
`Diagnostics` preserves discovery order rather than sorting by severity.

**Paths relative to the solution root.** Absolute paths dominate the line and
differ between a laptop and CI, which makes output hard to scan and hard to
diff. The root is already known, so it is subtracted.

**Degrades rather than fails.** Colour disappears when piped or when NO_COLOR
is set; box-drawing characters fall back to ASCII when the output encoding
cannot represent them. Redirecting output on a legacy Windows code page must
not raise `UnicodeEncodeError` from decoration.
"""

import os
import shutil
import sys
import time
from collections import OrderedDict
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, TextIO

import click

from strata.utils.diagnostics import Diagnostic, Diagnostics, Severity
from strata.utils.layout import display_path

#: Colour per severity. Click strips styling automatically when the stream is
#: not a terminal, so piped output stays clean without extra handling.
_COLOUR_BY_SEVERITY = {
    Severity.ERROR: "red",
    Severity.WARNING: "yellow",
    Severity.INFO: "cyan",
}

#: Width of the severity column, so messages line up under a header.
_LABEL_WIDTH = max(len(severity.value) for severity in Severity)

#: Decoration, with an ASCII fallback for streams that cannot encode it.
_UNICODE_SYMBOLS = {"rule": "\u2500", "step": "\u2192"}
_ASCII_SYMBOLS = {"rule": "-", "step": ">"}

#: Report width, clamped so it stays readable in both a narrow pane and a
#: maximised window.
_MIN_WIDTH = 60
_MAX_WIDTH = 100


def colour_enabled() -> bool:
    """True unless NO_COLOR is set (see no-color.org).

    Click already suppresses styling for non-terminals; this covers the
    separate case of a user who wants plain text on a terminal.
    """
    return "NO_COLOR" not in os.environ


def symbols_for(stream: TextIO | None = None) -> Mapping[str, str]:
    """Return box-drawing characters, or ASCII when the stream cannot encode them.

    Redirecting to a file on a legacy Windows code page uses the locale
    encoding, where `U+2500` raises. Decoration must never be the reason a
    command fails.
    """
    encoding = getattr(stream or sys.stdout, "encoding", None)
    if not encoding:
        return _ASCII_SYMBOLS
    try:
        "".join(_UNICODE_SYMBOLS.values()).encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return _ASCII_SYMBOLS
    return _UNICODE_SYMBOLS


def report_width() -> int:
    """Return the width to draw rules at, clamped to a readable range.

    One column short of the terminal: a rule that exactly fills the width
    wraps, which eats the blank line after it and runs the next section
    into the rule.
    """
    columns = shutil.get_terminal_size((80, 24)).columns - 1
    return max(_MIN_WIDTH, min(_MAX_WIDTH, columns))


def _styled(text: str, colour: str, *, enabled: bool) -> str:
    """Apply colour, or return the text untouched when disabled."""
    return click.style(text, fg=colour) if enabled else text



def group_by_source(
    diagnostics: Diagnostics, root: Path | None = None
) -> "OrderedDict[str, list[Diagnostic]]":
    """Group findings by their rendered source, preserving discovery order.

    Findings with no source collect under `""`, which the renderer prints
    first without a header — they are about the run, not about a document.
    """
    grouped: OrderedDict[str, list[Diagnostic]] = OrderedDict()
    for item in diagnostics:
        grouped.setdefault(display_path(item.source, root), []).append(item)
    return grouped


def _count(number: int, noun: str) -> str:
    """Render `1 error` / `2 errors`."""
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def format_summary(diagnostics: Diagnostics, document_count: int | None = None) -> str:
    """Return the closing line: what was checked, and what was found."""
    checked = f"{_count(document_count, 'document')} checked" if document_count is not None else "Checked"

    tallies = [
        _count(len(diagnostics.of(severity)), severity.value)
        for severity in (Severity.ERROR, Severity.WARNING, Severity.INFO)
        if diagnostics.of(severity)
    ]
    if not tallies:
        return f"{checked} — no problems found"
    return f"{checked} — {', '.join(tallies)}"


def format_diagnostic(item: Diagnostic, *, colour: bool) -> str:
    """Return one indented finding line, without its source.

    The source is already the group header, so repeating it on every line
    would triple the width for no information.
    """
    label = _styled(
        item.severity.value.ljust(_LABEL_WIDTH),
        _COLOUR_BY_SEVERITY[item.severity],
        enabled=colour,
    )
    parts = [part for part in (item.location, item.message) if part]
    line = f"  {label}  {': '.join(parts)}"
    return f"{line} [{item.code}]" if item.code else line


def format_console(
    diagnostics: Diagnostics,
    *,
    root: Path | None = None,
    document_count: int | None = None,
    colour: bool | None = None,
) -> str:
    """Render a full report: findings grouped by document, then a summary.

    Args:
        diagnostics: What was found.
        root: Solution root, used to shorten paths.
        document_count: How many documents were checked, for the summary.
        colour: Force styling on or off. Defaults to the NO_COLOR convention.

    Returns:
        The report, without a trailing newline. Always includes the summary,
        so a clean run still confirms that something was checked.
    """
    use_colour = colour_enabled() if colour is None else colour
    lines: list[str] = []

    for source, items in group_by_source(diagnostics, root).items():
        if source:
            lines.append(_styled(source, "bright_white", enabled=use_colour))
        lines.extend(format_diagnostic(item, colour=use_colour) for item in items)

    if lines:
        lines.append("")
    lines.append(format_summary(diagnostics, document_count))
    return "\n".join(lines)


class Reporter(Protocol):
    """What a command needs from any output format.

    Both `ConsoleReporter` and `JsonReporter` satisfy this, so a command
    chooses a renderer once and then reports the same way regardless of
    format — no `if output == "json"` scattered through command bodies.

    The sequence is always header -> steps -> diagnostics -> footer. Console
    writes as it goes; JSON buffers and emits once, because a half-written
    document is worse than none.
    """

    def header(self, context: Mapping[str, str] | None = None) -> None:
        """Announce what is running, against what."""

    def step(self, message: str) -> None:
        """Report progress while the command runs."""

    def diagnostics(
        self,
        diagnostics: Diagnostics,
        *,
        root: Path | None = None,
        document_count: int | None = None,
    ) -> str:
        """Report findings; returns the summary line for `footer()`."""

    def footer(self, ok: bool, summary: str = "") -> None:
        """Close the report."""


class ConsoleReporter:
    """Writes a run report: header, progress, findings, footer.

    Stateful only in the ways a report is: when the run started, and how wide
    to draw. Everything else is passed in, so the reporter never has to ask
    the domain anything.

    `quiet` suppresses the chrome and progress but never the findings —
    a quiet run that found problems must still say what they were.
    """

    def __init__(
        self,
        command: str,
        *,
        stream: TextIO | None = None,
        colour: bool | None = None,
        quiet: bool = False,
    ) -> None:
        self.command = command
        self.stream = stream
        self.quiet = quiet
        self.colour = colour_enabled() if colour is None else colour
        self.symbols = symbols_for(stream)
        self.width = report_width()
        self._started = time.monotonic()

    # -- primitives -------------------------------------------------------

    def _echo(self, text: str = "") -> None:
        """Write one line to the configured stream."""
        click.echo(text, file=self.stream)

    def _rule(self, title: str = "", trailing: str = "") -> str:
        """Return a horizontal rule, optionally with a title and right-hand tag."""
        fill = self.symbols["rule"]
        left = f"{fill}{fill} {title} " if title else fill * 2
        right = f" {trailing} {fill}{fill}" if trailing else ""
        padding = max(0, self.width - len(left) - len(right))
        return f"{left}{fill * padding}{right}"

    # -- sections ---------------------------------------------------------

    def header(self, context: Mapping[str, str] | None = None) -> None:
        """Announce what is running, against what.

        `context` is rendered as an aligned key/value block — the facts a bug
        report needs, and nothing that is the same on every run.
        """
        if self.quiet:
            return
        from strata.utils.version import get_version

        self._echo(self._rule(self.command, f"strata {get_version()}"))
        self._echo()

        entries = dict(context or {})
        entries.setdefault("started", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
        pad = max(len(key) for key in entries)
        for key, value in entries.items():
            self._echo(f"  {_styled(key.ljust(pad), 'bright_black', enabled=self.colour)}  {value}")
        self._echo()

    def step(self, message: str) -> None:
        """Report progress while the command runs."""
        if self.quiet:
            return
        marker = _styled(self.symbols["step"], "bright_black", enabled=self.colour)
        self._echo(f"  {marker} {message}")

    def diagnostics(
        self,
        diagnostics: Diagnostics,
        *,
        root: Path | None = None,
        document_count: int | None = None,
    ) -> str:
        """Print the findings, grouped by document, and return the summary line.

        Printed even when quiet: suppressing problems on request would make
        `--quiet` a way to hide failures.

        Returns:
            The summary to hand to `footer()`. Returned rather than stashed on
            the reporter so the two calls are not silently coupled through
            hidden state.
        """
        if len(diagnostics):
            self._echo()
        for source, items in group_by_source(diagnostics, root).items():
            if source:
                self._echo(_styled(source, "bright_white", enabled=self.colour))
            for item in items:
                self._echo(format_diagnostic(item, colour=self.colour))
        return format_summary(diagnostics, document_count)

    def footer(self, ok: bool, summary: str = "") -> None:
        """Close with the verdict, the totals, and how long it took.

        Args:
            ok: Whether the run succeeded.
            summary: Usually the return value of `diagnostics()`.
        """
        if self.quiet and ok:
            return
        status = "PASSED" if ok else "FAILED"
        coloured = _styled(status, "green" if ok else "red", enabled=self.colour)
        elapsed = f"{time.monotonic() - self._started:.2f}s"

        self._echo()
        self._echo(self._rule())
        self._echo(f"  {coloured}  {summary}".rstrip() + f"  ({elapsed})")

