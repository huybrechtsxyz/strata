"""Click CLI wiring for the ``tools`` command group."""

from typing import Optional

import click

from xyz_platform.commands.cli_common import (
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from xyz_platform.commands.tools.check_tools_command import CheckToolsCommand
from xyz_platform.commands.tools.status_tools_command import StatusToolsCommand


@click.group(name="tools", help="Manage and inspect external tool integrations.")
def tools_group():
    """Tools command group."""
    pass


@tools_group.command(name="status", help="List all known integrations and their availability.")
@click_work_path
@click_output_verbose
@click_output_quiet
def tools_status(
    work_path: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = StatusToolsCommand(work_path=work_path, verbose=verbose, quiet=quiet)
    success = command.execute()
    handle_command_exit(command, success)


@tools_group.command(name="check", help="Deep-check a single integration by name (e.g. terraform, docker).")
@click.argument("name")
@click_work_path
@click_output_verbose
@click_output_quiet
def tools_check(
    name: str,
    work_path: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = CheckToolsCommand(name=name, work_path=work_path, verbose=verbose, quiet=quiet)
    success = command.execute()
    handle_command_exit(command, success)
