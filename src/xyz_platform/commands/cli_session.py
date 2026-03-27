"""
===============================================================================
Script Name   : cli_session.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Click CLI wiring for session command group.
===============================================================================
"""

from typing import Optional

import click

from xyz_platform.commands.cli_common import (
    click_work_path,
    click_output_format,
    click_output_verbose,
    click_output_quiet,
    # click_config_path,
    # click_config_file,
    # click_no_hooks,
    handle_command_exit,
)

from xyz_platform.commands.session.init_session_command import InitSessionCommand
from xyz_platform.commands.session.clean_session_command import CleanSessionCommand
from xyz_platform.commands.session.add_source_session_command import (
    AddSourceSessionCommand,
)
from xyz_platform.commands.session.remove_source_session_command import (
    RemoveSourceSessionCommand,
)
from xyz_platform.commands.session.list_source_session_command import (
    ListSourceSessionCommand,
)

# from xyz_platform.commands.session.show_session_command import ShowSessionCommand
# from xyz_platform.commands.session.add_session_command import AddSessionCommand
# from xyz_platform.commands.session.fetch_session_command import FetchSessionCommand
# from xyz_platform.commands.session.sync_session_command import SyncSessionCommand
# from xyz_platform.commands.session.logs_session_command import LogsSessionCommand
# from xyz_platform.commands.session.list_session_command import ListSessionCommand
# from xyz_platform.commands.session.status_session_command import StatusSessionCommand
# from xyz_platform.commands.session.remove_session_command import RemoveSessionCommand

# from xyz_platform.commands.session.schema_session_command import SchemaSessionCommand


@click.group(name="session", help="Manage and view session information.")
def session_command():
    """Session command group."""
    pass


# SESSION Commands


