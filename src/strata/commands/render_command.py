#!/usr/bin/env python3
"""`strata render KIND NAME` - a resolved deployment, environment or tenant, and where each value comes from.

`base-parameters-v2.md` (work doc), phase 4. A document built with `extends` and parameter sets is spread over
several files; this prints the one strata uses, field by field, with the document that wrote each value and, for a
value filled in from a parameter set, the text as written and the set and key it read. Like `deploy list`, it runs
nothing and does not call `require_valid()`: rendering is how you find out why a document is what it is. Secrets
are always masked.
"""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import click

from strata.commands.json_output import JsonReporter
from strata.commands.options import output_option, quiet_option, resolve_work_path, verbose_option
from strata.commands.run import command_run
from strata.controllers.rendered_view import RENDERABLE_KINDS, FieldOrigin, RenderedDocument, render_document
from strata.controllers.solution_context import open_solution
from strata.models.common_models import PlatformKind


@click.command("render")
@click.argument("kind", type=click.Choice([kind.value for kind in RENDERABLE_KINDS]))
@click.argument("name")
@click.option(
    "--path",
    "start_path",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Where to start looking for the solution. Defaults to the current directory.",
)
@output_option
@quiet_option
@verbose_option
def render_command(kind: str, name: str, start_path: Path | None, output: str, quiet: bool, verbose: bool) -> None:
    """Show the resolved KIND document NAME and where each of its values comes from.

    KIND is deployment, environment or tenant. The document is shown as strata uses it: its
    extends chain merged, its parameter sets filled in, and for a deployment its tenant's
    defaults folded in. Each value names the document that wrote it; a value filled in from a
    parameter set also shows the text as written and the role, key and parameter set it read.
    Secret values are always masked.

    With -o json, `data` carries kind, name, source, chain, param_sets, params, spec and
    fields[] (path, value, document_kind, document, source, written, reads[]).

    \b
    Exit codes:
      0  rendered
      2  bad arguments, or not inside a solution
      3  no such document, a partial base, or a document that does not resolve
      1  system failure
    """
    with command_run("render", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(start_path))
        solution = context.controller.solution
        run.describe(solution=solution.meta.name if solution else "(unnamed)", root=context.root, kind=kind, name=name)
        rendered, diagnostics = render_document(context.controller.index, PlatformKind(kind), name)
        if rendered is not None:
            run.step(f"{len(rendered.fields)} field(s) from {len(rendered.chain)} document(s)")
        if rendered is not None and output == "console":
            _print(rendered)
        run.report(diagnostics, root=context.root)
        if isinstance(run.reporter, JsonReporter) and rendered is not None:
            run.reporter.data = asdict(rendered)
        run.ok = rendered is not None


def _value(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


def _detail(origin: FieldOrigin) -> str:
    if origin.written is None or not origin.reads:
        return ""  # nothing filled in, or a secret: its text and reads are never shown
    reads = ", ".join(
        f"{read.role}{'.' + read.key if read.key else ''} = {_value(read.value)} ({read.parameter_set})"
        for read in origin.reads
    )
    return f"{origin.written!r}: {reads}"


def _print(rendered: RenderedDocument) -> None:
    click.echo(f"  {rendered.kind} {rendered.name}")
    click.echo(f"  chain: {' <- '.join(rendered.chain)}")
    if rendered.param_sets:
        sets = "; ".join(f"{role}: {', '.join(names)}" for role, names in rendered.param_sets.items())
        click.echo(f"  parameter sets: {sets}")
    click.echo("")
    rows = [
        (
            origin.path,
            _value(origin.value),
            (origin.document if origin.document_kind == rendered.kind else f"{origin.document_kind}/{origin.document}")
            if origin.document
            else "(default)",
            _detail(origin),
        )
        for origin in rendered.fields
    ]
    if not rows:
        click.echo("  (empty spec)")
        return
    headers = ("FIELD", "VALUE", "FROM", "FILLED IN FROM")
    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(3)]
    for row in [headers, *rows]:
        cells = [cell.ljust(width) for cell, width in zip(row[:3], widths, strict=True)]
        click.echo(("  " + "  ".join([*cells, row[3]])).rstrip())
