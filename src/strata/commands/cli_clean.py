"""Click CLI wiring for the top-level clean command."""

from typing import Optional

import click

from strata.commands.clean.clean_solution_command import CleanSolutionCommand
from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)


@click.command(name="clean", help="Clean solution artifacts (logs, temp files).")
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
def clean_command(
    dry_run: bool = False,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
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
