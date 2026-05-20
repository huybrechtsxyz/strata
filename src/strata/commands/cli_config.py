"""Click CLI wiring for the config command group (workspace defaults)."""

from typing import Optional

import click

from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.commands.config.set_config_command import SetConfigCommand
from strata.commands.logger.log_config_command import LogConfigCommand


@click.group(name="config", help="Manage workspace CLI preferences and logging configuration.")
def config_group():
    """Configuration command group."""
    pass


@config_group.command(name="set", help="Set a workspace default (e.g. `strata config set output json`).")
@click.argument("key", required=True)
@click.argument("value", required=True)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def set_config_command(
    key: str,
    value: str,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = SetConfigCommand(
        action="set", key=key, value=value, work_path=work_path, output=output, verbose=verbose, quiet=quiet
    )
    success = command.execute()
    handle_command_exit(command, success)


@config_group.command(name="unset", help="Remove a workspace default (e.g. `strata config unset output`).")
@click.argument("key", required=True)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def unset_config_command(
    key: str,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = SetConfigCommand(
        action="unset", key=key, work_path=work_path, output=output, verbose=verbose, quiet=quiet
    )
    success = command.execute()
    handle_command_exit(command, success)


@config_group.command(name="list", help="Show current workspace defaults.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def list_config_command(
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = SetConfigCommand(action="list", work_path=work_path, output=output, verbose=verbose, quiet=quiet)
    success = command.execute()
    handle_command_exit(command, success)


# ==============================================================================
# strata config log  (logging configuration subgroup)
# ==============================================================================


@config_group.group(name="log", help="Manage workspace logging configuration (logging.yaml).")
def config_log_group():
    """Config log subgroup."""
    pass


@config_log_group.command(name="list", help="Show the current logging configuration (logging.yaml).")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def config_log_list(
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = LogConfigCommand(action="list", work_path=work_path, output=output, verbose=verbose, quiet=quiet)
    success = command.execute()
    handle_command_exit(command, success)


@config_log_group.command(name="get", help="Get a logging configuration value by key (dot-notation).")
@click.argument("key")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def config_log_get(
    key: str,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = LogConfigCommand(action="get", key=key, work_path=work_path, output=output, verbose=verbose, quiet=quiet)
    success = command.execute()
    handle_command_exit(command, success)


@config_log_group.command(name="set", help="Set a logging configuration value.")
@click.argument("key")
@click.argument("value")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def config_log_set(
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


@config_log_group.command(name="unset", help="Remove a logging configuration key.")
@click.argument("key")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def config_log_unset(
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


@config_log_group.command(name="reset", help="Reset logging configuration to package defaults.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def config_log_reset(
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = LogConfigCommand(action="reset", work_path=work_path, output=output, verbose=verbose, quiet=quiet)
    success = command.execute()
    handle_command_exit(command, success)
