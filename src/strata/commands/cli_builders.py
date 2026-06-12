"""Click CLI wiring for the build command group."""

from typing import Optional

import click

from strata.commands.builders.clean_build_command import CleanBuildCommand
from strata.commands.builders.plan_build_command import PlanBuildCommand
from strata.commands.builders.run_build_command import RunBuildCommand
from strata.commands.builders.sbom_build_command import SbomBuildCommand
from strata.commands.cli_common import (
    click_file,
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)


@click.group(name="build", help="Build platform and Terraform artifacts.")
def build():
    """Build command group."""
    pass


@build.command(name="run", help="Run platform + terraform build pipeline.")
@click_file
@click_work_path
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Validate and plan the build without writing any output files.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def build_run(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    dry_run: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Execute the build pipeline."""
    command = RunBuildCommand(
        file=file,
        work_path=work_path,
        dry_run=dry_run,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@build.command(name="clean", help="Clean deployment build artifacts.")
@click_file
@click_work_path
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show which path would be cleaned without deleting files.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def build_clean(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    dry_run: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Clean build artifacts for the selected deployment."""
    command = CleanBuildCommand(
        file=file,
        work_path=work_path,
        dry_run=dry_run,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@build.command(name="plan", help="Show artifact diff + terraform plan without writing to the real build path.")
@click_file
@click_work_path
@click.option(
    "--stage",
    default=None,
    metavar="NAME",
    help="Limit terraform plan to a specific deployment stage.",
)
@click.option(
    "--artifacts-only",
    "artifacts_only",
    is_flag=True,
    default=False,
    help="Show only the artifact diff — skip terraform plan.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def build_plan(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
    artifacts_only: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Show what build run would write, then run terraform plan per stage.

    Builds into a temporary directory, diffs the result against the existing
    build artifacts, then runs ``terraform init → validate → plan`` for each
    stage.  Nothing is written to the real build path.
    """
    command = PlanBuildCommand(
        file=file,
        work_path=work_path,
        stage=stage,
        artifacts_only=artifacts_only,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@build.command(name="sbom", help="(Re)generate SBOM from an existing platform.json.")
@click_file
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def build_sbom(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """(Re)generate the SBOM from an existing platform.json without re-running the full build."""
    command = SbomBuildCommand(
        file=file,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
