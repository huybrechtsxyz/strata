"""Click CLI wiring for the audit command group."""

from typing import Optional

import click

from strata.commands.audit.changes_audit_command import ChangesAuditCommand
from strata.commands.audit.diff_audit_command import DiffAuditCommand
from strata.commands.audit.export_audit_command import ExportAuditCommand
from strata.commands.audit.resend_audit_command import ResendAuditCommand
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
@click.option(
    "--ai",
    "ai",
    is_flag=True,
    default=False,
    help="Summarise deployment history with AI: trends, anomalies, recurring failures, recommendations.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def audit_changes(
    last: int,
    since: Optional[str],
    stage: Optional[str],
    ai: bool,
    work_path: Optional[str],
    output: Optional[str],
    verbose: bool,
    quiet: bool,
) -> None:
    """Query deploy-log entries for the current workspace."""
    command = ChangesAuditCommand(
        last=last,
        since=since,
        stage=stage,
        ai=ai,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


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
def audit_resend(
    last: Optional[int],
    since: Optional[str],
    work_path: Optional[str],
    output: Optional[str],
    verbose: bool,
    quiet: bool,
) -> None:
    """Re-forward local deploy-log records to configured audit sinks."""
    command = ResendAuditCommand(
        last=last,
        since=since,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


# ==============================================================================
# strata audit export
# ==============================================================================


@audit_group.command(name="export", help="Export deploy-log entries to a file.")
@click.option(
    "--last",
    default=None,
    type=int,
    help="Export only the last N entries.",
)
@click.option(
    "--since",
    default=None,
    type=str,
    help="Export only entries since ISO 8601 timestamp.",
)
@click.option(
    "--format",
    "export_format",
    default="json",
    type=click.Choice(["json", "ndjson"], case_sensitive=False),
    help="Export format.",
)
@click.option(
    "--include-manifests",
    is_flag=True,
    default=False,
    help="Include deployment manifests in the export.",
)
@click.option(
    "--siem",
    "siem_name",
    default=None,
    type=str,
    metavar="NAME",
    help="Forward entries to a configured SIEM integration by name (e.g. splunk_hec, sentinel).",
)
@click.option(
    "--out",
    "out_file",
    default=None,
    type=click.Path(),
    help="Output file path (defaults to stdout).",
)
@click_work_path
@click_output_quiet
def audit_export(
    last: Optional[int],
    since: Optional[str],
    export_format: str,
    include_manifests: bool,
    siem_name: Optional[str],
    out_file: Optional[str],
    work_path: Optional[str],
    quiet: bool,
) -> None:
    """Export deploy-log entries to JSON or NDJSON."""
    command = ExportAuditCommand(
        last=last,
        since=since,
        export_format=export_format,
        include_manifests=include_manifests,
        siem_name=siem_name,
        out_file=out_file,
        work_path=work_path,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


# ==============================================================================
# strata audit diff
# ==============================================================================


@audit_group.command(
    name="diff",
    help="Show configuration changes between two deployment executions.",
    epilog=(
        "FROM_ID and TO_ID are execution IDs from 'strata audit changes'.\n\n"
        "Exit codes:\n"
        "  0  no changes between the two deployments\n"
        "  1  system error (git unavailable, ID not found)\n"
        "  3  changes detected (use in CI to flag drift between deployments)"
    ),
)
@click.argument("from_id", metavar="FROM_ID")
@click.argument("to_id", metavar="TO_ID")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def audit_diff(
    from_id: str,
    to_id: str,
    work_path: Optional[str],
    output: Optional[str],
    verbose: bool,
    quiet: bool,
) -> None:
    """Show git diff of YAML configuration between FROM_ID and TO_ID deployments."""
    command = DiffAuditCommand(
        from_id=from_id,
        to_id=to_id,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
