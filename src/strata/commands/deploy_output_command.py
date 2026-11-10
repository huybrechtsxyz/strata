#!/usr/bin/env python3
"""`strata deploy output DEPLOYMENT [STEP | STEP.KEY]` - the `terraform output` equivalent.

docs/design/deployment-documentation.md, section 1. Reads the workspace's `output_store` only: no build directory,
no provisioner, no Terraform state, no secret. `--format raw` prints one bare value for `$(...)` in a script;
`--output json` carries every output, with descriptions, in the envelope's `data` (ADR-0056).
"""

import sys
from dataclasses import asdict
from pathlib import Path

import click

from strata.commands.json_output import JsonReporter
from strata.commands.options import output_option, quiet_option, resolve_work_path, verbose_option
from strata.commands.run import command_run
from strata.controllers.deployment_output_view import (
    SENSITIVE_PLACEHOLDER,
    StepOutputs,
    deployment_outputs,
    status_line,
)
from strata.controllers.solution_context import open_solution
from strata.utils.errors import UsageError


@click.command("output")
@click.argument("deployment")
@click.argument("address", required=False, metavar="[STEP | STEP.KEY]")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(("table", "raw")),
    default="table",
    show_default=True,
    help="table: one table per step. raw: the bare value of one STEP.KEY and nothing else, for $(...) in a "
    "script. Ignored with --output json.",
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
def deploy_output_command(
    deployment: str,
    address: str | None,
    output_format: str,
    start_path: Path | None,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """Show the outputs DEPLOYMENT's steps recorded, like `terraform output`.

    Reads the workspace's output_store, which 'deploy run' writes after each step: no Terraform state and no
    provider credentials are needed, only access to the store. STEP.KEY is the same address as an
    ${output:STEP.KEY} token. A sensitive output shows as <sensitive>: strata never stores its value, so
    --format raw on it fails. A step that has not been deployed with this output store shows "no outputs
    recorded". With -o json, data.steps[] carries step, status, collected_at, report, truncated and
    outputs[] (name, value, sensitive, description).

    \b
    Exit codes:
      0  shown
      2  bad arguments, an unknown DEPLOYMENT, STEP or STEP.KEY, a STEP.KEY in a step with no record,
         or --format raw on a sensitive output
      3  the solution is invalid, or the workspace sets no output_store
      1  system failure (the output store is unreachable, or a record is corrupt)
    """
    raw = output_format == "raw" and output == "console"
    # Under raw, stdout carries the bare value and nothing else; the run's report goes to stderr.
    with command_run(
        "deploy output", output=output, quiet=quiet, verbose=verbose, stream=sys.stderr if raw else None
    ) as run:
        if raw and (address is None or "." not in address):
            raise UsageError("--format raw needs one STEP.KEY.")

        context = open_solution(resolve_work_path(start_path)).require_valid()
        solution = context.controller.solution
        run.describe(solution=solution.meta.name if solution else "(unnamed)", root=context.root, deployment=deployment)

        result = deployment_outputs(context, deployment, address)
        if raw:
            if len(result.steps) != 1 or len(result.steps[0].outputs) != 1:
                raise UsageError("--format raw needs one STEP.KEY.")
            row = result.steps[0].outputs[0]
            if row.sensitive:
                detail = f" Its description: {row.description}" if row.description else ""
                raise UsageError(
                    f"Output '{result.steps[0].step}.{row.name}' is sensitive: strata never stores its value, so "
                    f"it cannot be printed.{detail}"
                )
            click.echo(row.value)
        elif output == "console":
            _print_steps(result.steps)

        run.report(result.diagnostics, root=context.root)
        run.ok = result.diagnostics.ok
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {"deployment": result.deployment, "steps": [asdict(step) for step in result.steps]}


def _print_steps(steps: list[StepOutputs]) -> None:
    if not steps:
        click.echo("  (no execution steps)")
        return
    for step in steps:
        click.echo(status_line(step))
        if step.status != "recorded":
            continue
        if not step.outputs:
            click.echo("  (no outputs)")
            continue
        width = max(len(row.name) for row in step.outputs)
        for row in step.outputs:
            value = SENSITIVE_PLACEHOLDER if row.sensitive else (row.value or "")
            click.echo(f"  {row.name.ljust(width)}  {value}")
