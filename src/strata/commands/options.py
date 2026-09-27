#!/usr/bin/env python3
"""Options shared by every command.

Declared once so `--output` means the same thing everywhere. v1 had the same
idea (`cli_common.py`'s `click_output_format`, `click_output_quiet`, ...) and
it was the right one — inconsistent flags across a 36-command surface are
what force people back to the docs for something they already know.

`STRATA_*` env-var defaults are grouped here too, each behind its own named
function (`resolve_work_path()` alongside `output_option()`'s `envvar=`) —
mirrors v1's own real pattern (`cli.py`'s `_resolve_work_path_early()`,
`_load_workspace_defaults()`): resolved once, at the command layer, then
passed down as a plain value. Deeper layers (`solution_context.py`,
`value_controller.py`, ...) never reach into `os.environ` themselves —
keeps every env-var read in one auditable place instead of a bug surfacing
later because some unrelated function quietly started reading the process
environment.
"""

from collections.abc import Callable
from os import environ
from pathlib import Path
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
    """Add `--output/-o`, choosing how results are rendered.

    Falls back to the `STRATA_OUTPUT` env var when the flag is omitted
    (Click's own `envvar` support) — every real CI workflow in both
    reference repos sets `STRATA_OUTPUT: json` once, in the job's `env:`
    block, rather than passing `--output json` on every single invocation
    (docs/decisions/0020-v1-consumer-feature-priority.md's Tier 1 finding).
    An explicit `--output`/`-o` on the command line still wins over the
    env var, matching Click's own documented precedence.
    """
    return click.option(
        "--output",
        "-o",
        envvar="STRATA_OUTPUT",
        type=click.Choice(OUTPUT_FORMATS),
        default=DEFAULT_OUTPUT,
        show_default=True,
        help="Output format. 'json' writes exactly one document to stdout, on success and on failure. "
        "Also settable via the STRATA_OUTPUT env var.",
    )(func)


def resolve_work_path(explicit: Path | None) -> Path:
    """The one place "where do we start" gets decided, for every command.

    Mirrors v1's own real precedence (`cli.py`'s comment: *"explicit flag >
    STRATA_* env var > cli.yaml > built-in default"*) folded into a single
    function, always returning a concrete `Path` — never `None` — so
    `open_solution()` and everything below it never has to know whether a
    path came from `--path`, `STRATA_WORK_PATH`, or neither; they just get
    told where to start.

    Args:
        explicit: The CLI-supplied path, or `None` if omitted.

    Returns:
        `explicit` when given; else `Path(STRATA_WORK_PATH)` when that env
        var is set; else the current directory.
    """
    if explicit is not None:
        return explicit
    work_path = environ.get("STRATA_WORK_PATH")
    return Path(work_path) if work_path else Path.cwd()


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
