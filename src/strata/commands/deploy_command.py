#!/usr/bin/env python3
"""`strata deploy run` — execute a deployment's workspace provisioners
(Terraform plan/apply, the Compose/Helm equivalents) against `build run`'s
already-rendered output (docs/design/deploy-command.md).

No `-f FILE` — same reasoning as `build_command`/`validate_command`/
`values_command`: v2 addresses documents by `(kind, name)`, so the argument
is the deployment's name, resolved against the solution found by walking up
from `--path` (default: cwd).

Never renders anything itself — `build run` is a separate, prior step;
`deploy_controller.deploy_run()` only calls `plan`/`deploy` against a
directory `build run` already wrote to (ADR-0022 D4's render-vs-execute
split).
"""

import uuid
from datetime import datetime, timezone
from pathlib import Path

import click

from strata.commands.options import output_option, quiet_option, resolve_work_path, verbose_option
from strata.commands.run import command_run
from strata.controllers.audit_run import finalize_and_distribute_deploy_audit
from strata.controllers.deploy_controller import deploy_run
from strata.controllers.solution_context import open_solution
from strata.utils.layout import build_dir


@click.group("deploy")
def deploy_command() -> None:
    """Execute a deployment's workspace provisioners."""


@deploy_command.command("run")
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
    help="Where `build run` already rendered artifacts. Defaults to '<solution root>/build/"
    "<deployment>' — the same default `build run` itself uses. Never re-rendered here.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Currently has no effect (docs/design/deploy-command.md's Remaining Work item 7) — "
    "v1 uses it to bypass advisory approval gates (AI plan review, promotion overrides), "
    "neither of which v2 has built yet. Accepted for CLI parity with real invocations, which "
    "always pass it; applying a saved plan file never needs '-auto-approve' regardless.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Report which steps would run without calling init/validate/plan/deploy at all.",
)
@click.option(
    "--stage",
    default=None,
    metavar="NAME",
    help="Restrict to the one workspace execution step named NAME.",
)
@click.option(
    "--scope",
    default=None,
    metavar="LABEL",
    help="Restrict to steps whose ProvisioningStepModel.scope matches LABEL.",
)
@output_option
@quiet_option
@verbose_option
def deploy_run_command(
    deployment: str,
    path: Path | None,
    build_path: Path | None,
    force: bool,
    dry_run: bool,
    stage: str | None,
    scope: str | None,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """Execute DEPLOYMENT's workspace provisioners against `build run`'s output.

    \b
    Exit codes:
      0  every filtered step deployed successfully
      2  bad arguments, DEPLOYMENT does not exist, or not inside a solution
      3  a declared value failed to resolve, a filtered step's tool is not
         available on PATH, or a step's plan/deploy failed

    Run 'strata build run DEPLOYMENT' first — this never renders anything,
    only executes what is already on disk at --build-path.
    """
    with command_run("deploy run", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(path)).require_valid()
        solution = context.controller.solution
        target = build_path or build_dir(context.root, deployment)

        run.describe(
            solution=solution.meta.name if solution else "(unnamed)",
            root=context.root,
            deployment=deployment,
            build_path=target,
        )

        execution_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)

        diagnostics = deploy_run(
            context,
            deployment,
            target,
            force=force,
            dry_run=dry_run,
            stage=stage,
            scope=scope,
            on_step=run.step,
        )

        # Audit trail — finalize + write locally + distribute (docs/design/
        # audit-trail.md's Layer 2). Never affects which deploy stages ran or
        # their recorded outcome; a required sink's push failure can still
        # fail this command's own exit code, merged in below.
        audit_diagnostics = finalize_and_distribute_deploy_audit(
            context,
            deployment,
            target,
            execution_id=execution_id,
            started_at=started_at,
            run_diagnostics=diagnostics,
            dry_run=dry_run,
        )
        diagnostics.extend(audit_diagnostics)

        run.report(diagnostics, root=context.root)
        run.ok = diagnostics.ok
