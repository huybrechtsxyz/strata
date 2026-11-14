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
from strata.controllers.artifact_usage import unreferenced_artifacts
from strata.controllers.deployment_resolution import resolve_deployment_chains
from strata.controllers.document_reachability import cleanup_candidates
from strata.controllers.graph_controller import GraphEdge, build_reference_graph
from strata.controllers.solution_context import open_solution
from strata.controllers.solution_controller import IndexEntry
from strata.models.common_models import PlatformKind
from strata.utils.layout import display_path


@click.command("graph")
@click.argument(
    "path",
    type=click.Path(file_okay=False, path_type=Path),
    required=False,
)
@click.option(
    "--unreferenced",
    is_flag=True,
    default=False,
    help="List the cleanup candidates instead: documents no deployment reaches, partial bases nothing extends, "
    "version documents no deployment lists, and artifacts no document names (what 'strata validate' reports as "
    "document_unreferenced, base_unused, version_document_unlisted/promotion_orphan_ring and artifact_unused).",
)
@output_option
@quiet_option
@verbose_option
def graph_command(path: Path | None, unreferenced: bool, output: str, quiet: bool, verbose: bool) -> None:
    """Show every cross-document reference in the solution.

    Walks up from PATH (default: the current directory) to find the solution
    root, then lists every reference each document declares — which document
    it points at, and whether that target actually exists.

    With --unreferenced, lists the documents nothing uses instead, for a
    cleanup pass: those no deployable deployment reaches through a reference
    or a ${value:} token, partial bases nothing extends, version documents no
    deployment lists, and artifacts no document names (a staged artifact,
    named but not deployed yet, is left out).

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

        if unreferenced:
            # Reachability follows deployments through `extends`, so their chains are resolved; nothing else of
            # the validation pass is needed, so a problem elsewhere does not hide the list.
            resolved, _ = resolve_deployment_chains(context.controller.index)
            candidates = cleanup_candidates(context.controller.index, resolved)
            # Artifacts nothing names (`artifact_unused` as a warning): their usage is read through the deployments'
            # effective images, which needs a solution that loaded cleanly. A staged artifact is left out.
            for name in unreferenced_artifacts(context) if context.ok else []:
                artifact = context.controller.index.get(PlatformKind.ARTIFACT, name)
                if artifact is not None:
                    candidates.append((artifact, "artifact_unreferenced"))
            candidates.sort(key=lambda item: (item[0].ref.kind.value, item[0].ref.name))
            run.step(f"found {len(candidates)} unreferenced document(s) across {document_count} document(s)")
            if output == "console":
                _print_candidates(candidates, context.root)
            run.report(context.diagnostics, root=context.root, document_count=document_count)
            if isinstance(run.reporter, JsonReporter):
                run.reporter.data = {
                    "unreferenced": [
                        {
                            "kind": entry.ref.kind.value,
                            "name": entry.ref.name,
                            "path": str(entry.source),
                            "reason": reason,
                        }
                        for entry, reason in candidates
                    ]
                }
            run.ok = True
            return

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


_REASONS = {
    "unreferenced": "reached by no deployment",
    "base_unused": "partial base nothing extends",
    "version_unlisted": "listed by no deployment (fine if CI uses it through --pin)",
    "artifact_unreferenced": "named by no document",
}


def _print_candidates(candidates: list[tuple[IndexEntry, str]], root: Path) -> None:
    """One line per cleanup candidate: kind/name, why, and the file."""
    if not candidates:
        click.echo("  (every document is used)")
        return
    for entry, reason in candidates:
        click.echo(
            f"  {entry.ref.kind.value}/{entry.ref.name}  {_REASONS[reason]}  ({display_path(str(entry.source), root)})"
        )


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
