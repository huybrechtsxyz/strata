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
    click_config_path,
    click_config_file,
    handle_command_exit,
)
from xyz_platform.commands.session.init_session_command import InitSessionCommand
from xyz_platform.commands.session.show_session_command import ShowSessionCommand
from xyz_platform.commands.session.add_session_command import AddSessionCommand
from xyz_platform.commands.session.fetch_session_command import FetchSessionCommand
from xyz_platform.commands.session.sync_session_command import SyncSessionCommand
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
    help="Add a repository OR register a config source to the current session workspace.",
)
@click.option(
    "--name",
    required=False,
    type=str,
    help="Name of the item (used as folder name for repos; derived from path for config sources)",
)
@click.option(
    "--url",
    required=False,
    type=str,
    help="URL or path of a repository to add (mutually exclusive with --config-path/--config-file)",
)
@click.option(
    "--type",
    "item_type",
    type=click.Choice(["repo", "git", "local", "archive"], case_sensitive=False),
    help="Item type: repo (auto-detect), git, local, or archive. Ignored for config sources.",
)
@click.option(
    "--branch",
    type=str,
    default="main",
    help="Git branch to clone (default: main, only for git repositories)",
)
@click_config_path
@click_config_file
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def session_add(
    name: str = None,
    url: str = None,
    item_type: str = None,
    branch: str = "main",
    config_path: str = None,
    config_file: str = None,
    work_path: str = None,
    output: str = None,
    verbose: bool = None,
    quiet: bool = None,
):
    """Add a repository or register a config source in the session workspace."""
    # Validate: exactly one mode must be chosen
    if not url and not config_path and not config_file:
        raise click.UsageError(
            "Provide --url (repository) or --config-path/--config-file (config source)."
        )
    if url and (config_path or config_file):
        raise click.UsageError(
            "--url and --config-path/--config-file are mutually exclusive."
        )
    command = AddSessionCommand(
        name=name,
        url=url,
        item_type=item_type,
        branch=branch,
        config_path=config_path,
        config_file=config_file,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@session.command(
    name="fetch",
    help="Fetch all repositories declared in the merged platform configuration.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Re-fetch even if the repository already exists on disk.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="List what would be fetched without touching disk.",
)
@click.option(
    "--name",
    type=str,
    default=None,
    help="Fetch only the repository with this name.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def session_fetch(
    force: bool = False,
    dry_run: bool = False,
    name: str = None,
    work_path: str = None,
    output: str = None,
    verbose: bool = None,
    quiet: bool = None,
):
    """Fetch all repositories declared in the merged platform configuration."""
    command = FetchSessionCommand(
        force=force,
        dry_run=dry_run,
        name=name,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@session.command(
    name="sync",
    help="Re-validate config sources and re-merge the platform configuration.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Remove missing config sources from session state then re-merge.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def session_sync(
    force: bool = False,
    work_path: str = None,
    output: str = None,
    verbose: bool = None,
    quiet: bool = None,
):
    """Re-validate config sources and re-merge the platform configuration."""
    command = SyncSessionCommand(
        force=force,
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


@session.command(name="remove", help="Remove a repository or config source from the session workspace.")
@click.option(
    "--name",
    required=True,
    type=str,
    help="Name of the item to remove",
)
@click.option(
    "--config",
    "remove_config",
    is_flag=True,
    default=False,
    help="Remove a config source instead of a repository.",
)
@click.option(
    "--delete",
    "delete_folder",
    is_flag=True,
    default=False,
    help="Also delete the repository folder from disk (repo mode only).",
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
def session_remove(name, remove_config, delete_folder, dry_run, work_path, output, verbose, quiet):
    """Remove a repository or config source from the session workspace."""
    command = RemoveSessionCommand(
        name=name,
        work_path=work_path,
        delete_folder=delete_folder,
        dry_run=dry_run,
        config=remove_config,
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
