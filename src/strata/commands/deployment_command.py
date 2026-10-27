#!/usr/bin/env python3
"""`strata deployment list` - every deployment, as `build run` and `deploy run` would resolve it.

docs/work/vscode-integrations-policies-views.md V1: `strata path list deployment` lists the files,
but cannot mark a partial base or show a workspace inherited through `extends`. Phase 1 (schema)
only, like `path` and `graph`: listing is useful even when another document has a problem.
"""

from dataclasses import asdict
from pathlib import Path

import click

from strata.commands.json_output import JsonReporter
from strata.commands.options import output_option, quiet_option, resolve_work_path, verbose_option
from strata.commands.run import command_run
from strata.controllers.deployment_resolution import DeploymentSummary, list_deployments
from strata.controllers.solution_context import open_solution
from strata.utils.diagnostics import Diagnostics


@click.group("deployment")
def deployment_command() -> None:
    """List the solution's deployments."""


@deployment_command.command("list")
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
def list_command(start_path: Path | None, output: str, quiet: bool, verbose: bool) -> None:
    """List every deployment with the workspace and environments it resolves to.

    Partial bases (spec.partial: true) are listed and marked; they cannot be
    built or deployed themselves.

    \b
    Exit codes:
      0  listed
      2  bad arguments, or not inside a solution
      1  system failure
    """
    with command_run("deployment list", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(start_path))
        solution = context.controller.solution
        run.describe(solution=solution.meta.name if solution else "(unnamed)", root=context.root)
        rows = list_deployments(context.controller.index)
        run.step(f"{len(rows)} deployment(s)")
        if output == "console":
            _print_rows(rows)
        run.report(Diagnostics(), root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {"deployments": [asdict(row) for row in rows]}


def _print_rows(rows: list[DeploymentSummary]) -> None:
    if not rows:
        click.echo("  (no deployments)")
        return
    table = [
        (
            row.name + (" (partial)" if row.partial else ""),
            row.workspace or "-",
            ", ".join(row.environments) or "-",
        )
        for row in rows
    ]
    headers = ("NAME", "WORKSPACE", "ENVIRONMENTS")
    widths = [max(len(headers[i]), *(len(line[i]) for line in table)) for i in range(2)]
    for line in [headers, *table]:
        click.echo(f"  {line[0].ljust(widths[0])}  {line[1].ljust(widths[1])}  {line[2]}")
