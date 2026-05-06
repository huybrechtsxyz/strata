"""Click CLI wiring for the deploy command group."""

from typing import Optional

import click

from xyz_platform.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from xyz_platform.commands.deploy.destroy_deploy_command import DestroyDeployCommand
from xyz_platform.commands.deploy.run_deploy_command import RunDeployCommand


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
