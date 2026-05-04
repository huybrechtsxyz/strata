"""Click CLI wiring for solution command group."""

from typing import Optional

import click

from xyz_platform.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from xyz_platform.commands.solution.add_repo_solution_command import AddRepoSolutionCommand
from xyz_platform.commands.solution.clean_solution_command import CleanSolutionCommand
from xyz_platform.commands.solution.init_solution_command import InitSolutionCommand
from xyz_platform.commands.solution.list_repo_solution_command import ListRepoSolutionCommand
from xyz_platform.commands.solution.remove_repo_solution_command import RemoveRepoSolutionCommand
from xyz_platform.commands.solution.sync_repo_solution_command import SyncRepoSolutionCommand


@click.group(name="solution", help="Manage XYZ Platform solutions.")
def solution_group():
    """Solution command group."""
    pass


@solution_group.command(name="init", help="Initialize a new XYZ Platform solution workspace.")
@click.option(
    "--name",
    required=True,
    type=str,
    help="Name of the solution workspace.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def solution_init(
    name: str,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Initialize a new solution workspace."""
    command = InitSolutionCommand(
        name=name,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@solution_group.command(name="clean", help="Clean solution artifacts (logs, temp files).")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Report what would be deleted without making changes.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def solution_clean(
    dry_run: bool = False,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
):
    """Clean solution workspace artifacts (logs, temp files)."""
    command = CleanSolutionCommand(
        work_path=work_path,
        dry_run=dry_run,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


# ---------------------------------------------------------------------------
# solution repo group
# ---------------------------------------------------------------------------

@click.group(name="repo", help="Manage repositories in the current solution.")
def repo_group():
    """Repo subcommand group."""
    pass


solution_group.add_command(repo_group)


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
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def solution_repo_add(
    name: str,
    url: str,
    branch: str = "main",
    path: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Register a repository entry in the solution (no cloning)."""
    command = AddRepoSolutionCommand(
        name=name,
        url=url,
        branch=branch,
        path=path,
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
def solution_repo_list(
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
def solution_repo_remove(
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
def solution_repo_sync(
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
