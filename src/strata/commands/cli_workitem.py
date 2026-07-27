"""Click CLI wiring for the workitem command group — ADR-0057."""

from typing import Optional

import click

from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.commands.workitem.approve_workitem_command import ApproveWorkItemCommand
from strata.commands.workitem.cancel_workitem_command import CancelWorkItemCommand
from strata.commands.workitem.complete_workitem_command import CompleteWorkItemCommand
from strata.commands.workitem.expire_workitem_command import ExpireWorkItemCommand
from strata.commands.workitem.list_workitem_command import ListWorkItemCommand
from strata.commands.workitem.reject_workitem_command import RejectWorkItemCommand
from strata.commands.workitem.show_workitem_command import ShowWorkItemCommand


@click.group(name="workitem", help="Manage deployment workflow hand-off gates and approvals.")
def workitem_group() -> None:
    """Deployment workflow orchestration — work items and hand-off gates."""


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@workitem_group.command(name="list", help="List work items.")
@click.option(
    "--type", "type_filter", default=None, metavar="TYPE", help="Filter by type (e.g. approval, cost_review)."
)
@click.option(
    "--status",
    "status_filter",
    default=None,
    metavar="STATUS",
    help="Filter by status (pending, approved, rejected, …). Default: all.",
)
@click.option("--deployment", "-f", default=None, metavar="FILE", help="Filter by deployment file path.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def list_command(
    type_filter: Optional[str] = None,
    status_filter: Optional[str] = None,
    deployment: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """List pending and historical work items for this workspace."""
    command = ListWorkItemCommand(
        type=type_filter,
        status=status_filter,
        deployment=deployment,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@workitem_group.command(name="show", help="Show details of a work item.")
@click.argument("item_id")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def show_command(
    item_id: str,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Show full details of a work item by ID."""
    command = ShowWorkItemCommand(
        item_id=item_id,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


# ---------------------------------------------------------------------------
# approve
# ---------------------------------------------------------------------------


@workitem_group.command(name="approve", help="Approve a pending work item.")
@click.argument("item_id")
@click.option("--note", default=None, metavar="TEXT", help="Optional approval note.")
@click.option(
    "--as",
    "as_identity",
    default=None,
    metavar="IDENTITY",
    help="Assert a specific identity (logged as [asserted]). Defaults to resolved operator identity.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def approve_command(
    item_id: str,
    note: Optional[str] = None,
    as_identity: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Approve a pending work item, unblocking the paused deployment."""
    command = ApproveWorkItemCommand(
        item_id=item_id,
        note=note,
        as_identity=as_identity,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


# ---------------------------------------------------------------------------
# reject
# ---------------------------------------------------------------------------


@workitem_group.command(name="reject", help="Reject a pending work item.")
@click.argument("item_id")
@click.option("--reason", default=None, metavar="TEXT", help="Reason for rejection.")
@click.option(
    "--as", "as_identity", default=None, metavar="IDENTITY", help="Assert a specific identity (logged as [asserted])."
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def reject_command(
    item_id: str,
    reason: Optional[str] = None,
    as_identity: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Reject a pending work item, blocking the deployment."""
    command = RejectWorkItemCommand(
        item_id=item_id,
        reason=reason,
        as_identity=as_identity,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


@workitem_group.command(name="cancel", help="Cancel a pending work item.")
@click.argument("item_id")
@click.option("--reason", default=None, metavar="TEXT", help="Reason for cancellation.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def cancel_command(
    item_id: str,
    reason: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Cancel a pending work item."""
    command = CancelWorkItemCommand(
        item_id=item_id,
        reason=reason,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


# ---------------------------------------------------------------------------
# expire
# ---------------------------------------------------------------------------


@workitem_group.command(name="expire", help="Expire stale work items whose timeout has passed.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def expire_command(
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Mark pending work items as expired when their timeout_minutes has elapsed."""
    command = ExpireWorkItemCommand(
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------


@workitem_group.command(name="complete", help="Complete a pending work item (e.g. after manual verify gate).")
@click.argument("item_id")
@click.option("--comment", default=None, metavar="TEXT", help="Optional completion comment.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def complete_command(
    item_id: str,
    comment: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Mark a verify gate work item as completed, unblocking the paused deployment."""
    command = CompleteWorkItemCommand(
        item_id=item_id,
        comment=comment,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
