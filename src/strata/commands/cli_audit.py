"""Click CLI wiring for the audit command group."""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import click

from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.utils.system import generate_uuid


def _make_envelope(
    command: str, success: bool, data: Any, messages: Optional[List[str]] = None, errors: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Build the standard JSON response envelope used by all strata commands."""
    return {
        "success": success,
        "command": command,
        "execution_id": generate_uuid(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
        "messages": messages or [],
        "errors": errors or [],
    }


def _forward_entries_to_siem(
    siem_name: str,
    entries: list,
    work_path,
    quiet: bool,
) -> bool:
    """Forward deploy-log entries to a named SIEM integration.

    Loads the integration from configuration.yaml, creates an instance via
    IntegrationFactory, and calls send_batch.  Returns True on success.
    """
    from pathlib import Path as _Path

    from strata.integrations.capabilities import ISiemSink
    from strata.integrations.factory import IntegrationFactory
    from strata.services.configuration_service import ConfigurationService
    from strata.utils.config import SOLUTION_DIR

    wp = _Path(str(work_path))

    # Attempt to load integration model from configuration
    integration_model = None
    for cfg_path in (wp / SOLUTION_DIR).rglob("*.yaml"):
        try:
            svc = ConfigurationService.load(str(cfg_path), validate=False)
            if svc.model and svc.model.spec:
                integrations = getattr(svc.model.spec, "integrations", []) or []
                for m in integrations:
                    if m.name == siem_name:
                        integration_model = m
                        break
            if integration_model:
                break
        except Exception:
            continue

    if not integration_model:
        click.echo(
            f"SIEM integration '{siem_name}' not found in configuration. "
            "Ensure it is declared under spec.integrations in a configuration YAML.",
            err=True,
        )
        return False

    try:
        instance = IntegrationFactory.create(integration_model)
    except Exception as exc:
        click.echo(f"Failed to create SIEM integration '{siem_name}': {exc}", err=True)
        return False

    if not isinstance(instance, ISiemSink):
        click.echo(
            f"Integration '{siem_name}' (type: {integration_model.type}) does not support SIEM forwarding.",
            err=True,
        )
        return False

    payloads = [e.model_dump(exclude_none=True) for e in entries]
    ok = instance.send_batch("deploy_audit", payloads)

    if not quiet:
        if ok:
            click.echo(f"Forwarded {len(payloads)} entries to SIEM '{siem_name}'.")
        else:
            click.echo(f"SIEM forwarding to '{siem_name}' failed (partial or complete). Check logs.", err=True)

    return ok


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
        entries_data = [e.model_dump(exclude_none=True) for e in entries]
        envelope = _make_envelope("audit_changes", True, {"entries": entries_data, "count": len(entries_data)})
        click.echo(json.dumps(envelope, indent=2, default=str))
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
        envelope = _make_envelope("audit_resend", failed == 0, {"sent": sent, "failed": failed})
        click.echo(json.dumps(envelope, indent=2, default=str))
    else:
        if not quiet:
            click.echo(f"Resend complete: {sent} sent, {failed} failed.")

    handle_command_exit("audit resend", success=(failed == 0))


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
@click.pass_context
def audit_export(
    ctx: click.Context,
    last: Optional[int],
    since: Optional[str],
    export_format: str,
    include_manifests: bool,
    siem_name: Optional[str],
    out_file: Optional[str],
    work_path: str,
    quiet: bool,
) -> None:
    """Export deploy-log entries to JSON or NDJSON."""
    from pathlib import Path

    from strata.controllers.audit_controller import AuditController
    from strata.utils.config import SOLUTION_DEPLOY_LOG_DIR, SOLUTION_DEPLOYMENTS_DIR, SOLUTION_DIR

    wp = Path(work_path)
    base_path = wp / SOLUTION_DIR / SOLUTION_DEPLOY_LOG_DIR

    controller = AuditController(work_path=wp)
    entries = controller.query_deploy_logs(
        base_path=base_path,
        since=since,
        last=last,
    )

    # Optionally bundle deployment manifests alongside deploy-logs
    manifest_data = []
    if include_manifests:
        from strata.services.deployment_manifest_service import DeploymentManifestService

        manifest_base = wp / SOLUTION_DIR / SOLUTION_DEPLOYMENTS_DIR
        if manifest_base.exists():
            manifest_files = DeploymentManifestService.list_manifests(manifest_base)
            if last:
                manifest_files = manifest_files[:last]
            for mf in manifest_files:
                try:
                    manifest_data.append(json.loads(mf.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, OSError):
                    pass

    if export_format == "ndjson":
        lines = [json.dumps(e.model_dump(exclude_none=True), default=str) for e in entries]
        if include_manifests:
            for md in manifest_data:
                lines.append(json.dumps(md, default=str))
        content = "\n".join(lines) + ("\n" if lines else "")
    else:
        log_data = [e.model_dump(exclude_none=True) for e in entries]
        if include_manifests:
            # Wrapped format when manifests are bundled
            content = (
                json.dumps(
                    {"deploy_logs": log_data, "manifests": manifest_data},
                    indent=2,
                    default=str,
                )
                + "\n"
            )
        else:
            # Backward-compatible flat array
            content = json.dumps(log_data, indent=2, default=str) + "\n"

    if out_file:
        out_path = Path(out_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        if not quiet:
            click.echo(f"Exported {len(entries)} entries to {out_path}")
    elif not siem_name:
        # Only print to stdout when not forwarding to SIEM
        click.echo(content, nl=False)

    # --- Optional SIEM forwarding ---
    siem_success = True
    if siem_name:
        siem_success = _forward_entries_to_siem(siem_name, entries, wp, quiet)

    handle_command_exit("audit export", success=siem_success)
