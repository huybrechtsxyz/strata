"""Click CLI wiring for session command group."""

import json
from typing import Optional

import click

from strata.commands.base_command import BaseCommand
from strata.commands.cli_common import (
    click_output_format,
    click_output_verbose,
)
from strata.utils.version import check_for_updates, get_version


@click.command()
@click.option(
    "--check-updates",
    is_flag=True,
    default=False,
    help="Check if a newer version is available on PyPI.",
)
@click_output_format
@click_output_verbose
def version_command(
    check_updates: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
):
    """Show CLI version."""
    version = get_version()

    # Handle update check if requested
    if check_updates:
        latest, update_available = check_for_updates()

        if output == "json":
            result = {
                "version": version,
                "latest": latest,
                "update_available": update_available,
            }
            click.echo(json.dumps(result))
            return

        elif output == "text":
            if latest and update_available:
                click.echo(f"Current: {version}")
                click.echo(f"Latest:  {latest}")
                click.echo("Update available!")
            elif latest:
                click.echo(f"Current: {version}")
                click.echo(f"Latest:  {latest}")
                click.echo("You are up to date.")
            else:
                click.echo(f"Current: {version}")
                click.echo("Could not check for updates.")
            return

        else:
            # Console output
            BaseCommand.show_console_header(work_path=None)
            click.echo("")
            click.echo(f"Strata CLI Version: {version}")
            if latest and update_available:
                click.echo(f"Latest Version: {latest} (available)")
                click.echo("Run: pip install --upgrade xyz-strata")
            elif latest:
                click.echo(f"Latest Version: {latest} (you are up to date)")
            else:
                click.echo("Could not check for updates.")
            click.echo("")
            BaseCommand.show_console_footer()
            return

    # Default behavior (no update check)
    if output == "json":
        click.echo(json.dumps({"version": version}))
        return

    elif output == "text":
        click.echo(version)
        return

    else:
        # If no output format specified, show full console header and footer with version in between
        BaseCommand.show_console_header(work_path=None)  # No work path for version command
        click.echo("")
        click.echo(f"Strata CLI Version: {version}")
        click.echo("")
        BaseCommand.show_console_footer()
