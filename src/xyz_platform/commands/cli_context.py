"""Click CLI wiring for the ``context`` command group (team-shared template variables)."""

from typing import Optional

import click

from xyz_platform.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from xyz_platform.commands.context.set_context_command import SetContextCommand


@click.group(name="context", help="Manage team-shared template variables (stored in solution.json).")
def context_group():
    """Context command group."""
    pass


@context_group.command(name="set", help="Set a template variable (e.g. `xyz context set owner myteam`).")
@click.argument("key", required=True)
@click.argument("value", required=True)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def set_context_command(
    key: str,
    value: str,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = SetContextCommand(
        action="set", key=key, value=value, work_path=work_path, output=output, verbose=verbose, quiet=quiet
    )
    success = command.execute()
    handle_command_exit(command, success)


@context_group.command(name="unset", help="Remove a template variable.")
@click.argument("key", required=True)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def unset_context_command(
    key: str,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = SetContextCommand(
        action="unset", key=key, work_path=work_path, output=output, verbose=verbose, quiet=quiet
    )
    success = command.execute()
    handle_command_exit(command, success)


@context_group.command(name="list", help="Show current template variables.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def list_context_command(
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = SetContextCommand(action="list", work_path=work_path, output=output, verbose=verbose, quiet=quiet)
    success = command.execute()
    handle_command_exit(command, success)
