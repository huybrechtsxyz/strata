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
@click.option(
    "--next",
    "next_step",
    is_flag=True,
    default=False,
    help="Show only the next pending setup step and exit (no full checklist).",
)
@click.option(
    "--do",
    "do_step",
    is_flag=True,
    default=False,
    help="Execute the next pending setup step if all values are resolved; show what is missing otherwise.",
)
@click.option(
    "--ai",
    "ai",
    is_flag=True,
    default=False,
    help="Run AI assistance when a readiness phase is blocked (requires an ai_agent integration).",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def guide_command(
    file: Optional[str] = None,
    next_step: bool = False,
    do_step: bool = False,
    ai: bool = False,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Show setup progress and suggest the next action for this workspace."""
    command = GuideCommand(
        file=file,
        next_step=next_step,
        do_step=do_step,
        ai=ai,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
