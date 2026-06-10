"""Click CLI wiring for the guide command."""

from typing import Optional

import click

from strata.commands.cli_common import (
    click_file,
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.commands.guide.show_guide_command import GuideCommand


@click.command(name="guide")
@click_file
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def guide_command(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Show setup progress and suggest the next action for this workspace."""
    command = GuideCommand(
        file=file,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
