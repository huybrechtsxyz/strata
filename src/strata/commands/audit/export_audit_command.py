"""Command to export deploy-log entries to a file or SIEM."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import click

from strata.commands.schemas.schema_base_command import SchemaBaseCommand
from strata.controllers.audit_controller import AuditController
from strata.utils.config import get_deploy_log_dir


class ExportAuditCommand(SchemaBaseCommand):
    """Export deploy-log entries to JSON, NDJSON, or a SIEM integration."""

    OPERATION = "audit_export"

    def __init__(
        self,
        last: Optional[int] = None,
        since: Optional[str] = None,
        export_format: str = "json",
        include_manifests: bool = False,
        siem_name: Optional[str] = None,
        out_file: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._last = last
        self._since = since
        self._export_format = export_format
        self._include_manifests = include_manifests
        self._siem_name = siem_name
        self._out_file = out_file

        self._entries: List[Any] = []
        self._manifest_data: List[Dict[str, Any]] = []
        self._content: str = ""
        self._siem_success: bool = True

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    @classmethod
    def show_console_header(cls, work_path: Optional[str] = None) -> None:
        """Suppress the standard base-command chrome."""

    @classmethod
    def show_console_footer(cls) -> None:
        """Suppress the standard base-command chrome."""

    def _execute(self) -> bool:
        from pathlib import Path

        base_path = get_deploy_log_dir(self._work_path)
        controller = AuditController(work_path=self._work_path)
        self._entries = controller.query_deploy_logs(
            base_path=base_path,
            since=self._since,
            last=self._last,
        )

        if self._include_manifests:
            from strata.controllers.solution_controller import SolutionController
            from strata.services.deployment_manifest_service import DeploymentManifestService

            manifest_base = SolutionController.get_deployments_dir(self._work_path)
            if manifest_base.exists():
                manifest_files = DeploymentManifestService.list_manifests(manifest_base)
                if self._last:
                    manifest_files = manifest_files[: self._last]
                for mf in manifest_files:
                    try:
                        self._manifest_data.append(json.loads(mf.read_text(encoding="utf-8")))
                    except (json.JSONDecodeError, OSError):
                        pass

        # Build export content
        if self._export_format == "ndjson":
            lines = [json.dumps(e.model_dump(exclude_none=True), default=str) for e in self._entries]
            if self._include_manifests:
                for md in self._manifest_data:
                    lines.append(json.dumps(md, default=str))
            self._content = "\n".join(lines) + ("\n" if lines else "")
        else:
            log_data = [e.model_dump(exclude_none=True) for e in self._entries]
            if self._include_manifests:
                self._content = (
                    json.dumps(
                        {"deploy_logs": log_data, "manifests": self._manifest_data},
                        indent=2,
                        default=str,
                    )
                    + "\n"
                )
            else:
                self._content = json.dumps(log_data, indent=2, default=str) + "\n"

        # Write to file if requested
        if self._out_file:
            out_path = Path(self._out_file)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(self._content, encoding="utf-8")

        # SIEM forwarding
        if self._siem_name:
            self._siem_success = self._forward_to_siem()

        self._output_data = {
            "entry_count": len(self._entries),
            "manifest_count": len(self._manifest_data),
            "out_file": self._out_file,
            "siem": self._siem_name,
        }
        return self._siem_success

    def _after_execute(self) -> bool:
        if self._is_console_output() or not self._output_format:
            if self._out_file:
                click.echo(f"Exported {len(self._entries)} entries to {self._out_file}")
            elif not self._siem_name:
                click.echo(self._content, nl=False)
        return super()._after_execute()

    # ------------------------------------------------------------------
    # SIEM helpers (extracted from the original module-level function)
    # ------------------------------------------------------------------

    def _forward_to_siem(self) -> bool:
        """Forward exported deploy-log entries to a configured audit sink (ADR-0066 gap 1 fix).

        ``--siem <name>`` must reference an integration declared in an *enabled*
        ``spec.audit.sinks`` entry — no more resolving an arbitrary integration by
        scanning ``.strata/*.yaml`` directly. This closes the two concrete
        inconsistencies with every other forwarding path (``deploy run``, ``resend``,
        work items): the policy gate is now consulted, and entries are wrapped in the
        same CloudEvents 1.0 + ECS envelope via ``AuditController._build_envelope()``
        rather than sent as raw, un-enveloped payloads.

        Entries are grouped by event type before forwarding (ADR-0066 gap B) — the
        exported deploy-log entries are no longer exclusively ``deploy run`` records
        (``deploy destroy`` writes them too), so each record's actual event type
        (``deployment.completed`` vs ``deployment.destroyed``, from its ``command``
        field) is looked up individually rather than assumed for the whole batch. The
        gate and the sink's own ``events`` filter are each consulted per group.
        """
        from collections import defaultdict

        from strata.controllers.audit_controller import (
            DEPLOY_LOG_EVENT_TYPE_BY_COMMAND,
            AuditController,
        )
        from strata.models.audit_config_model import AuditConfigModel
        from strata.models.capabilities import ISiemSink
        from strata.services.configuration_service import ConfigurationService
        from strata.services.integration_service import IntegrationService
        from strata.utils.config import get_configuration_path

        siem_name = self._siem_name
        if not siem_name:
            return True

        audit_config: Optional[AuditConfigModel] = None
        try:
            config_service = ConfigurationService.load(str(get_configuration_path(self._work_path)), validate=False)
            if config_service.model and config_service.model.spec and config_service.model.spec.audit:
                audit_config = config_service.model.spec.audit
        except Exception as exc:
            self._errors.append(f"Failed to load configuration for --siem: {exc}")
            return False

        audit_config = audit_config or AuditConfigModel()

        sink = next((s for s in audit_config.sinks if str(s.integration) == siem_name), None)
        if sink is None:
            self._errors.append(
                f"'{siem_name}' is not declared as a sink in spec.audit.sinks. "
                "Add it there before using --siem, e.g.:\n\n"
                "  audit:\n"
                "    sinks:\n"
                f"      - name: {siem_name}\n"
                f"        integration: {siem_name}"
            )
            return False
        if not sink.enabled:
            self._errors.append(f"Sink '{sink.name}' (integration '{siem_name}') is disabled.")
            return False

        try:
            svc = IntegrationService.get_instance()
            if not svc.is_initialized():
                svc.initialize_integrations()
            ok, errors = svc.validate_required_integrations(capabilities={ISiemSink})
            if not ok:
                self.logger.warning("audit_export_required_integration_unavailable", errors=errors)
            instance = svc.get_integration(siem_name)
        except Exception as exc:
            self._errors.append(f"Failed to resolve integration '{siem_name}': {exc}")
            return False

        if instance is None:
            self._errors.append(f"Integration '{siem_name}' not found in configuration.")
            return False
        if not isinstance(instance, ISiemSink):
            self._errors.append(f"Integration '{siem_name}' does not support SIEM forwarding.")
            return False

        groups: Dict[str, List[Any]] = defaultdict(list)
        for entry in self._entries:
            event_type = DEPLOY_LOG_EVENT_TYPE_BY_COMMAND.get(entry.command, "deployment.completed")
            groups[event_type].append(entry)
        if not groups:
            # No entries (e.g. --last with nothing to export) — still send one
            # "deployment.completed" batch (empty), matching the pre-grouping
            # behaviour of always calling send_batch once regardless of count.
            groups["deployment.completed"] = []

        ok = True
        forwarded_count = 0
        for event_type, group_entries in groups.items():
            if not audit_config.policy.is_enabled(event_type):
                if not self._output_quiet:
                    click.echo(
                        f"Skipped: spec.audit.policy.events.{event_type} is disabled — "
                        f"{len(group_entries)} entries not forwarded.",
                        err=True,
                    )
                continue
            if sink.events is not None and event_type not in sink.events:
                if not self._output_quiet:
                    click.echo(
                        f"Skipped: sink '{sink.name}' does not include '{event_type}' in its event filter — "
                        f"{len(group_entries)} entries not forwarded.",
                        err=True,
                    )
                continue

            envelopes = [
                AuditController._build_envelope(event_type, e.model_dump(exclude_none=True)) for e in group_entries
            ]
            group_ok = instance.send_batch(event_type, envelopes)
            ok = ok and group_ok
            if group_ok:
                forwarded_count += len(envelopes)

        if not self._output_quiet:
            if forwarded_count:
                click.echo(f"Forwarded {forwarded_count} entries to SIEM '{siem_name}'.")
            if not ok:
                click.echo(f"SIEM forwarding to '{siem_name}' failed (partial or complete). Check logs.", err=True)

        # Also forward sbom-ignore rules as a separate batch — kept in sync with the
        # deploy-log batch above (ADR-0066): same resolved integration instance (no
        # redundant re-resolution via the old .strata/*.yaml-scanning path), same
        # CloudEvents 1.0 + ECS envelope, and failures now surface into self._errors
        # and the command's exit code rather than being swallowed as best-effort-only.
        # "sbom_ignore_rules" is outside the closed policy-gate event set by design —
        # it isn't a deploy audit event, so the gate never applies to it (consistent
        # with AuditPolicyModel.is_enabled()'s "unrecognised type is never gated off").
        from strata.builders.sbom.deps_collector import DependencyFileCollector

        ignore_cfg = DependencyFileCollector.load_ignore_config(self._work_path)
        ignore_evidence = ignore_cfg.model_dump(exclude_none=True)
        # ignore_cfg's fields are all list-typed with a `[]` default (never None), so
        # model_dump(exclude_none=True) is always a non-empty dict of empty lists even
        # when .strata/sbom-ignore.yaml doesn't exist — `if ignore_evidence:` alone would
        # never actually skip anything. Only forward when at least one rule is declared.
        has_rules = any(ignore_evidence.get(key) for key in ignore_evidence)
        if has_rules:
            sbom_event_type = "sbom_ignore_rules"
            sbom_envelope = AuditController._build_envelope(sbom_event_type, ignore_evidence)
            try:
                sbom_ok = instance.send_batch(sbom_event_type, [sbom_envelope])
            except Exception as exc:
                self._errors.append(f"Failed to forward sbom-ignore rules to '{siem_name}': {exc}")
                sbom_ok = False
            if not sbom_ok:
                self._errors.append(f"SIEM forwarding of sbom-ignore rules to '{siem_name}' failed. Check logs.")
            elif not self._output_quiet:
                click.echo(f"Forwarded sbom-ignore rules to SIEM '{siem_name}'.")
            ok = ok and sbom_ok

        return ok
