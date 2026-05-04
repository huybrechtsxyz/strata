"""Click CLI wiring for the top-level profile command group."""

from typing import Optional

import click

from xyz_platform.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from xyz_platform.commands.solution.activate_profile_command import ActivateProfileCommand
from xyz_platform.commands.solution.add_profile_command import AddProfileCommand
from xyz_platform.commands.solution.list_profile_command import ListProfileCommand
from xyz_platform.commands.solution.list_profile_path_command import ListProfilePathCommand
from xyz_platform.commands.solution.remove_profile_command import RemoveProfileCommand


@click.group(name="profile", help="Manage profiles in the current solution.")
def profile_group():
    """Profile command group."""
    pass


@profile_group.command(name="add", help="Add a new profile to the current solution.")
@click.argument("name")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def profile_add(
    name: str,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Add a profile to the solution."""
    command = AddProfileCommand(
        name=name,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@profile_group.command(name="remove", help="Remove a profile from the current solution.")
@click.argument("name")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def profile_remove(
    name: str,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Remove a profile from the solution."""
    command = RemoveProfileCommand(
        name=name,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@profile_group.command(name="list", help="List profiles registered in the current solution.")
@click.option(
    "--name",
    default=None,
    help="Show only this profile (default: show all).",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def profile_list(
    name: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """List profiles registered in the solution."""
    command = ListProfileCommand(
        name=name,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@profile_group.command(name="activate", help="Activate a profile in the current solution.")
@click.argument("name")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def profile_activate(
    name: str,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Activate a profile (deactivates all others)."""
    command = ActivateProfileCommand(
        name=name,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@profile_group.command(name="show", help="Show all registered ref paths for a profile, grouped by type.")
@click.argument("name")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def profile_show(
    name: str,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Show all registered ref paths for a profile, grouped by type."""
    command = ListProfilePathCommand(
        profile=name,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
