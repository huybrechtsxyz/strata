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


@click.group(name="session", help="Manage and view session information.")
def session():
    """Session command group."""
    pass


@session.command(name="init", help="Initialize a new XYZ Platform session workspace.")
@click.option(
    "--name",
    required=True,
    type=str,
    help="Name of the VSCode workspace (required)",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def session_init(
    name: str,
    work_path: str = None,
    output: str = None,
    verbose: bool = None,
    quiet: bool = None,
):
    """Initialize a new session workspace."""
    command = InitSessionCommand(
        name=name,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
