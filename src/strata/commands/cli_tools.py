"""Click CLI wiring for the ``tools`` command group."""

from typing import Optional

import click

from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.commands.tools.check_tools_command import CheckToolsCommand
from strata.commands.tools.install_tools_command import InstallToolsCommand
from strata.commands.tools.status_tools_command import StatusToolsCommand


@click.group(name="tools", help="Manage and inspect external tool integrations.")
def tools_group():
    """Tools command group."""
    pass


@tools_group.command(name="status", help="List all known integrations and their availability.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
@click.option(
    "--deployment",
    "deployment_file",
    default=None,
    metavar="FILE",
    help="Deployment file to derive required/optional integration context.",
)
@click.option(
    "--required",
    "filter_required",
    is_flag=True,
    default=False,
    help="Show only integrations required by the deployment.",
)
@click.option(
    "--optional",
    "filter_optional",
    is_flag=True,
    default=False,
    help="Show only optional integrations referenced by the deployment.",
)
@click.option(
    "--available", "filter_available", is_flag=True, default=False, help="Show only available (installed) integrations."
)
@click.option(
    "--missing",
    "filter_missing",
    is_flag=True,
    default=False,
    help="Show only unavailable integrations. Exits with code 3 if any required ones are missing.",
)
def tools_status(
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
    deployment_file: Optional[str] = None,
    filter_required: bool = False,
    filter_optional: bool = False,
    filter_available: bool = False,
    filter_missing: bool = False,
) -> None:
    command = StatusToolsCommand(
        deployment_file=deployment_file,
        filter_required=filter_required,
        filter_optional=filter_optional,
        filter_available=filter_available,
        filter_missing=filter_missing,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@tools_group.command(name="check", help="Deep-check a single integration by name (e.g. terraform, docker).")
@click.argument("name")
@click_work_path
@click_output_verbose
@click_output_quiet
def tools_check(
    name: str,
    work_path: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = CheckToolsCommand(name=name, work_path=work_path, verbose=verbose, quiet=quiet)
    success = command.execute()
    handle_command_exit(command, success)


@tools_group.command(name="install", help="Show download URL, env vars, and auth methods for an integration.")
@click.argument("name")
@click.option(
    "--env-file",
    default=None,
    metavar="PATH",
    help="Write an env-var template file to PATH (commented, not executable).",
)
@click_work_path
@click_output_verbose
@click_output_quiet
def tools_install(
    name: str,
    env_file: Optional[str] = None,
    work_path: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = InstallToolsCommand(
        name=name,
        env_file=env_file,
        work_path=work_path,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
