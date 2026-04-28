"""Click CLI wiring for solution command group."""

from typing import Optional

import click

from xyz_platform.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from xyz_platform.commands.solution.clean_solution_command import CleanSolutionCommand
from xyz_platform.commands.solution.init_solution_command import InitSolutionCommand


@click.group(name="solution", help="Manage XYZ Platform solutions.")
def solution_group():
    """Solution command group."""
    pass


@solution_group.command(name="init", help="Initialize a new XYZ Platform solution workspace.")
@click.option(
    "--name",
    required=True,
    type=str,
    help="Name of the solution workspace.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def solution_init(
    name: str,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Initialize a new solution workspace."""
    command = InitSolutionCommand(
        name=name,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@solution_group.command(name="clean", help="Clean solution artifacts (logs, temp files).")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Report what would be deleted without making changes.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def solution_clean(
    dry_run: bool = False,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
):
    """Clean solution workspace artifacts (logs, temp files)."""
    command = CleanSolutionCommand(
        work_path=work_path,
        dry_run=dry_run,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
