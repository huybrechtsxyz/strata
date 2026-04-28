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


@config_group.command(name="set", help="Set a workspace default (e.g. `xyz set output json`).")
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


@config_group.command(name="unset", help="Remove a workspace default (e.g. `xyz unset output`).")
@click.argument("key", required=True)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def unset_config_command(
    key: str, work_path: Optional[str] = None, output: Optional[str] = None, verbose: bool = False, quiet: bool = False
) -> None:
    ctx = click.get_current_context()
    parent = ctx.parent
    if parent is not None:
        if work_path is None:
            work_path = parent.params.get("work_path")
        parent_output = parent.params.get("output")
        if output in (None, "console") and parent_output and parent_output != "console":
            output = parent_output
        if not verbose:
            verbose = parent.params.get("verbose") or False
        if not quiet:
            quiet = parent.params.get("quiet") or False

    command = SetConfigCommand(
        action="unset", key=key, work_path=work_path, output=output, verbose=verbose, quiet=quiet
    )
    success = command.execute()
    handle_command_exit(command, success)


@config_group.command(name="list", help="Current workspace defaults information.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def list_config_command(
    work_path: Optional[str] = None, output: Optional[str] = None, verbose: bool = False, quiet: bool = False
) -> None:
    # If options were provided at the group level (e.g. `config --output json list`),
    # Click passes them to the group callback, not the subcommand. In that case,
    # inherit the values from the parent context so subcommands behave consistently.
    ctx = click.get_current_context()
    parent = ctx.parent
    if parent is not None:
        if work_path is None:
            work_path = parent.params.get("work_path")
        # If the subcommand left `--output` at its default ("console"), inherit
        # the parent's value when the parent provided a structured format.
        parent_output = parent.params.get("output")
        if output in (None, "console") and parent_output and parent_output != "console":
            output = parent_output
        if not verbose:
            verbose = parent.params.get("verbose") or False
        if not quiet:
            quiet = parent.params.get("quiet") or False

    command = SetConfigCommand(action="info", work_path=work_path, output=output, verbose=verbose, quiet=quiet)
    success = command.execute()
    handle_command_exit(command, success)


# Add an alias so users can run either `xyz config list` or `xyz config info`.
config_group.add_command(list_config_command, name="info")
