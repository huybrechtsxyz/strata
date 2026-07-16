"""Command to show a single deployment manifest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import click

from strata.commands.schemas.schema_base_command import SchemaBaseCommand


class ShowManifestCommand(SchemaBaseCommand):
    """Display the full content of a deployment manifest file."""

    OPERATION = "manifest_show"

    def __init__(
        self,
        manifest_path: str,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._manifest_path = manifest_path
        self._manifest_data: Dict[str, Any] = {}

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    @classmethod
    def show_console_header(cls, work_path: Optional[str] = None) -> None:
        """Suppress the standard base-command chrome."""

    @classmethod
    def show_console_footer(cls) -> None:
        """Suppress the standard base-command chrome."""

    def _execute(self) -> bool:
        path = Path(self._manifest_path)
        try:
            self._manifest_data = json.loads(path.read_text(encoding="utf-8"))
            self._output_data = self._manifest_data
            return True
        except (json.JSONDecodeError, OSError) as exc:
            self._errors.append(f"Error reading manifest: {exc}")
            return False

    def _after_execute(self) -> bool:
        if self._is_console_output():
            self._render_console()
        return super()._after_execute()

    def _render_console(self) -> None:
        if not self._manifest_data:
            click.echo("Error reading manifest.", err=True)
            return

        spec = self._manifest_data.get("spec", {})
        meta = self._manifest_data.get("meta", {})
        click.echo(f"Manifest: {meta.get('name', '?')}")
        click.echo(f"  Action:      {spec.get('action', '?')}")
        click.echo(f"  Status:      {spec.get('status', '?')}")
        click.echo(f"  Deployment:  {spec.get('deployment_name', '?')}")
        click.echo(f"  Workspace:   {spec.get('workspace_name', '?')}")
        click.echo(f"  Environment: {spec.get('environment', '—')}")
        click.echo(f"  Started:     {spec.get('started_at', '?')}")
        click.echo(f"  Completed:   {spec.get('completed_at', '—')}")
        click.echo(f"  Duration:    {spec.get('duration_seconds', '—')}s")
        click.echo(f"  By:          {spec.get('deployed_by', '?')}")

        artifacts = spec.get("artifacts", {})
        platform = artifacts.get("platform", {})
        if platform:
            click.echo(f"  Platform:    {platform.get('hash', '?')}")

        repos = artifacts.get("repositories", {})
        if repos:
            click.echo("  Repositories:")
            for name, info in repos.items():
                commit = info.get("commit", "?")[:12] if info.get("commit") else "?"
                click.echo(f"    {name}: {commit} ({info.get('ref', '—')})")

        stages = spec.get("stages", [])
        if stages:
            click.echo("  Stages:")
            for stage in stages:
                click.echo(
                    f"    {stage.get('name', '?')}: {stage.get('status', '?')} ({stage.get('duration_seconds', '?')}s)"
                )

        sbom = spec.get("sbom")
        if sbom:
            click.echo(f"  SBOM:        {sbom.get('path', '?')} ({sbom.get('component_count', '?')} components)")

        policy_results = spec.get("policy_results", [])
        if policy_results:
            click.echo("  Policies:")
            for policy_result in policy_results:
                icon = "✓" if policy_result.get("passed") else "✗"
                click.echo(
                    f"    {icon} {policy_result.get('policy_name', '?')} [{policy_result.get('enforcement', '?')}]"
                )

        lock = spec.get("lock")
        if lock:
            click.echo(f"  Lock:        {lock.get('backend', '?')} (id={lock.get('lock_id', '?')[:12]})")

        signatures = spec.get("signatures")
        if signatures:
            click.echo(f"  Signed:      {signatures.get('method', 'yes')}")
