"""Click CLI wiring for the log command group."""

from typing import Optional

import click

from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.commands.logger.show_log_command import ShowLogCommand


@click.group(
    name="log",
    help="Show execution logs for the current workspace.\n\nTo configure logging behaviour (levels, output), use: strata config log",
)
def log_group():
    """Log command group."""
    pass


# ==============================================================================
# strata log list
# ==============================================================================


@log_group.command(name="list", help="List execution log entries for the current workspace.")
@click.option(
    "--lines",
    default=50,
    show_default=True,
    type=int,
    help="Maximum number of log entries to show.",
)
@click.option(
    "--minutes",
    default=None,
    type=int,
    help="Show only entries from the last N minutes.",
)
@click.option(
    "--level",
    default=None,
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False),
    help="Filter by minimum log level.",
)
@click.option(
    "--execution-id",
    default=None,
    help="Filter to a specific execution ID.",
)
@click.option(
    "--last",
    is_flag=True,
    default=False,
    help="Show logs for the most recent command execution.",
)
@click.option(
    "--ai",
    "ai",
    is_flag=True,
    default=False,
    help="Summarise errors and warnings with AI: groups related failures, identifies root causes, suggests next steps.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def log_list(
    lines: int = 50,
    minutes: Optional[int] = None,
    level: Optional[str] = None,
    execution_id: Optional[str] = None,
    last: bool = False,
    ai: bool = False,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Display execution logs for the current workspace."""
    command = ShowLogCommand(
        lines=lines,
        minutes=minutes,
        level=level,
        execution_id=execution_id,
        last=last,
        ai=ai,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
