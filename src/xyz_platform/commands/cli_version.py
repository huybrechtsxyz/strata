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
import json

from xyz_platform.commands.base_command import BaseCommand
from xyz_platform.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
)
from xyz_platform.utils import system


@click.command()
@click_output_format
@click_output_verbose
@click_output_quiet
def version_command(output: str = None, verbose: bool = None, quiet: bool = None):
    """Show CLI version."""
    version = system.get_cli_version()

    # Output handling based on --output option
    if output == "json":
        click.echo(json.dumps({"platform": "xyz-platform", "version": "1.0.0"}))
        return

    # Default to text output if no format specified or if --output text
    elif output == "text":
        click.echo(version)
        return

    # If no output format specified, show full console header and footer with version in between
    command: BaseCommand = BaseCommand(output=output, verbose=verbose, quiet=quiet)
    command.ShowConsoleHeader(work_path=None)  # No work path for version command
    click.echo("")
    click.echo(f"XYZ Platform CLI Version: {version}")
    click.echo("")
    command.ShowConsoleFooter()
