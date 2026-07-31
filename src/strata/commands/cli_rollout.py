"""Click CLI wiring for the ``rollout`` (fleet-wide) command group."""

from typing import Optional

import click

from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.commands.rollout.status_rollout_command import StatusRolloutCommand


@click.group(name="rollout", help="Manage fleet-wide, multi-deployment rollouts.")
def rollout_group() -> None:
    """Rollout (fleet-wide) command group."""


@rollout_group.command(
    name="status", help="Scan a directory or the whole workspace and summarize deployment status per manifest."
)
@click.option(
    "--path",
    default=None,
    metavar="DIR",
    help="Scan a directory for deployment manifests and show a summary for each.",
)
@click.option(
    "--all",
    "all_deployments",
    is_flag=True,
    default=False,
    help="Scan the entire workspace for deployment manifests and show a summary for each.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def rollout_status(
    path: Optional[str] = None,
    all_deployments: bool = False,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
) -> None:
    """Scan for deployment manifests and show a one-line status summary per deployment."""
    command = StatusRolloutCommand(
        work_path=work_path,
        path=path,
        all_deployments=all_deployments,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
