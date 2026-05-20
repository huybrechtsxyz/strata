"""Click CLI wiring for the diff command."""

from typing import Optional

import click

from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.commands.diff.diff_command import DiffCommand


@click.command(
    name="diff",
    help="Show what would change in the environment before deploying.\n\n"
    "Builds artifacts to a temp directory, diffs against the current build, "
    "and runs terraform plan against remote state. Nothing is modified.",
)
@click.option(
    "--file",
    "-f",
    default=None,
    metavar="PATH",
    help="Path to the deployment YAML file.",
)
@click_work_path
@click.option(
    "--stage",
    default=None,
    metavar="NAME",
    help="Limit diff to a specific deployment stage.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def diff_command(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Show what would change if you deploy now."""
    command = DiffCommand(
        file=file,
        work_path=work_path,
        stage=stage,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
