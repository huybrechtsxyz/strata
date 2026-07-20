"""Command to list deployment manifests."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import click

from strata.commands.schemas.schema_base_command import SchemaBaseCommand
from strata.services.deployment_manifest_service import DeploymentManifestService


class ListManifestCommand(SchemaBaseCommand):
    """List deployment manifests from the configured manifest store."""

    OPERATION = "manifest_list"

    def __init__(
        self,
        deployment: Optional[str] = None,
        last: Optional[int] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._deployment = deployment
        self._last = last
        self._entries: List[Dict[str, Any]] = []

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    @classmethod
    def show_console_header(cls, work_path: Optional[str] = None) -> None:
        """Suppress the standard base-command chrome."""

    @classmethod
    def show_console_footer(cls) -> None:
        """Suppress the standard base-command chrome."""

    def _execute(self) -> bool:
        from strata.controllers.solution_controller import SolutionController

        manifests_dir = SolutionController.get_deployments_dir(self._work_path)
        manifests = DeploymentManifestService.list_manifests(manifests_dir) if manifests_dir.exists() else []

        if self._deployment:
            manifests = [m for m in manifests if m.stem.startswith(self._deployment + "_")]
        if self._last:
            manifests = manifests[: self._last]

        entries: List[Dict[str, Any]] = []
        for manifest_path in manifests:
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                spec = data.get("spec", {})
                entries.append(
                    {
                        "path": str(manifest_path.relative_to(self._work_path)),
                        "deployment": spec.get("deployment_name", ""),
                        "action": spec.get("action", ""),
                        "status": spec.get("status", ""),
                        "started_at": spec.get("started_at", ""),
                        "deployed_by": spec.get("deployed_by", ""),
                    }
                )
            except (json.JSONDecodeError, OSError):
                entries.append({"path": str(manifest_path.relative_to(self._work_path)), "error": "unreadable"})

        self._entries = entries
        self._output_data = {"manifests": entries}
        return True

    def _after_execute(self) -> bool:
        if self._is_console_output():
            self._render_console()
        return super()._after_execute()

    def _render_console(self) -> None:
        if not self._entries:
            click.echo("No manifests found.")
            return

        for entry in self._entries:
            if entry.get("error"):
                click.echo(f"  {entry.get('path', '?')}  (unreadable)")
                continue
            click.echo(
                f"  {entry.get('path', '?')}  {entry.get('action', '?')}/{entry.get('status', '?')}  "
                f"{entry.get('started_at', '?')}  by {entry.get('deployed_by', '?')}"
            )
