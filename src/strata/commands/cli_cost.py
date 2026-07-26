"""Click CLI wiring for the cost command group."""

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
from strata.commands.cost.diff_cost_command import DiffCostCommand
from strata.commands.cost.history_cost_command import HistoryCostCommand
from strata.commands.cost.show_cost_command import ShowCostCommand


@click.group(name="cost", help="Cost estimation and visibility.")
def cost_group():
    """Cost command group."""
    pass


@cost_group.command(
    name="show",
    help="Show cost estimate for a deployment (uses Infracost on terraform artifacts).",
)
@click_file
@click.option(
    "--currency",
    default=None,
    metavar="CODE",
    help="ISO 4217 currency code (e.g. EUR, USD, GBP). Defaults to Infracost default (USD).",
)
@click.option(
    "--provisioner",
    default=None,
    metavar="NAME",
    help="Limit cost estimation to a specific terraform provisioner by name.",
)
@click.option(
    "--refresh",
    is_flag=True,
    default=False,
    help="Bypass local cache and force a fresh estimate from Infracost.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def cost_show(
    file: Optional[str] = None,
    currency: Optional[str] = None,
    provisioner: Optional[str] = None,
    refresh: bool = False,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Show cost estimate for a deployment."""
    command = ShowCostCommand(
        file=file,
        work_path=work_path,
        currency=currency,
        provisioner=provisioner,
        force_refresh=refresh,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@cost_group.command(
    name="diff",
    help="Show cost diff between current state and a terraform plan.",
)
@click_file
@click.option(
    "--plan-file",
    required=True,
    metavar="PATH",
    help=(
        "Path to terraform plan JSON file. "
        "Generate with: terraform plan -out=plan.tfplan && "
        "terraform show -json plan.tfplan > plan.json"
    ),
)
@click.option(
    "--currency",
    default=None,
    metavar="CODE",
    help="ISO 4217 currency code (e.g. EUR, USD, GBP).",
)
@click.option(
    "--provisioner",
    default=None,
    metavar="NAME",
    help="Limit to a specific terraform provisioner by name.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def cost_diff(
    file: Optional[str] = None,
    plan_file: Optional[str] = None,
    currency: Optional[str] = None,
    provisioner: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Show cost diff for a terraform plan."""
    command = DiffCostCommand(
        file=file,
        work_path=work_path,
        plan_file=plan_file,
        currency=currency,
        provisioner=provisioner,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@cost_group.command(
    name="history",
    help="Show historical cost snapshots for a deployment.",
)
@click_file
@click.option(
    "--last",
    default=10,
    type=int,
    metavar="N",
    help="Number of most-recent snapshots to show (default: 10).",
)
@click.option(
    "--ai",
    "ai",
    is_flag=True,
    default=False,
    help="Analyse cost history with AI: identify trends, explain spikes, suggest optimisations.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def cost_history(
    file: Optional[str] = None,
    last: int = 10,
    ai: bool = False,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Show historical cost snapshots for a deployment."""
    command = HistoryCostCommand(
        file=file,
        work_path=work_path,
        last=last,
        ai=ai,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
