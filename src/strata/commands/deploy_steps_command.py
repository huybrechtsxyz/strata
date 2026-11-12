#!/usr/bin/env python3
"""`strata deploy steps DEPLOYMENT` - the execution steps `deploy run` would run, for a pipeline to plan its jobs
(docs/design/deploy-steps-command.md).

Reads the solution only, unless `--check-tools` asks this machine for each listed step's tool. Selection is
`select_steps()`, the function every deploy command uses, so what is listed is what runs.
"""

from pathlib import Path

import click

from strata.commands.json_output import JsonReporter
from strata.commands.options import output_option, quiet_option, resolve_work_path, verbose_option
from strata.commands.run import command_run
from strata.controllers.deployment_steps import DeploymentSteps, StepRow, deployment_steps
from strata.controllers.solution_context import open_solution


@click.command("steps")
@click.argument("deployment")
@click.option(
    "--stage",
    "stages",
    multiple=True,
    metavar="NAME",
    help="Only the step named NAME. Repeatable: a step named by any of them is kept.",
)
@click.option(
    "--scope",
    "scopes",
    multiple=True,
    metavar="LABEL",
    help="Only steps whose scope: label is LABEL. Repeatable: a step with any of them is kept. "
    "With --stage too, a step must match both.",
)
@click.option(
    "--destroy",
    is_flag=True,
    default=False,
    help="List in teardown order, as 'deploy destroy' runs the steps.",
)
@click.option(
    "--check-tools",
    is_flag=True,
    default=False,
    help="Check each listed step's tool on this machine (on PATH, required version), as 'deploy run' does "
    "before it starts. A tool that is not ready exits 3.",
)
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
def deploy_steps_command(
    deployment: str,
    stages: tuple[str, ...],
    scopes: tuple[str, ...],
    destroy: bool,
    check_tools: bool,
    start_path: Path | None,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """List DEPLOYMENT's execution steps in the order 'deploy run' runs them.

    For a pipeline to plan its jobs: each step's scope, order, level (steps on one level do not depend on each
    other), depends_on, tool, whether 'deploy apply' (saved_plan) and 'deploy destroy' can take it, its declared
    and effective targets, and its stage timeouts. --stage and --scope select exactly as on 'deploy run'. An
    empty selection is a result, not an error; values that matched no step are listed as unmatched.

    Order and level are counted over all steps, before --stage/--scope, so they do not change with a filter.
    Nothing runs and nothing remote is contacted; without --check-tools no tool is looked up either.

    With -o json, data carries deployment, workspace, partial, direction, filters, unmatched, tools_checked,
    steps[] (name, scope, order, level, depends_on, provisioner, integration, readiness, capabilities, targets,
    effective, description, timeouts) and scopes[] (scope, steps, depends_on, orderable).

    \b
    Exit codes:
      0  listed (also when nothing matched, and with scope-cycle warnings)
      2  bad arguments, an unknown DEPLOYMENT or one without a workspace, or not inside a solution
      3  the solution is invalid, or --check-tools found a tool that is not ready
      1  system failure
    """
    with command_run("deploy steps", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(start_path)).require_valid()
        solution = context.controller.solution
        run.describe(solution=solution.meta.name if solution else "(unnamed)", root=context.root, deployment=deployment)

        result = deployment_steps(
            context, deployment, stages=stages, scopes=scopes, destroy=destroy, check_tools=check_tools
        )
        run.step(f"{len(result.steps)} step(s) in {len(result.scopes)} scope(s)")

        if output == "console":
            _print(result, verbose=verbose)

        run.report(result.diagnostics, root=context.root)
        run.ok = result.diagnostics.ok
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = result.to_data()


def _scope_label(scope: str | None) -> str:
    return scope if scope is not None else "(no scope)"


def _plan_apply(row: StepRow) -> str:
    if row.capabilities.container:
        return "container"
    return "yes" if row.capabilities.saved_plan else "no"


def _tool_ready(row: StepRow) -> str:
    if row.readiness is None:
        return "-"
    if row.readiness.problems:
        return row.readiness.problems[0].code
    return f"ok {row.readiness.installed_version}" if row.readiness.installed_version else "ok"


def _print(result: DeploymentSteps, *, verbose: bool) -> None:
    click.echo(f"{result.deployment}  workspace {result.workspace}, {result.direction} order")
    click.echo()
    if not result.steps:
        click.echo("  (no steps match)")
    else:
        headers = ["ORDER", "LEVEL", "STEP", "SCOPE", "TOOL", "DEPENDS ON", "PLAN/APPLY", "DESTROY", "TOPOLOGIES"]
        if result.tools_checked:
            headers.append("TOOL READY")
        table: list[list[str]] = []
        for row in result.steps:
            line = [
                str(row.order),
                str(row.level),
                row.name,
                row.scope or "-",
                row.integration.type,
                ", ".join(row.depends_on) or "-",
                _plan_apply(row),
                "yes" if row.capabilities.destroy else "no",
                ", ".join(row.targets["topologies"]) or "-",
            ]
            if result.tools_checked:
                line.append(_tool_ready(row))
            table.append(line)
        widths = [max(len(headers[i]), *(len(line[i]) for line in table)) for i in range(len(headers) - 1)]
        click.echo(_format(headers, widths))
        for row, line in zip(result.steps, table, strict=True):
            click.echo(_format(line, widths))
            if verbose:
                for kind in ("resources", "namespaces"):
                    if row.effective[kind]:
                        click.echo(f"      {kind}: {', '.join(row.effective[kind])}")

    if result.scopes:
        labels = [_scope_label(scope.scope) + ("" if scope.orderable else " (cycle)") for scope in result.scopes]
        click.echo()
        click.echo("  Scopes: " + " -> ".join(labels))
    for kind, values in (("stage", result.unmatched_stages), ("scope", result.unmatched_scopes)):
        if values:
            click.echo(f"  No step matches --{kind} {', '.join(values)}")


def _format(cells: list[str], widths: list[int]) -> str:
    padded = [cell.ljust(width) for cell, width in zip(cells[:-1], widths, strict=True)]
    return ("  " + "  ".join([*padded, cells[-1]])).rstrip()
