"""Click CLI wiring for the ``policy`` command group."""

from typing import Optional, Tuple

import click

from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.commands.policies.check_policy_command import CheckPolicyCommand
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


@policy_group.command(name="check")
@click.option(
    "--file",
    "-f",
    required=True,
    metavar="PATH",
    help="Deployment YAML file to evaluate policies against.",
)
@click.option(
    "--phase",
    "-p",
    multiple=True,
    type=click.Choice(["validate", "build", "plan", "deploy"], case_sensitive=False),
    metavar="PHASE",
    help=("Limit evaluation to specific phase(s). May be repeated: -p validate -p plan. Defaults to all phases."),
)
@click.option(
    "--plan-file",
    default=None,
    metavar="PATH",
    help=(
        "Path to a saved Terraform plan JSON file (*.tfplan.json) for plan-phase policies. "
        "Auto-discovered from the build directory when omitted."
    ),
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
@click.option(
    "--ai",
    "ai",
    is_flag=True,
    default=False,
    help="Run AI explanation of policy violations (requires an ai_agent integration).",
)
def check_policy_command(
    file: str,
    phase: Tuple[str, ...] = (),
    plan_file: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
    ai: bool = False,
) -> None:
    """Evaluate policies against a deployment without running a deploy.

    Loads the active configuration and the given deployment file, then
    evaluates all matching enabled policies for the requested phase(s).
    Missing context (no platform.json, no plan file) is reported as an
    informational note with instructions on what to run next.

    \b
        strata policy check -f config/deploy-prd.yaml
        strata policy check -f config/deploy-prd.yaml -p validate -p plan
        strata policy check -f config/deploy-prd.yaml --plan-file build/infra.tfplan.json
        strata policy check -f config/deploy-prd.yaml --output json
    """
    command = CheckPolicyCommand(
        file=file,
        phase=phase if phase else None,
        plan_file=plan_file,
        ai=ai,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
