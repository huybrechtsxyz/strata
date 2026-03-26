#!/usr/bin/env python3
"""
===============================================================================
Script Name   : cli_tools.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Click CLI wiring for tools command group.
===============================================================================
"""

import click

from xyz_platform.commands.cli_common import (
    click_env_file,
    click_env_path,
    click_work_path,
    click_output_format,
    click_output_verbose,
    handle_command_exit,
)
from xyz_platform.commands.tools.status_tools_command import StatusToolsCommand


@click.group(name="tools", help="Check status of required CLI tools and integrations.")
def tools_command():
    """Tools command group."""
    pass


@tools_command.command(
    name="status", help="Display status of all required CLI tools and integrations."
)
@click_work_path
@click_env_path
@click_env_file
@click_output_format
@click_output_verbose
def tools_status(work_path, env_path, env_file, output, verbose):
    """Display status of all required CLI tools and integrations."""
    command = StatusToolsCommand(
        work_path=work_path,
        env_path=env_path,
        env_file=env_file,
        output=output,
        verbose=verbose,
    )
    success = command.execute()
    handle_command_exit(command, success)
