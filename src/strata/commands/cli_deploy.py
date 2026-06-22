"""Click CLI wiring for the deploy command group."""

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
from strata.commands.deploy.destroy_deploy_command import DestroyDeployCommand
from strata.commands.deploy.health_deploy_command import HealthDeployCommand
from strata.commands.deploy.history_deploy_command import HistoryDeployCommand
from strata.commands.deploy.list_deploy_command import ListDeployCommand
from strata.commands.deploy.lock_deploy_command import LockHistoryCommand, LockReleaseCommand, LockStatusCommand
from strata.commands.deploy.output_deploy_command import OutputDeployCommand
from strata.commands.deploy.run_deploy_command import RunDeployCommand
from strata.commands.deploy.show_deploy_command import ShowDeployCommand
from strata.commands.deploy.status_deploy_command import StatusDeployCommand


@click.group(name="deploy", help="Deploy platform using provisioners.")
def deploy():
    """Deploy command group."""
    pass


@deploy.command(name="run", help="Run the deploy pipeline for a deployment definition.")
@click_file
@click_work_path
@click.option(
    "--stage",
    default=None,
    metavar="NAME",
    help="Limit execution to a specific deployment stage by name.",
)
@click.option(
    "--scope",
    default=None,
    metavar="LABEL",
    help="Run only deployment stages whose scope field matches this label.",
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
    scope: Optional[str] = None,
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
        scope=scope,
        force=force,
        dry_run=dry_run,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deploy.command(name="destroy", help="Tear down provisioned infrastructure for a deployment definition.")
@click_file
@click_work_path
@click.option(
    "--stage",
    default=None,
    metavar="NAME",
    help="Limit destruction to a specific deployment stage by name.",
)
@click.option(
    "--scope",
    default=None,
    metavar="LABEL",
    help="Destroy only deployment stages whose scope field matches this label.",
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
    scope: Optional[str] = None,
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
        scope=scope,
        force=force,
        dry_run=dry_run,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deploy.command(
    name="show", help="Show resolved deployment configuration: remote versions, workspace, and environment."
)
@click_file
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_show(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Show resolved deployment configuration."""
    command = ShowDeployCommand(
        file=file,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deploy.command(name="status", help="Show deployment status: live Terraform outputs or saved plan details.")
@click_file
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


@deploy.command(name="list", help="List deployment manifests with metadata for CI matrix generation.")
@click.option(
    "--path",
    "-p",
    default=None,
    metavar="DIR",
    help="Directory to scan for deployment manifests (default: current directory).",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_list(
    path: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """List deployment manifests with metadata."""
    command = ListDeployCommand(
        path=path,
        work_path=work_path,
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
    envvar="STRATA_FILE",
    metavar="PATH",
    help="Path to the deployment YAML file. [env: STRATA_FILE]",
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


@deploy.command(name="output", help="Show Terraform outputs for a deployment (cached, live, or stored artifacts.)")
@click_file
@click_work_path
@click.option(
    "--stage",
    default=None,
    metavar="NAME",
    help="Limit output to a specific deployment stage.",
)
@click.option(
    "--key",
    default=None,
    metavar="NAME",
    help="Show only a single output key (useful for scripting).",
)
@click.option(
    "--refresh",
    is_flag=True,
    default=False,
    help="Fetch outputs live from the backend and update the cache.",
)
@click.option(
    "--version",
    default=None,
    metavar="VERSION",
    help="Show stored output artifacts for a specific version tag.",
)
@click.option(
    "--all-versions",
    is_flag=True,
    default=False,
    help="Show stored output artifacts for every version found.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_output(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
    key: Optional[str] = None,
    refresh: bool = False,
    version: Optional[str] = None,
    all_versions: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Show Terraform outputs from cache, live backend, or stored artifacts."""
    command = OutputDeployCommand(
        file=file,
        work_path=work_path,
        stage=stage,
        key=key,
        refresh=refresh,
        version=version,
        all_versions=all_versions,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


# ---------------------------------------------------------------------------
# deploy lock subgroup
# ---------------------------------------------------------------------------


@deploy.group(name="lock", help="Manage deployment state locks.")
def deploy_lock():
    """Lock management subgroup."""
    pass


@deploy_lock.command(name="status", help="Show the current lock state for a deployment.")
@click_file
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_lock_status(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Show current lock state."""
    command = LockStatusCommand(
        file=file,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deploy_lock.command(name="release", help="Release the state lock for a deployment.")
@click_file
@click_work_path
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Release the lock even if held by a different user or host.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_lock_release(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    force: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Release the current deployment lock."""
    command = LockReleaseCommand(
        file=file,
        work_path=work_path,
        force=force,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deploy_lock.command(name="history", help="Show recent lock history for a deployment.")
@click_file
@click_work_path
@click.option(
    "--last",
    default=10,
    show_default=True,
    type=click.IntRange(1, 100),
    help="Number of recent lock events to show.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_lock_history(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    last: int = 10,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Show recent lock history."""
    command = LockHistoryCommand(
        file=file,
        work_path=work_path,
        last=last,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
