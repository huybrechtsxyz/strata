"""Click CLI wiring for the status command."""

from typing import Optional

import click

from xyz_platform.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from xyz_platform.commands.status.show_status_command import StatusCommand


@click.command(name="status")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def status_command(
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Show workspace health: solution, profile, repositories, and integrations."""
    command = StatusCommand(
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
