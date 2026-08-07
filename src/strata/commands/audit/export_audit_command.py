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
        from strata.builders.sbom.deps_collector import DependencyFileCollector
        from strata.integrations.factory import IntegrationFactory
        from strata.models.capabilities import ISiemSink

        integration_model = self._find_integration_model(self._siem_name)
        if not integration_model:
            self._errors.append(
                f"SIEM integration '{self._siem_name}' not found in configuration. "
                "Ensure it is declared under spec.integrations in a configuration YAML."
            )
            return False

        try:
            instance = IntegrationFactory.create(integration_model)
        except Exception as exc:
            self._errors.append(f"Failed to create SIEM integration '{self._siem_name}': {exc}")
            return False

        if not isinstance(instance, ISiemSink):
            self._errors.append(
                f"Integration '{self._siem_name}' (type: {integration_model.type}) does not support SIEM forwarding."
            )
            return False

        payloads = [e.model_dump(exclude_none=True) for e in self._entries]
        ok = instance.send_batch("deploy_audit", payloads)
        if not self._output_quiet:
            if ok:
                click.echo(f"Forwarded {len(payloads)} entries to SIEM '{self._siem_name}'.")
            else:
                click.echo(
                    f"SIEM forwarding to '{self._siem_name}' failed (partial or complete). Check logs.", err=True
                )

        # Also forward sbom-ignore rules as a separate batch
        ignore_cfg = DependencyFileCollector.load_ignore_config(self._work_path)
        ignore_evidence = ignore_cfg.model_dump(exclude_none=True)
        if ignore_evidence:
            try:
                integration_model2 = self._find_integration_model(self._siem_name)
                if integration_model2:
                    instance2 = IntegrationFactory.create(integration_model2)
                    if isinstance(instance2, ISiemSink):
                        instance2.send_batch("sbom_ignore_rules", [ignore_evidence])
                        if not self._output_quiet:
                            click.echo(f"Forwarded sbom-ignore rules to SIEM '{self._siem_name}'.")
            except Exception as exc:
                if not self._output_quiet:
                    click.echo(f"Could not forward sbom-ignore rules to SIEM: {exc}", err=True)

        return ok

    def _find_integration_model(self, siem_name: Optional[str]):
        import yaml

        from strata.models.configuration_model import ConfigurationModel
        from strata.utils.config import get_strata_dir

        if not siem_name:
            return None
        # Scan each YAML file in .strata/ independently — do NOT use the shared
        # singleton here, as that would contaminate the process-wide instance.
        for cfg_path in get_strata_dir(self._work_path).rglob("*.yaml"):
            try:
                raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    continue
                model = ConfigurationModel.model_validate(raw)
                if model.spec:
                    for m in getattr(model.spec, "integrations", []) or []:
                        if m.name == siem_name:
                            return m
            except Exception:
                continue
        return None
