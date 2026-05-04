"""Click CLI wiring for the log command group."""

from typing import Optional

import click

from xyz_platform.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from xyz_platform.commands.log.config_log_command import LogConfigCommand
from xyz_platform.commands.log.show_log_command import ShowLogCommand


@click.group(name="log", help="View execution logs and manage logging configuration.")
def log_group():
    """Log command group."""
    pass


# ==============================================================================
# xyz log show
# ==============================================================================


@log_group.command(name="show", help="Display execution logs for the current workspace.")
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
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def log_show(
    lines: int = 50,
    minutes: Optional[int] = None,
    level: Optional[str] = None,
    execution_id: Optional[str] = None,
    last: bool = False,
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
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


# ==============================================================================
# xyz log config  (subgroup)
# ==============================================================================


@log_group.group(name="config", help="Manage the workspace logging.yaml configuration.")
def log_config_group():
    """Log config subcommand group."""
    pass


@log_config_group.command(name="list", help="Show the current logging.yaml configuration.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def log_config_list(
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = LogConfigCommand(action="list", work_path=work_path, output=output, verbose=verbose, quiet=quiet)
    success = command.execute()
    handle_command_exit(command, success)


@log_config_group.command(name="get", help="Get a logging.yaml value by key (dot-notation).")
@click.argument("key")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def log_config_get(
    key: str,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = LogConfigCommand(action="get", key=key, work_path=work_path, output=output, verbose=verbose, quiet=quiet)
    success = command.execute()
    handle_command_exit(command, success)


@log_config_group.command(name="set", help="Set a logging.yaml value. Use 'level' as a shorthand for log level.")
@click.argument("key")
@click.argument("value")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def log_config_set(
    key: str,
    value: str,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = LogConfigCommand(
        action="set", key=key, value=value, work_path=work_path, output=output, verbose=verbose, quiet=quiet
    )
    success = command.execute()
    handle_command_exit(command, success)


@log_config_group.command(name="unset", help="Remove a key from logging.yaml.")
@click.argument("key")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def log_config_unset(
    key: str,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = LogConfigCommand(
        action="unset", key=key, work_path=work_path, output=output, verbose=verbose, quiet=quiet
    )
    success = command.execute()
    handle_command_exit(command, success)


@log_config_group.command(name="reset", help="Reset logging.yaml to the package default.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def log_config_reset(
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = LogConfigCommand(action="reset", work_path=work_path, output=output, verbose=verbose, quiet=quiet)
    success = command.execute()
    handle_command_exit(command, success)
