#!/usr/bin/env python3
"""`strata graph` — show every cross-document reference in the solution.

Not a validator. `validate` already reports an unresolved reference as an
error; this command's job is the opposite one: show the *shape* of a
solution, including references that do not resolve, so a human (or a VS Code
tree view) can see what points at what. Finding a dangling reference here is
informational, never a failure — asking "why doesn't this reference
resolve?" is exactly when you want the command not to have already exited
non-zero on you.

No `-f FILE`, same reasoning as `validate_command`: v2 discovers the whole
solution from `strata.yaml`, so there is no single file to ask about.
"""

from dataclasses import asdict
from pathlib import Path

import click

from strata.commands.json_output import JsonReporter
from strata.commands.options import output_option, quiet_option, resolve_work_path, verbose_option
from strata.commands.run import command_run
from strata.controllers.graph_controller import GraphEdge, build_reference_graph
from strata.controllers.solution_context import open_solution
from strata.utils.layout import display_path


@click.command("graph")
@click.argument(
    "path",
    type=click.Path(file_okay=False, path_type=Path),
    required=False,
)
@output_option
@quiet_option
@verbose_option
def graph_command(path: Path | None, output: str, quiet: bool, verbose: bool) -> None:
    """Show every cross-document reference in the solution.

    Walks up from PATH (default: the current directory) to find the solution
    root, then lists every reference each document declares — which document
    it points at, and whether that target actually exists.

    \b
    Exit codes:
      0  always — an unresolved reference is shown, not failed on; run
         `strata validate` for pass/fail
      2  not inside a solution, or bad arguments
      1  system failure
    """
    with command_run("graph", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(path))
        solution = context.controller.solution
        document_count = len(context.controller.index)

        run.describe(
            solution=solution.meta.name if solution else "(unnamed)",
            root=context.root,
        )

        # Phase 1 (schema) only — a graph of *what resolves to what* needs no
        # cross-document resolution pass of its own; `resolved` below is
        # computed directly against the index.
        edges = build_reference_graph(context.controller.index)
        run.step(f"found {len(edges)} reference(s) across {document_count} document(s)")

        if output == "console":
            _print_graph(edges, context.root)

        run.report(context.diagnostics, root=context.root, document_count=document_count)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {"edges": [asdict(edge) for edge in edges]}
        run.ok = True


def _print_graph(edges: list[GraphEdge], root: Path) -> None:
    """Render edges grouped by the document that declared them.

    Mirrors `format_console()`'s own grouping convention (one header per
    source, findings indented beneath it) rather than inventing a second
    layout for the same kind of report.
    """
    if not edges:
        click.echo("  (no cross-document references found)")
        return

    grouped: dict[tuple[str, str, str], list[GraphEdge]] = {}
    for edge in edges:
        grouped.setdefault((edge.source_kind, edge.source_name, edge.source_path), []).append(edge)

    for (source_kind, source_name, source_path), group in grouped.items():
        click.echo(
            click.style(f"{source_kind}/{source_name}", fg="bright_white") + f"  ({display_path(source_path, root)})"
        )
        for edge in group:
            marker = " " if edge.resolved else click.style("!", fg="red")
            click.echo(f"  {marker} {edge.location} -> {edge.target_kind}/{edge.target_name}")
