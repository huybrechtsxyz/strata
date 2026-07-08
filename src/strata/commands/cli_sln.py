"""Click CLI wiring for the sln (solution workspace lifecycle) command group."""

import click

from strata.commands.cli_clean import clean_command
from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.commands.cli_init import init_command
from strata.commands.cli_status import status_command
from strata.commands.sln.add_deployment_command import AddDeploymentCommand
from strata.commands.sln.export_template_command import export_command
from strata.commands.sln.list_deployments_command import ListDeploymentsCommand
from strata.commands.sln.remove_deployment_command import RemoveDeploymentCommand
from strata.commands.sln.scan_deployments_command import ScanDeploymentsCommand
from strata.commands.sln.update_solution_command import update_command


@click.group(name="sln", help="Manage the solution workspace lifecycle.")
def sln_group() -> None:
    """Solution workspace lifecycle commands."""


sln_group.add_command(init_command, name="init")
sln_group.add_command(update_command, name="update")
sln_group.add_command(clean_command, name="clean")
sln_group.add_command(status_command, name="status")
sln_group.add_command(export_command, name="export")


# ---------------------------------------------------------------------------
# strata sln deployment — manage registered deployment files
# ---------------------------------------------------------------------------


@click.group(name="deployment", help="Manage deployment files registered in this solution.")
def deployment_group() -> None:
    """Deployment registry commands."""


@deployment_group.command(name="add", help="Register a deployment YAML file in the solution.")
@click.argument("path")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def deployment_add(
    path: str,
    work_path=None,
    output=None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Register a single deployment YAML file."""
    command = AddDeploymentCommand(
        path=path,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deployment_group.command(name="remove", help="Remove a registered deployment from the solution.")
@click.argument("name")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def deployment_remove(
    name: str,
    work_path=None,
    output=None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Remove a deployment entry by name."""
    command = RemoveDeploymentCommand(
        name=name,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deployment_group.command(name="list", help="List deployment files registered in the solution.")
@click.option("--name", default=None, help="Filter to a single deployment by name.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def deployment_list(
    name=None,
    work_path=None,
    output=None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """List all registered deployments."""
    command = ListDeploymentsCommand(
        name=name,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deployment_group.command(name="scan", help="Scan a directory for deployment YAML files and register them.")
@click.argument("path", default=".", required=False)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def deployment_scan(
    path: str = ".",
    work_path=None,
    output=None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Recursively scan PATH for kind:deployment YAML files and register any found."""
    command = ScanDeploymentsCommand(
        scan_path=path,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


sln_group.add_command(deployment_group, name="deployment")