@session_command.command(
    name="init", help="Initialize a new XYZ Platform session workspace."
)
@click.option(
    "--name",
    required=True,
    type=str,
    help="Name of the workspace (required)",
)
@click.option(
    "--editor",
    type=click.Choice(["vscode"], case_sensitive=False),
    default=None,
    help="Editor integration to activate. Use 'vscode' to create the .code-workspace file.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def session_init(
    name: str,
    editor: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
):
    """Initialize a new session workspace."""
    command = InitSessionCommand(
        name=name,
        editor=editor,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@session_command.command(
    name="clean", help="Clean workspace artifacts (logs, temp files)."
)
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
def session_clean(
    dry_run: bool = False,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
):
    """Clean session workspace artifacts (logs, temp files)."""
    command = CleanSessionCommand(
        work_path=work_path,
        dry_run=dry_run,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


# TODO
@session_command.command(name="logs", help="Show logs for the current session.")
def session_logs():
    """Show details of the current session."""
    pass


# TODO
@session_command.command(
    name="show", help="Show status and details of the current session."
)
def session_status(work_path, output, verbose):
    """Show details of the current session."""
    pass


# SOURCE Command Group: session source add/remove/list


@session_command.group(name="source", help="Manage session repository sources.")
def session_source_command():
    """Session source subcommand group."""
    pass


@session_source_command.command(
    name="add",
    help="Add a repository to the current session workspace.",
)
@click.option(
    "--name",
    required=True,
    type=str,
    help="Name of the item (used as folder name and identifier for repositories)",
)
@click.option(
    "--url",
    required=True,
    type=str,
    help="URL or path of a repository to add (e.g. git repo URL, local folder path, or archive URL).",
)
@click.option(
    "--type",
    "item_type",
    type=click.Choice(["repo", "git", "local", "archive"], case_sensitive=False),
    help="Item type: repo (auto-detect), git, local, or archive.",
)
@click.option(
    "--branch",
    type=str,
    default="main",
    help="Git branch to clone (default: main, only for git repositories)",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def session_source_add_command(
    name: str,
    url: Optional[str] = None,
    item_type: Optional[str] = None,
    branch: str = "main",
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
):
    """Add a session source (repository or config)."""
    # AddSourceSessionCommand handles both repositories and config sources, so we pass all parameters and let it sort out the logic internally.
    command = AddSourceSessionCommand(
        name=name,
        url=url,
        item_type=item_type,
        branch=branch,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@session_source_command.command(
    name="remove",
    help="Remove a repository from the current session workspace.",
)
@click.option(
    "--name",
    required=True,
    type=str,
    help="Name of the item (used as folder name and identifier for repositories)",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def session_source_remove_command(
    name: str,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
):
    """Remove a session source (repository or config)."""
    command = RemoveSourceSessionCommand(
        name=name,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@session_source_command.command(
    name="list",
    help="List session source repositories.",
)
@click_work_path
@click_output_format
@click_output_verbose
def session_source_list_command(
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
):
    """List session sources (repositories and configs)."""
    command = ListSourceSessionCommand(
        work_path=work_path,
        output=output,
        verbose=verbose,
    )
    success = command.execute()
    handle_command_exit(command, success)


# TODO
@session_source_command.command(
    name="fetch",
    help="Fetch all repositories declared in the merged platform configuration.",
)
def session_source_fetch_command():
    """Fetch session source repositories."""
    pass


# DOTENV Command Group: session dotenv add/remove/list


@session_command.group(
    name="dotenv", help="Manage session environment variable sources."
)
def session_dotenv_command():
    """Session dotenv subcommand group."""
    pass


# TODO
@session_dotenv_command.command(
    name="add",
    help="Add an environment variable source (.env file) to the current session workspace.",
)
def session_dotenv_add_command():
    """Add a session environment variable source (.env file)."""
    pass


# TODO
@session_dotenv_command.command(
    name="remove",
    help="Remove an environment variable source (.env file) from the current session workspace.",
)
def session_dotenv_remove_command():
    """Remove a session environment variable source (.env file)."""
    pass


# TODO
@session_dotenv_command.command(
    name="list",
    help="List environment variable sources (.env files) in the current session workspace.",
)
def session_dotenv_list_command():
    """List session environment variable sources (.env files)."""
    pass


# CONFIG Command Group: session config add/remove/list


@session_command.group(name="config", help="Manage session configuration sources.")
def session_config_command():
    """Session config subcommand group."""
    pass


# TODO
@session_config_command.command(
    name="add",
    help="Add a configuration source (file or directory) to the current session workspace.",
)
def session_config_add_command():
    """Add a session configuration source (file or directory)."""
    pass


# TODO
@session_config_command.command(
    name="remove",
    help="Remove a configuration source (file or directory) from the current session workspace.",
)
def session_config_remove_command():
    """Remove a session configuration source (file or directory)."""
    pass


# TODO
@session_config_command.command(
    name="list",
    help="List configuration sources (files or directories) in the current session workspace.",
)
def session_config_list_command():
    """List session configuration sources (files or directories)."""
    pass


# @session_command.command(name="show", help="Show details of the current session.")
# @click_work_path
# @click_output_format
# @click_output_verbose
# def session_show(work_path, output, verbose):
#     """Show details of the current session."""
#     command = ShowSessionCommand(
#         work_path=work_path,
#         output=output,
#         verbose=verbose,
#     )
#     success = command.execute()
#     handle_command_exit(command, success)


# @session_command.command(name="status", help="Show on-disk status of session workspace items.")
# @click_work_path
# @click_output_format
# @click_output_verbose
# def session_status(work_path, output, verbose):
#     """Show on-disk status of session workspace items."""
#     command = StatusSessionCommand(
#         work_path=work_path,
#         output=output,
#         verbose=verbose,
#     )
#     success = command.execute()
#     handle_command_exit(command, success)


# @session_command.command(
#     name="fetch",
#     help="Fetch all repositories declared in the merged platform configuration.",
# )
# @click.option(
#     "--force",
#     is_flag=True,
#     default=False,
#     help="Re-fetch even if the repository already exists on disk.",
# )
# @click.option(
#     "--dry-run",
#     is_flag=True,
#     default=False,
#     help="List what would be fetched without touching disk.",
# )
# @click.option(
#     "--name",
#     type=str,
#     default=None,
#     help="Fetch only the repository with this name.",
# )
# @click_work_path
# @click_output_format
# @click_output_verbose
# @click_output_quiet
# def session_fetch(
#     force: bool = False,
#     dry_run: bool = False,
#     name: Optional[str] = None,
#     work_path: Optional[str] = None,
#     output: Optional[str] = None,
#     verbose: bool = False,
#     quiet: bool = False,
# ):
#     """Fetch all repositories declared in the merged platform configuration."""
#     command = FetchSessionCommand(
#         force=force,
#         dry_run=dry_run,
#         name=name,
#         work_path=work_path,
#         output=output,
#         verbose=verbose,
#         quiet=quiet,
#     )
#     success = command.execute()
#     handle_command_exit(command, success)


# @session_command.command(
#     name="sync",
#     help="Re-validate config sources and re-merge the platform configuration.",
# )
# @click.option(
#     "--force",
#     is_flag=True,
#     default=False,
#     help="Remove missing config sources from session state then re-merge.",
# )
# @click_work_path
# @click_output_format
# @click_output_verbose
# @click_output_quiet
# def session_sync(
#     force: bool = False,
#     work_path: Optional[str] = None,
#     output: Optional[str] = None,
#     verbose: bool = False,
#     quiet: bool = False,
# ):
#     """Re-validate config sources and re-merge the platform configuration."""
#     command = SyncSessionCommand(
#         force=force,
#         work_path=work_path,
#         output=output,
#         verbose=verbose,
#         quiet=quiet,
#     )
#     success = command.execute()
#     handle_command_exit(command, success)


# @session_command.command(name="logs", help="Display session execution logs.")
# @click.option(
#     "--lines",
#     type=int,
#     default=50,
#     show_default=True,
#     help="Number of log lines to show",
# )
# @click.option(
#     "--minutes",
#     type=int,
#     default=None,
#     help="Show logs from the last N minutes",
# )
# @click.option(
#     "--level",
#     type=click.Choice(
#         ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False
#     ),
#     default=None,
#     help="Filter by log level",
# )
# @click.option(
#     "--session-id",
#     type=str,
#     default=None,
#     help="Filter by session ID",
# )
# @click.option(
#     "--execution-id",
#     "--exec-id",
#     type=str,
#     default=None,
#     help="Filter by execution ID",
# )
# @click.option(
#     "--session",
#     "use_current_session",
#     is_flag=True,
#     default=False,
#     help="Filter by current session only",
# )
# @click.option(
#     "--last-exec",
#     "--last-execution",
#     "use_last_execution",
#     is_flag=True,
#     default=False,
#     help="Show logs from the last execution",
# )
# @click_work_path
# @click_output_format
# @click_output_verbose
# def session_logs(
#     lines: int = 50,
#     minutes: int = None,
#     level: Optional[str] = None,
#     session_id: Optional[str] = None,
#     execution_id: Optional[str] = None,
#     use_current_session: bool = False,
#     use_last_execution: bool = False,
#     work_path: Optional[str] = None,
#     output: Optional[str] = None,
#     verbose: bool = False,
# ):
#     """Display session execution logs."""
#     command = LogsSessionCommand(
#         work_path=work_path,
#         lines=lines,
#         minutes=minutes,
#         level=level,
#         session_id=session_id,
#         execution_id=execution_id,
#         use_current_session=use_current_session,
#         use_last_execution=use_last_execution,
#         output=output,
#         verbose=verbose,
#     )
#     success = command.execute()
#     handle_command_exit(command, success)


# @session_command.command(name="list", help="List items tracked in the session workspace.")
# @click_work_path
# @click_output_format
# @click_output_verbose
# def session_list(work_path, output, verbose):
#     """List items tracked in the session workspace."""
#     command = ListSessionCommand(
#         work_path=work_path,
#         output=output,
#         verbose=verbose,
#     )
#     success = command.execute()
#     handle_command_exit(command, success)


# @session_command.command(
#     name="remove",
#     help="Remove a repository or config source from the session workspace.",
# )
# @click.option(
#     "--name",
#     required=True,
#     type=str,
#     help="Name of the item to remove",
# )
# @click.option(
#     "--config",
#     "remove_config",
#     is_flag=True,
#     default=False,
#     help="Remove a config source instead of a repository.",
# )
# @click.option(
#     "--delete",
#     "delete_folder",
#     is_flag=True,
#     default=False,
#     help="Also delete the repository folder from disk (repo mode only).",
# )
# @click.option(
#     "--dry-run",
#     is_flag=True,
#     default=False,
#     help="Report what would be removed without making changes.",
# )
# @click_work_path
# @click_output_format
# @click_output_verbose
# @click_output_quiet
# def session_remove(
#     name, remove_config, delete_folder, dry_run, work_path, output, verbose, quiet
# ):
#     """Remove a repository or config source from the session workspace."""
#     command = RemoveSessionCommand(
#         name=name,
#         work_path=work_path,
#         delete_folder=delete_folder,
#         dry_run=dry_run,
#         config=remove_config,
#         output=output,
#         verbose=verbose,
#         quiet=quiet,
#     )
#     success = command.execute()
#     handle_command_exit(command, success)


# @session_command.command(name="clean", help="Clean workspace artifacts (logs, temp files).")
# @click.option(
#     "--logs/--no-logs",
#     default=True,
#     show_default=True,
#     help="Delete files in the logs/ folder.",
# )
# @click.option(
#     "--dry-run",
#     is_flag=True,
#     default=False,
#     help="Report what would be deleted without making changes.",
# )
# @click_work_path
# @click_output_format
# @click_output_verbose
# @click_output_quiet
# def session_clean(logs, dry_run, work_path, output, verbose, quiet):
#     """Clean workspace artifacts (logs, temp files)."""
#     command = CleanSessionCommand(
#         work_path=work_path,
#         logs=logs,
#         dry_run=dry_run,
#         output=output,
#         verbose=verbose,
#         quiet=quiet,
#     )
#     success = command.execute()
#     handle_command_exit(command, success)


# @session_command.command(
#     name="schemas", help="Export JSON schemas for all platform config models."
# )
# @click.option(
#     "--output-dir",
#     type=str,
#     default=None,
#     help="Directory to write schema files into (default: .xyz-platform/schemas)",
# )
# @click.option(
#     "--editor",
#     type=click.Choice(["vscode"], case_sensitive=False),
#     default=None,
#     help="Editor integration to activate. Use 'vscode' to update .vscode/settings.json with yaml.schemas entries.",
# )
# @click_work_path
# @click_output_format
# @click_output_verbose
# @click_output_quiet
# def session_schemas(
#     output_dir: Optional[str] = None,
#     editor: Optional[str] = None,
#     work_path: Optional[str] = None,
#     output: Optional[str] = None,
#     verbose: bool = False,
#     quiet: bool = False,
# ):
#     """Export JSON schemas for all platform config models."""
#     command = SchemaSessionCommand(
#         work_path=work_path,
#         output_dir=output_dir,
#         editor=editor,
#         output=output,
#         verbose=verbose,
#         quiet=quiet,
#     )
#     success = command.execute()
#     handle_command_exit(command, success)
