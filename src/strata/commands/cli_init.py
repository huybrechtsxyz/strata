"""Click CLI wiring for the top-level init command."""

from typing import Optional

import click

from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.commands.init.init_solution_command import InitSolutionCommand


@click.command(name="init", help="Initialize a new XYZ Platform solution workspace.")
@click.option(
    "--name",
    required=True,
    type=str,
    help="Name of the solution workspace.",
)
@click.option(
    "--template",
    "template",
    default=None,
    type=str,
    help=(
        "Scaffold template to apply. Accepts a built-in name (e.g. 'aks') "
        "or a path to a local template folder containing 'scaffold/' and an optional 'template.yaml'."
    ),
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def init_command(
    name: str,
    template: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Initialize a new solution workspace."""
    command = InitSolutionCommand(
        name=name,
        template=template,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
