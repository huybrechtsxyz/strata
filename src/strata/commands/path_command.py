#!/usr/bin/env python3
"""`strata path get`/`strata path list` — resolve a document's `(kind, name)`
identity to the real file strata loaded it from, or enumerate every document
of one kind. See docs/work/version-lifecycle.md's "`strata path` — a
dedicated group, decided" section for the design.

Not version-specific, despite being designed alongside `kind: version`'s own
tooling — useful to any external system that needs to locate a
strata-managed file without reimplementing discovery (`.strata/` exclusion,
remotes, path conventions — all real, easy to get subtly wrong outside
strata).

Phase 1 (schema) only, same reasoning as `graph_command`: a path lookup, or
an enumeration, is meaningful even when some unrelated document elsewhere
has a Phase 2 problem — `open_solution()` here, never `.require_valid()`.
"""

from dataclasses import asdict
from pathlib import Path

import click

from strata.commands.json_output import JsonReporter
from strata.commands.options import output_option, quiet_option, resolve_work_path, verbose_option
from strata.commands.run import command_run
from strata.controllers.path_controller import PathResult, get_path, list_paths
from strata.controllers.solution_context import open_solution
from strata.models.common_models import PlatformKind
from strata.utils.diagnostics import Diagnostics

_KIND_CHOICES = [kind.value for kind in PlatformKind]
_PATH_HELP = "Where to start looking for the solution. Defaults to the current directory."


@click.group("path")
def path_command() -> None:
    """Resolve a document's (kind, name) to its real file path."""


@path_command.command("get")
@click.argument("kind", type=click.Choice(_KIND_CHOICES))
@click.argument("name")
@click.option("--path", "start_path", type=click.Path(file_okay=False, path_type=Path), default=None, help=_PATH_HELP)
@output_option
@quiet_option
@verbose_option
def path_get(kind: str, name: str, start_path: Path | None, output: str, quiet: bool, verbose: bool) -> None:
    """Print the real file path of the KIND document named NAME.

    The resolved path is its own line in the console report; add --quiet
    for a fully bare, script-friendly line, or --output json for the
    structured {kind, name, path, exists} shape. Fails rather than
    printing nothing silently when no such document is indexed.

    \b
    Exit codes:
      0  found
      2  bad arguments, or not inside a solution
      3  no KIND document named NAME is indexed
      1  system failure
    """
    with command_run("path get", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(start_path))
        solution = context.controller.solution
        run.describe(solution=solution.meta.name if solution else "(unnamed)", root=context.root, kind=kind, name=name)

        result = get_path(context.controller.index, PlatformKind(kind), name)

        diagnostics = Diagnostics()
        if result.exists:
            if output == "console":
                click.echo(result.path)
        else:
            diagnostics.error(f"No {kind} document named '{name}' is indexed.", code="path_not_found")

        run.report(diagnostics, root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = asdict(result)
        run.ok = result.exists


@path_command.command("list")
@click.argument("kind", type=click.Choice(_KIND_CHOICES))
@click.option(
    "--workspace",
    default=None,
    help="Restrict to documents whose spec.workspace matches. Only meaningful for kinds "
    "that declare that field (today: deployment, version) — any other kind returns "
    "nothing rather than erroring.",
)
@click.option("--path", "start_path", type=click.Path(file_okay=False, path_type=Path), default=None, help=_PATH_HELP)
@output_option
@quiet_option
@verbose_option
def path_list(
    kind: str, workspace: str | None, start_path: Path | None, output: str, quiet: bool, verbose: bool
) -> None:
    """List every KIND document's name and real file path.

    \b
    Exit codes:
      0  always — an empty result is not a failure
      2  bad arguments, or not inside a solution
      1  system failure
    """
    with command_run("path list", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(start_path))
        solution = context.controller.solution
        run.describe(
            solution=solution.meta.name if solution else "(unnamed)", root=context.root, kind=kind, workspace=workspace
        )

        results = list_paths(context.controller.index, PlatformKind(kind), workspace=workspace)
        run.step(f"found {len(results)} {kind} document(s)")

        if output == "console":
            _print_list(results)

        run.report(Diagnostics(), root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {"kind": kind, "workspace": workspace, "results": [asdict(r) for r in results]}
        run.ok = True


def _print_list(results: list[PathResult]) -> None:
    """Render `path list` rows: name, then its real file path."""
    if not results:
        click.echo("  (no documents found)")
        return

    name_w = max(len(r.name) for r in results)
    for r in results:
        click.echo(f"  {r.name.ljust(name_w)}  {r.path}")
