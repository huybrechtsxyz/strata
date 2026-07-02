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
from strata.commands.envs.doctor_env_command import DoctorEnvCommand
from strata.commands.envs.drift_env_command import DriftEnvCommand
from strata.commands.envs.info_env_command import InfoEnvCommand
from strata.commands.envs.output_env_command import OutputEnvCommand
from strata.commands.envs.show_env_command import ShowEnvCommand
from strata.commands.envs.state_env_command import StateEnvCommand


@click.group(
    name="env",
    help="Inspect environment configuration and state.",
    invoke_without_command=True,
)
@click.pass_context
def env_group(ctx: click.Context) -> None:
    """Env command group. Defaults to 'info' when no subcommand is given."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(env_info)


@env_group.command(name="info", help="Show workspace context: solution, profile, version, work path.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def env_info(
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
) -> None:
    """Show workspace context: solution identity, active profile, strata version, work path."""
    command = InfoEnvCommand(
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@env_group.command(name="output", help="Show live Terraform outputs for a deployment.")
@click_file
@click_work_path
@click.option(
    "--name",
    default=None,
    metavar="NAME",
    help="Print a single output value only.",
)
@click.option(
    "--provisioner",
    default=None,
    metavar="NAME",
    help="Limit to stages that use a specific provisioner (default: all).",
)
@click.option(
    "--raw",
    is_flag=True,
    default=False,
    help="Print bare value with no formatting — requires --name.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help="Emit raw outputs as JSON — bypasses the strata envelope.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def env_output(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    name: Optional[str] = None,
    provisioner: Optional[str] = None,
    raw: bool = False,
    json_output: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
) -> None:
    """Show live Terraform outputs grouped by provisioner."""
    command = OutputEnvCommand(
        file=file,
        work_path=work_path,
        name=name,
        provisioner=provisioner,
        raw=raw,
        json_output=json_output,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


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


@env_group.command(name="doctor", help="Run a workspace health check: runtime, tools, config, and auth.")
@click.option(
    "--file",
    "-f",
    default=None,
    envvar="STRATA_FILE",
    metavar="PATH",
    help="Deployment file — enables requirement-level derivation for tools. [env: STRATA_FILE]",
)
@click_work_path
@click.option(
    "--category",
    default=None,
    type=click.Choice(["runtime", "workspace", "tools", "config", "auth"], case_sensitive=False),
    metavar="NAME",
    help="Run only a specific check category.",
)
@click.option(
    "--deep",
    is_flag=True,
    default=False,
    help="Run slow checks: backend reachability and auth validation.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def env_doctor(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    category: Optional[str] = None,
    deep: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
) -> None:
    """Run a workspace health check across runtime, workspace, tools, config, and auth."""
    command = DoctorEnvCommand(
        file=file,
        work_path=work_path,
        category=category,
        deep=deep,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
