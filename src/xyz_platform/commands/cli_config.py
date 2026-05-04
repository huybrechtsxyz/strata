"""Click CLI wiring for the config command group (workspace defaults)."""

from typing import Optional

import click

from xyz_platform.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from xyz_platform.commands.config.set_config_command import SetConfigCommand


@click.group(name="config", help="Manage XYZ Platform configurations.")
def config_group():
    """Configuration command group."""
    pass


@config_group.command(name="set", help="Set a workspace default (e.g. `xyz config set output json`).")
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


@config_group.command(name="unset", help="Remove a workspace default (e.g. `xyz config unset output`).")
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
