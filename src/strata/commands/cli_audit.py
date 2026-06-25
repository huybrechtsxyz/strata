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


# ==============================================================================
# strata audit resend
# ==============================================================================


@audit_group.command(name="resend", help="Re-forward deploy-log entries to configured audit sinks.")
@click.option(
    "--last",
    default=None,
    type=int,
    help="Resend only the last N entries.",
)
@click.option(
    "--since",
    default=None,
    type=str,
    help="Resend only entries since ISO 8601 timestamp.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
@click.pass_context
def audit_resend(
    ctx: click.Context,
    last: Optional[int],
    since: Optional[str],
    work_path: str,
    output: Optional[str],
    verbose: bool,
    quiet: bool,
) -> None:
    """Re-forward local deploy-log records to configured audit sinks."""
    from pathlib import Path

    from strata.controllers.audit_controller import AuditController
    from strata.models.audit_config_model import AuditConfigModel
    from strata.services.configuration_service import ConfigurationService
    from strata.utils.config import SOLUTION_DEPLOY_LOG_DIR, SOLUTION_DIR

    wp = Path(work_path)
    base_path = wp / SOLUTION_DIR / SOLUTION_DEPLOY_LOG_DIR

    # Try to load audit config from configuration
    audit_config: Optional[AuditConfigModel] = None
    try:
        config_service = ConfigurationService.load(str(wp / SOLUTION_DIR / "configuration.yaml"), validate=False)
        if config_service.model and config_service.model.spec and config_service.model.spec.audit:
            audit_config = config_service.model.spec.audit
    except Exception:
        pass

    if not audit_config or not audit_config.sinks:
        if not quiet:
            click.echo("No audit sinks configured. Add spec.audit.sinks to configuration.yaml.")
        ctx.exit(1)
        return

    controller = AuditController(work_path=wp)
    sent, failed = controller.resend(
        base_path=base_path,
        audit_config=audit_config,
        since=since,
        last=last,
    )

    if output == "json":
        click.echo(json.dumps({"sent": sent, "failed": failed}))
    else:
        if not quiet:
            click.echo(f"Resend complete: {sent} sent, {failed} failed.")

    handle_command_exit("audit resend", success=(failed == 0))
