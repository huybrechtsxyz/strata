"""Click CLI wiring for the ``env`` command group."""

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
from strata.commands.env.drift_env_command import DriftEnvCommand
from strata.commands.env.show_env_command import ShowEnvCommand
from strata.commands.env.state_env_command import StateEnvCommand


@click.group(name="env", help="Inspect environment configuration and state.")
def env_group() -> None:
    """Env command group."""


@env_group.command(name="show", help="Show the full resolved environment for a deployment.")
@click_file
@click_work_path
@click.option(
    "--stage",
    default=None,
    metavar="NAME",
    help="Filter secrets visibility to a specific stage's allowlist.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def env_show(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
) -> None:
    """Show the resolved environment: meta, properties, values, overrides, stages."""
    command = ShowEnvCommand(
        file=file,
        work_path=work_path,
        stage=stage,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@env_group.command(name="state", help="Show the live infrastructure state for a deployment.")
@click_file
@click_work_path
@click.option(
    "--stage",
    default=None,
    metavar="NAME",
    help="Query only a single stage (default: all).",
)
@click.option(
    "--offline",
    is_flag=True,
    default=False,
    help="Use cached data only — do not contact remote backends.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def env_state(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
    offline: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
) -> None:
    """Show live infrastructure state: resources, outputs, serial, cache freshness."""
    command = StateEnvCommand(
        file=file,
        work_path=work_path,
        stage=stage,
        offline=offline,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@env_group.command(name="drift", help="Detect drift between desired config and live infrastructure.")
@click_file
@click_work_path
@click.option(
    "--stage",
    default=None,
    metavar="NAME",
    help="Check only a single stage (default: all).",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def env_drift(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
) -> None:
    """Run terraform plan per stage to detect configuration drift."""
    command = DriftEnvCommand(
        file=file,
        work_path=work_path,
        stage=stage,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
