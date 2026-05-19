"""Click CLI wiring for the top-level repo command group."""

from typing import Optional

import click

from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.commands.repo.add_repo_solution_command import AddRepoSolutionCommand
from strata.commands.repo.list_repo_solution_command import ListRepoSolutionCommand
from strata.commands.repo.remove_repo_solution_command import RemoveRepoSolutionCommand
from strata.commands.repo.status_repo_solution_command import StatusRepoSolutionCommand
from strata.commands.repo.sync_repo_solution_command import SyncRepoSolutionCommand


@click.group(name="repo", help="Manage repositories in the current solution.")
def repo_group():
    """Repo command group."""
    pass


@repo_group.command(name="add", help="Register a repository in the current solution.")
@click.argument("name")
@click.argument("url")
@click.option(
    "--branch",
    default="main",
    show_default=True,
    help="Default branch to track.",
)
@click.option(
    "--path",
    default=None,
    help="Local path relative to work-path (default: repos/<name>).",
)
@click.option(
    "--clone",
    is_flag=True,
    default=False,
    help="Clone the repository immediately after registering.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def repo_add(
    name: str,
    url: str,
    branch: str = "main",
    path: Optional[str] = None,
    clone: bool = False,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Register a repository entry in the solution."""
    command = AddRepoSolutionCommand(
        name=name,
        url=url,
        branch=branch,
        path=path,
        clone=clone,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@repo_group.command(name="list", help="List repositories registered in the current solution.")
@click.option(
    "--name",
    default=None,
    help="Show only this repository (default: show all).",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def repo_list(
    name: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """List repository entries registered in the solution."""
    command = ListRepoSolutionCommand(
        name=name,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@repo_group.command(name="remove", help="Remove a repository from the current solution.")
@click.argument("name")
@click.option(
    "--purge",
    is_flag=True,
    default=False,
    help="Also delete the local clone directory from disk.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def repo_remove(
    name: str,
    purge: bool = False,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Remove a repository entry from the solution."""
    command = RemoveRepoSolutionCommand(
        name=name,
        purge=purge,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@repo_group.command(name="sync", help="Clone or pull repositories registered in the solution.")
@click.option(
    "--name",
    default=None,
    help="Sync only this repository (default: sync all).",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Hard-reset dirty working trees instead of skipping them.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def repo_sync(
    name: Optional[str] = None,
    force: bool = False,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Clone or pull all (or one) repositories registered in the solution."""
    command = SyncRepoSolutionCommand(
        name=name,
        force=force,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@repo_group.command(name="status", help="Show git state for all (or one) registered repositories.")
@click.option(
    "--name",
    default=None,
    metavar="NAME",
    help="Inspect a single registered repository by name.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def repo_status(
    name: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Show git working-tree state for registered repositories."""
    command = StatusRepoSolutionCommand(
        name=name,
        work_path=work_path,
        output=output,
        verbose=verbose or False,
        quiet=quiet or False,
    )
    success = command.execute()
    handle_command_exit(command, success)
