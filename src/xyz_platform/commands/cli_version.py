"""Click CLI wiring for session command group."""

import json
from typing import Optional

import click

from xyz_platform.commands.base_command import BaseCommand
from xyz_platform.commands.cli_common import (
    click_output_format,
    click_output_verbose,
)
from xyz_platform.utils.version import get_version


@click.command()
@click_output_format
@click_output_verbose
def version_command(
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
):
    """Show CLI version."""
    version = get_version()

    # Output handling based on --output option
    if output == "json":
        click.echo(json.dumps({"version": version}))
        return

    # Default to text output if no format specified or if --output text
    elif output == "text":
        click.echo(version)
        return

    # If no output format specified, show full console header and footer with version in between
    BaseCommand.show_console_header(work_path=None)  # No work path for version command
    click.echo("")
    click.echo(f"XYZ Platform CLI Version: {version}")
    click.echo("")
    BaseCommand.show_console_footer()
