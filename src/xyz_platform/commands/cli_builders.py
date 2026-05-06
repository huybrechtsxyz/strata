"""Click CLI wiring for the build command group."""

from typing import Optional

import click

from xyz_platform.commands.builders.clean_build_command import CleanBuildCommand
from xyz_platform.commands.builders.run_build_command import RunBuildCommand
from xyz_platform.commands.cli_common import (
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
@click.option(
    "--file",
    "-f",
    default=None,
    help="Path to the deployment YAML file.",
)
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
@click.option(
    "--file",
    "-f",
    default=None,
    help="Path to the deployment YAML file.",
)
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
