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
from xyz_platform.commands.config.env_config_command import EnvConfigCommand
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

    command = SetConfigCommand(action="list", work_path=work_path, output=output, verbose=verbose, quiet=quiet)
    success = command.execute()
    handle_command_exit(command, success)


# ==============================================================================
# config env  (subgroup)
# ==============================================================================


@config_group.group(name="env", help="Manage env-file sources loaded at command startup.")
def config_env_group():
    """Environment variable source management."""
    pass


@config_env_group.command(name="add", help="Register an env-file source.")
@click.argument("name")
@click.argument("path")
@click.option(
    "--order",
    default=50,
    show_default=True,
    type=int,
    help="Load order (ascending; higher overrides lower).",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def config_env_add(
    name: str,
    path: str,
    order: int = 50,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = EnvConfigCommand(
        action="add",
        name=name,
        path=path,
        order=order,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@config_env_group.command(name="remove", help="Unregister an env-file source.")
@click.argument("name")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def config_env_remove(
    name: str,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = EnvConfigCommand(
        action="remove",
        name=name,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@config_env_group.command(name="list", help="Show registered env-file sources and their load order.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def config_env_list(
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = EnvConfigCommand(
        action="list",
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@config_env_group.command(name="show", help="Resolve all sources and display the merged environment.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def config_env_show(
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = EnvConfigCommand(
        action="show",
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
