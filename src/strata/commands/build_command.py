#!/usr/bin/env python3
"""`strata build run` — render a deployment's workspace into on-disk artifacts.

No `-f FILE` — same reasoning as `validate_command`/`values_command`: v2
addresses documents by `(kind, name)`, so the argument is the deployment's
name, resolved against the solution found by walking up from `--path`
(default: cwd).

Renders only, never executes — `build_controller.build_run()` only calls
`prepare()`, never `plan`/`deploy`/`destroy` (ADR-0022 D4). `strata deploy`
is a separate, not-yet-built command that acts on what this one writes.
"""

from pathlib import Path

import click

from strata.commands.options import output_option, quiet_option, verbose_option
from strata.commands.run import command_run
from strata.controllers.build_controller import build_run
from strata.controllers.solution_context import open_solution
from strata.utils.layout import build_dir


@click.group("build")
def build_command() -> None:
    """Render a deployment's workspace provisioners into on-disk artifacts."""


@build_command.command("run")
@click.argument("deployment")
@click.option(
    "--path",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Where to start looking for the solution. Defaults to the current directory.",
)
@click.option(
    "--build-path",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Where rendered artifacts land. Defaults to '<solution root>/build/<deployment>'.",
)
@output_option
@quiet_option
@verbose_option
def build_run_command(
    deployment: str,
    path: Path | None,
    build_path: Path | None,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """Render DEPLOYMENT's workspace provisioners into on-disk artifacts.

    Never executes anything — no plan, apply or deploy (ADR-0022 D4).

    \b
    Exit codes:
      0  every step rendered, every build-time value resolved
      2  bad arguments, DEPLOYMENT does not exist, or not inside a solution
      3  the solution is invalid, or a build-time value failed to resolve
      1  system failure — a remote could not be fetched, or a source could
         not be materialised
    """
    with command_run("build run", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(path).require_valid()
        solution = context.controller.solution
        target = build_path or build_dir(context.root, deployment)

        run.describe(
            solution=solution.meta.name if solution else "(unnamed)",
            root=context.root,
            deployment=deployment,
            build_path=target,
        )

        diagnostics = build_run(context, deployment, target)
        run.step(f"rendered to {target}")

        run.report(diagnostics, root=context.root)
        run.ok = diagnostics.ok
