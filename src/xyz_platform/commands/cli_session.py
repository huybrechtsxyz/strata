"""
===============================================================================
Script Name   : cli_session.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Click CLI wiring for session command group.
===============================================================================
"""

import click

from xyz_platform.commands.cli_common import (
    click_work_path,
    click_output_format,
    click_output_verbose,
    click_output_quiet,
    handle_command_exit,
)
from xyz_platform.commands.session.init_session_command import InitSessionCommand
from xyz_platform.commands.session.show_session_command import ShowSessionCommand
from xyz_platform.commands.session.add_session_command import AddSessionCommand
from xyz_platform.commands.session.logs_session_command import LogsSessionCommand
from xyz_platform.commands.session.list_session_command import ListSessionCommand
from xyz_platform.commands.session.status_session_command import StatusSessionCommand
from xyz_platform.commands.session.remove_session_command import RemoveSessionCommand
from xyz_platform.commands.session.clean_session_command import CleanSessionCommand
from xyz_platform.commands.session.schema_session_command import SchemaSessionCommand


@click.group(name="session", help="Manage and view session information.")
def session():
    """Session command group."""
    pass


@session.command(name="init", help="Initialize a new XYZ Platform session workspace.")
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
    editor: str = None,
    work_path: str = None,
    output: str = None,
    verbose: bool = None,
    quiet: bool = None,
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


@session.command(name="show", help="Show details of the current session.")
@click_work_path
@click_output_format
@click_output_verbose
def session_show(work_path, output, verbose):
    """Show details of the current session."""
    command = ShowSessionCommand(
        work_path=work_path,
        output=output,
        verbose=verbose,
    )
    success = command.execute()
    handle_command_exit(command, success)


@session.command(name="status", help="Show on-disk status of session workspace items.")
@click_work_path
@click_output_format
@click_output_verbose
def session_status(work_path, output, verbose):
    """Show on-disk status of session workspace items."""
    command = StatusSessionCommand(
        work_path=work_path,
        output=output,
        verbose=verbose,
    )
    success = command.execute()
    handle_command_exit(command, success)


@session.command(
    name="add",
    help="Add an item to the current session workspace (repository, config, etc.).",
)
@click.option(
    "--name",
    required=True,
    type=str,
    help="Name of the item (used as the folder name)",
)
@click.option(
    "--url",
    required=True,
    type=str,
    help="URL or path to the item",
)
@click.option(
    "--type",
    "item_type",
    type=click.Choice(["repo", "git", "local", "archive"], case_sensitive=False),
    help="Item type: repo (auto-detect git/local/archive), git, local, or archive. Auto-detected from URL if omitted.",
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
def session_add(
    name: str,
    url: str,
    item_type: str = None,
    branch: str = "main",
    work_path: str = None,
    output: str = None,
    verbose: bool = None,
    quiet: bool = None,
):
    """Add an item to the session workspace."""
    command = AddSessionCommand(
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


@session.command(name="logs", help="Display session execution logs.")
@click.option(
    "--lines",
    type=int,
    default=50,
    show_default=True,
    help="Number of log lines to show",
)
@click.option(
    "--minutes",
    type=int,
    default=None,
    help="Show logs from the last N minutes",
)
@click.option(
    "--level",
    type=click.Choice(
        ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False
    ),
    default=None,
    help="Filter by log level",
)
@click.option(
    "--session-id",
    type=str,
    default=None,
    help="Filter by session ID",
)
@click.option(
    "--execution-id",
    "--exec-id",
    type=str,
    default=None,
    help="Filter by execution ID",
)
@click.option(
    "--session",
    "use_current_session",
    is_flag=True,
    default=False,
    help="Filter by current session only",
)
@click.option(
    "--last-exec",
    "--last-execution",
    "use_last_execution",
    is_flag=True,
    default=False,
    help="Show logs from the last execution",
)
@click_work_path
@click_output_format
@click_output_verbose
def session_logs(
    lines: int = 50,
    minutes: int = None,
    level: str = None,
    session_id: str = None,
    execution_id: str = None,
    use_current_session: bool = False,
    use_last_execution: bool = False,
    work_path: str = None,
    output: str = None,
    verbose: bool = None,
):
    """Display session execution logs."""
    command = LogsSessionCommand(
        work_path=work_path,
        lines=lines,
        minutes=minutes,
        level=level,
        session_id=session_id,
        execution_id=execution_id,
        use_current_session=use_current_session,
        use_last_execution=use_last_execution,
        output=output,
        verbose=verbose,
    )
    success = command.execute()
    handle_command_exit(command, success)


@session.command(name="list", help="List items tracked in the session workspace.")
@click_work_path
@click_output_format
@click_output_verbose
def session_list(work_path, output, verbose):
    """List items tracked in the session workspace."""
    command = ListSessionCommand(
        work_path=work_path,
        output=output,
        verbose=verbose,
    )
    success = command.execute()
    handle_command_exit(command, success)


@session.command(name="remove", help="Remove an item from the session workspace.")
@click.option(
    "--name",
    required=True,
    type=str,
    help="Name of the item to remove",
)
@click.option(
    "--delete",
    "delete_folder",
    is_flag=True,
    default=False,
    help="Also delete the repository folder from disk.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Report what would be removed without making changes.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def session_remove(name, delete_folder, dry_run, work_path, output, verbose, quiet):
    """Remove an item from the session workspace."""
    command = RemoveSessionCommand(
        name=name,
        work_path=work_path,
        delete_folder=delete_folder,
        dry_run=dry_run,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@session.command(name="clean", help="Clean workspace artifacts (logs, temp files).")
@click.option(
    "--logs/--no-logs",
    default=True,
    show_default=True,
    help="Delete files in the logs/ folder.",
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
def session_clean(logs, dry_run, work_path, output, verbose, quiet):
    """Clean workspace artifacts (logs, temp files)."""
    command = CleanSessionCommand(
        work_path=work_path,
        logs=logs,
        dry_run=dry_run,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@session.command(
    name="schemas", help="Export JSON schemas for all platform config models."
)
@click.option(
    "--output-dir",
    type=str,
    default=None,
    help="Directory to write schema files into (default: .xyz-platform/schemas)",
)
@click.option(
    "--editor",
    type=click.Choice(["vscode"], case_sensitive=False),
    default=None,
    help="Editor integration to activate. Use 'vscode' to update .vscode/settings.json with yaml.schemas entries.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def session_schemas(
    output_dir: str = None,
    editor: str = None,
    work_path: str = None,
    output: str = None,
    verbose: bool = None,
    quiet: bool = None,
):
    """Export JSON schemas for all platform config models."""
    command = SchemaSessionCommand(
        work_path=work_path,
        output_dir=output_dir,
        editor=editor,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
