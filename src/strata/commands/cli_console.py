"""Click CLI wiring for the console command."""

from typing import Optional

import click

from strata.commands.cli_common import click_work_path, handle_command_exit
from strata.commands.console.run_console_command import ConsoleCommand


@click.command(name="console")
@click_work_path
@click.option("--no-color", is_flag=True, default=False, help="Disable color output.")
def console_command(
    work_path: Optional[str] = None,
    no_color: bool = False,
) -> None:
    """Interactive workspace session with guided onboarding."""
    command = ConsoleCommand(work_path=work_path, no_color=no_color)
    success = command.execute()
    handle_command_exit(command, success)
