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
@click.option(
    "--clean/--no-clean",
    default=None,
    help="Wipe --build-path before rendering. Defaults to on for the default build path "
    "(exclusively this build's own directory, always safe to wipe) and off for a custom "
    "--build-path (which may be pointing somewhere not exclusively owned by this build).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Report what would be cleaned, materialised and rendered without doing any of it. "
    "Every other step (deployment/workspace resolution, value-reference derivation, integration "
    "resolution) still runs for real, so a dry run still catches a bad deployment name or "
    "an unresolvable integration.",
)
@click.option(
    "--resolve",
    is_flag=True,
    default=False,
    help="Additionally validate every declared variable/feature/secret, including "
    "integration-backed stores (real network calls, real auth) - a full pre-deploy smoke "
    "test. Findings are reported; the resolved values themselves are never written anywhere, "
    "with or without this flag. Off by default: no network call beyond fetching sources.",
)
@click.option(
    "--env-file",
    "env_files",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="A .env-style file (KEY=VALUE per line) merged into the process environment before "
    "anything else runs - supplies 'environment'-store values a local shell doesn't already "
    "have, the way a CI pipeline's own exported env vars would. Repeatable; a real, "
    "already-exported env var always wins over a file's value. Applied in the order given.",
)
@output_option
@quiet_option
@verbose_option
def build_run_command(
    deployment: str,
    path: Path | None,
    build_path: Path | None,
    clean: bool | None,
    dry_run: bool,
    resolve: bool,
    env_files: tuple[Path, ...],
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """Render DEPLOYMENT's workspace provisioners into on-disk artifacts.

    Never executes anything — no plan, apply or deploy (ADR-0022 D4).

    \b
    Exit codes:
      0  every step rendered; every value --resolve was asked to validate resolved
      2  bad arguments, DEPLOYMENT does not exist, or not inside a solution
      3  the solution is invalid, or (only with --resolve) a declared value failed to resolve
      1  system failure — a remote could not be fetched, a source could
         not be materialised, or --build-path could not be cleaned

    Without --resolve, only 'constant'/'environment'-backed values are ever read (no network
    beyond fetching sources) — an unset 'environment'-store variable is simply omitted from
    output, not a failure. --resolve additionally attempts every declared value, including
    secrets and integration-backed stores, and reports any that fail.
    """
    with command_run("build run", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(path).require_valid()
        solution = context.controller.solution
        target = build_path or build_dir(context.root, deployment)
        should_clean = clean if clean is not None else build_path is None

        run.describe(
            solution=solution.meta.name if solution else "(unnamed)",
            root=context.root,
            deployment=deployment,
            build_path=target,
        )

        diagnostics = build_run(
            context,
            deployment,
            target,
            clean=should_clean,
            dry_run=dry_run,
            on_step=run.step,
            resolve=resolve,
            env_files=list(env_files),
        )
        if not dry_run:
            run.step(f"rendered to {target}")

        run.report(diagnostics, root=context.root)
        run.ok = diagnostics.ok
