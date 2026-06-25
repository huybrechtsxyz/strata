"""Click CLI wiring for the audit command group."""

import json
from typing import Optional

import click

from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)


@click.group(
    name="audit",
    help="Deployment audit trail — query deploy-logs and manage audit evidence.",
)
def audit_group():
    """Audit command group."""
    pass


# ==============================================================================
# strata audit changes
# ==============================================================================


@audit_group.command(name="changes", help="List recent deployment executions from the deploy-log.")
@click.option(
    "--last",
    default=10,
    show_default=True,
    type=int,
    help="Maximum number of entries to show.",
)
@click.option(
    "--since",
    default=None,
    type=str,
    help="Show only entries since ISO 8601 timestamp (e.g. 2024-01-15T00:00:00+00:00).",
)
@click.option(
    "--stage",
    default=None,
    type=str,
    help="Filter to entries that executed a specific stage name.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
@click.pass_context
def audit_changes(
    ctx: click.Context,
    last: int,
    since: Optional[str],
    stage: Optional[str],
    work_path: str,
    output: Optional[str],
    verbose: bool,
    quiet: bool,
) -> None:
    """Query deploy-log entries for the current workspace."""
    from pathlib import Path

    from strata.controllers.audit_controller import AuditController
    from strata.utils.config import SOLUTION_DEPLOY_LOG_DIR, SOLUTION_DIR

    wp = Path(work_path)
    base_path = wp / SOLUTION_DIR / SOLUTION_DEPLOY_LOG_DIR

    controller = AuditController(work_path=wp)
    entries = controller.query_deploy_logs(
        base_path=base_path,
        since=since,
        stage=stage,
        last=last,
    )

    if output == "json":
        data = [e.model_dump(exclude_none=True) for e in entries]
        click.echo(json.dumps(data, indent=2, default=str))
    elif output == "ndjson":
        for entry in entries:
            click.echo(json.dumps(entry.model_dump(exclude_none=True), default=str))
    else:
        # Console table output
        if not entries:
            if not quiet:
                click.echo("No deploy-log entries found.")
            ctx.exit(0)
            return

        if not quiet:
            click.echo(f"{'Timestamp':<28} {'Deployment':<20} {'Success':<9} {'Duration':<10} {'Stages'}")
            click.echo("─" * 90)

        for entry in entries:
            status = "✓" if entry.success else "✗"
            duration = f"{entry.duration_seconds:.1f}s"
            stage_count = str(len(entry.stages))
            click.echo(f"{entry.timestamp:<28} {entry.deployment:<20} {status:<9} {duration:<10} {stage_count}")

        if not quiet:
            click.echo(f"\n{len(entries)} entries shown.")

    handle_command_exit("audit changes", success=True)
