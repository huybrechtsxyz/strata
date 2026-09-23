#!/usr/bin/env python3
"""Options shared by every command.

Declared once so `--output` means the same thing everywhere. v1 had the same
idea (`cli_common.py`'s `click_output_format`, `click_output_quiet`, ...) and
it was the right one — inconsistent flags across a 36-command surface are
what force people back to the docs for something they already know.
"""

from collections.abc import Callable
from typing import Any, TextIO

import click

from strata.commands.json_output import JsonReporter
from strata.commands.output import ConsoleReporter, Reporter

#: Supported renderings. v1 also offered `text` (console minus colour, which
#: NO_COLOR and TTY detection already handle) and `ndjson` (streaming, which
#: only makes sense once a command has something slow to stream).
OUTPUT_FORMATS = ("console", "json")

#: The default rendering.
DEFAULT_OUTPUT = "console"


def output_option(func: Callable[..., Any]) -> Callable[..., Any]:
    """Add `--output/-o`, choosing how results are rendered."""
    return click.option(
        "--output",
        "-o",
        type=click.Choice(OUTPUT_FORMATS),
        default=DEFAULT_OUTPUT,
        show_default=True,
        help="Output format. 'json' writes exactly one document to stdout, on success and on failure.",
    )(func)


def quiet_option(func: Callable[..., Any]) -> Callable[..., Any]:
    """Add `--quiet/-q`, suppressing decoration but never problems."""
    return click.option(
        "--quiet",
        "-q",
        is_flag=True,
        default=False,
        help="Suppress the header, progress and the passing summary. Findings are still reported.",
    )(func)


def verbose_option(func: Callable[..., Any]) -> Callable[..., Any]:
    """Add `--verbose/-v`, raising the log level.

    Affects logging only, which goes to stderr — so it can never make JSON
    output unparseable.
    """
    return click.option(
        "--verbose",
        "-v",
        is_flag=True,
        default=False,
        help="Log at INFO instead of WARNING. Logs are written to stderr.",
    )(func)


def make_reporter(
    command: str,
    output: str,
    *,
    quiet: bool = False,
    stream: TextIO | None = None,
) -> Reporter:
    """Return the renderer for `output`.

    Chosen once per run so command bodies never branch on format.

    Args:
        command: Command name, shown in the header and the JSON envelope.
        output: One of `OUTPUT_FORMATS`.
        quiet: Console only — JSON has no decoration to suppress.
        stream: Where to write. Defaults to stdout.

    Returns:
        A `Reporter`.
    """
    if output == "json":
        return JsonReporter(command, stream=stream)
    return ConsoleReporter(command, stream=stream, quiet=quiet)
