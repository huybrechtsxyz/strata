"""Click CLI wiring for the ``policy`` command group."""

from typing import Optional

import click

from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.commands.policies.list_policy_command import ListPolicyCommand


@click.group(name="policy", help="Inspect and evaluate deployment policies.")
def policy_group() -> None:
    """Policy command group."""


@policy_group.command(name="list")
@click.option(
    "--file",
    "-f",
    default=None,
    metavar="PATH",
    help=(
        "Optional deployment YAML file. When given, annotates the output with "
        "the lifecycle phases that deployment can trigger."
    ),
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def list_policy_command(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """List policies declared in configuration.spec.policies.

    Loads the active configuration from the workspace's active profile.
    Optionally pass a deployment file to annotate which lifecycle phases
    that deployment can trigger:

    \b
        strata policy list
        strata policy list -f config/deploy-prd.yaml
        strata policy list --output json
    """
    command = ListPolicyCommand(
        file=file,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
