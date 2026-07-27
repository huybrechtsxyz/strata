"""Click CLI wiring for the ``service`` command group."""

from typing import Optional

import click

from strata.commands.cli_common import (
    click_file,
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.commands.service.deploy_service_command import DeployServiceCommand
from strata.commands.service.destroy_service_command import DestroyServiceCommand
from strata.commands.service.list_service_command import ListServiceCommand
from strata.commands.service.status_service_command import StatusServiceCommand


@click.group(name="service", help="Deploy and manage individual services (namespace/module).")
def service_group():
    """Service command group."""
    pass


@service_group.command(name="list", help="List all services in the deployment definition.")
@click_file
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def service_list(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """List all services in the deployment."""
    command = ListServiceCommand(
        file=file,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@service_group.command(name="status", help="Show runtime status of a service by name.")
@click.argument("name")
@click_file
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def service_status(
    name: str,
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Show runtime status of a service."""
    command = StatusServiceCommand(
        file=file,
        name=name,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@service_group.command(name="deploy", help="Deploy a single service (namespace or module) by name.")
@click.argument("name")
@click_file
@click_work_path
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Continue deploying remaining services even if one fails.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be deployed without running any commands.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
@click.option(
    "--ai",
    "ai",
    is_flag=True,
    default=False,
    help="Run AI failure diagnosis when a service deploy step fails (requires an ai_agent integration).",
)
def service_deploy(
    name: str,
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    force: bool = False,
    dry_run: bool = False,
    ai: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Deploy a single service by name."""
    command = DeployServiceCommand(
        file=file,
        name=name,
        work_path=work_path,
        force=force,
        dry_run=dry_run,
        ai=ai,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@service_group.command(name="destroy", help="Tear down a single service by name.")
@click.argument("name")
@click_file
@click_work_path
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Required for destructive operations (auto-approve).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be destroyed without running any commands.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def service_destroy(
    name: str,
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    force: bool = False,
    dry_run: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Tear down a single service."""
    command = DestroyServiceCommand(
        file=file,
        name=name,
        work_path=work_path,
        force=force,
        dry_run=dry_run,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
