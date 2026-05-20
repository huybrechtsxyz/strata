"""Click CLI wiring for the sln (solution workspace lifecycle) command group."""

import click

from strata.commands.cli_clean import clean_command
from strata.commands.cli_init import init_command
from strata.commands.cli_status import status_command
from strata.commands.sln.export_template_command import export_command


@click.group(name="sln", help="Manage the solution workspace lifecycle.")
def sln_group() -> None:
    """Solution workspace lifecycle commands."""


sln_group.add_command(init_command, name="init")
sln_group.add_command(clean_command, name="clean")
sln_group.add_command(status_command, name="status")
sln_group.add_command(export_command, name="export")
