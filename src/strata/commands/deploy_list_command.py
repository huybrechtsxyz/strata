#!/usr/bin/env python3
"""`strata deploy list` - every deployment, as `build run` and `deploy run` would resolve it.

docs/work/vscode-integrations-policies-views.md V1: `strata path list deployment` lists the files,
but cannot mark a partial base or show a workspace inherited through `extends`. Phase 1 (schema)
only, like `path` and `graph`: listing is useful even when another document has a problem. Unlike
every other `deploy` subcommand it runs nothing and does not call `require_valid()`.
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


@click.command("list")
@click.option(
    "--path",
    "start_path",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Where to start looking for the solution. Defaults to the current directory.",
)
@click.option("--workspace", default=None, metavar="NAME", help="Only deployments running this workspace.")
@click.option("--tenant", default=None, metavar="NAME", help="Only deployments for this tenant.")
@click.option(
    "--version",
    "version_name",
    default=None,
    metavar="NAME",
    help="Only deployments that use this kind: version document.",
)
@click.option(
    "--ring",
    default=None,
    metavar="RING",
    help="Only deployments whose version document is tagged with this ring.",
)
@click.option(
    "--deployable",
    is_flag=True,
    default=False,
    help="Leave out partial bases, which cannot be built or deployed.",
)
@output_option
@quiet_option
@verbose_option
def deploy_list_command(
    start_path: Path | None,
    workspace: str | None,
    tenant: str | None,
    version_name: str | None,
    ring: str | None,
    deployable: bool,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """List every deployment with what it resolves to.

    Shows the workspace, tenant, version document, ring and environments, each as resolved
    through `extends`. Partial bases (spec.partial: true) are listed and marked unless
    --deployable is given; they cannot be built or deployed themselves.

    The filters combine with AND. One that names a workspace, tenant, version or ring that does
    not exist is a usage error rather than an empty list, so a typo cannot produce an empty
    CI matrix. With -o json, `data.deployments[]` carries name, workspace, extends, tenant,
    version, ring, order, wave, environments, partial and source.

    \b
    Exit codes:
      0  listed (possibly nothing matched a valid filter)
      2  bad arguments, a filter naming nothing, or not inside a solution
      1  system failure
    """
    with command_run("deploy list", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(start_path))
        solution = context.controller.solution
        run.describe(solution=solution.meta.name if solution else "(unnamed)", root=context.root)
        rows = list_deployments(
            context.controller.index,
            workspace=workspace,
            tenant=tenant,
            version=version_name,
            ring=ring,
            deployable=deployable,
        )
        run.step(f"{len(rows)} deployment(s)")
        if output == "console":
            filtered = any((workspace, tenant, version_name, ring, deployable))
            _print_rows(rows, filtered=filtered)
        run.report(Diagnostics(), root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {"deployments": [asdict(row) for row in rows]}


def _print_rows(rows: list[DeploymentSummary], *, filtered: bool) -> None:
    if not rows:
        click.echo("  (no deployments match)" if filtered else "  (no deployments)")
        return
    headers = ("NAME", "WORKSPACE", "TENANT", "VERSION", "RING", "ENVIRONMENTS")
    table = [
        (
            row.name + (" (partial)" if row.partial else ""),
            row.workspace or "-",
            row.tenant or "-",
            row.version or "-",
            row.ring or "-",
            ", ".join(row.environments) or "-",
        )
        for row in rows
    ]
    widths = [max(len(header), *(len(line[i]) for line in table)) for i, header in enumerate(headers[:-1])]
    for line in [headers, *table]:
        cells = [cell.ljust(width) for cell, width in zip(line[:-1], widths, strict=True)]
        click.echo("  " + "  ".join([*cells, line[-1]]))
