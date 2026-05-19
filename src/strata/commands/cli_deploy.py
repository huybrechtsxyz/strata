"""Click CLI wiring for the deploy command group."""

from typing import Optional

import click

from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.commands.deploy.destroy_deploy_command import DestroyDeployCommand
from strata.commands.deploy.health_deploy_command import HealthDeployCommand
from strata.commands.deploy.history_deploy_command import HistoryDeployCommand
from strata.commands.deploy.run_deploy_command import RunDeployCommand
from strata.commands.deploy.status_deploy_command import StatusDeployCommand


@click.group(name="deploy", help="Deploy platform using provisioners.")
def deploy():
    """Deploy command group."""
    pass


@deploy.command(name="run", help="Run the deploy pipeline for a deployment definition.")
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
    help="Limit execution to a specific deployment stage by name.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Skip interactive confirmation prompts and approval gates.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Validate and plan the deploy without running any provisioners.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_run(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
    force: bool = False,
    dry_run: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Execute the deploy pipeline."""
    command = RunDeployCommand(
        file=file,
        work_path=work_path,
        stage=stage,
        force=force,
        dry_run=dry_run,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deploy.command(name="destroy", help="Tear down provisioned infrastructure for a deployment definition.")
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
    help="Limit destruction to a specific deployment stage by name.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Auto-approve: run terraform destroy non-interactively. Required unless --dry-run.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Plan what would be destroyed (terraform plan -destroy) without removing anything.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_destroy(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
    force: bool = False,
    dry_run: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Tear down provisioned infrastructure."""
    command = DestroyDeployCommand(
        file=file,
        work_path=work_path,
        stage=stage,
        force=force,
        dry_run=dry_run,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deploy.command(name="status", help="Show deployment status: live Terraform outputs or saved plan details.")
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
    help="Limit output to a specific deployment stage.",
)
@click.option(
    "--plan",
    "show_plan",
    is_flag=True,
    default=False,
    help="Show the last saved .tfplan (terraform show -json). No backend calls.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_status(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
    show_plan: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Show live Terraform outputs or saved plan details."""
    command = StatusDeployCommand(
        file=file,
        work_path=work_path,
        stage=stage,
        show_plan=show_plan,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deploy.command(name="history", help="Show deployment execution history from workspace logs.")
@click_work_path
@click.option(
    "--lines",
    default=50,
    show_default=True,
    help="Maximum number of history entries to display.",
)
@click.option(
    "--operation",
    default=None,
    type=click.Choice(["run", "destroy"], case_sensitive=False),
    help="Filter to a specific operation type.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_history(
    work_path: Optional[str] = None,
    lines: int = 50,
    operation: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Show deployment execution history from workspace logs."""
    command = HistoryDeployCommand(
        work_path=work_path,
        lines=lines,
        operation=operation,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@click.option(
    "--file",
    "-f",
    required=True,
    metavar="PATH",
    help="Path to the deployment YAML file.",
)
@click_work_path
@click.option(
    "--stage",
    default=None,
    metavar="NAME",
    help="Limit checks to a specific deployment stage.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_health(
    file: str,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Run health checks against provisioned infrastructure stages."""
    command = HealthDeployCommand(
        file=file,
        work_path=work_path,
        stage=stage,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
