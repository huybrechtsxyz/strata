"""Command to show resolved deployment configuration: remote versions, workspace, and environment."""

from typing import Any, Dict, List, Optional

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand


class ShowDeployCommand(BaseDeployCommand):
    """Show resolved deployment configuration for a deployment manifest.

    For each remote configured in the workspace, displays:
    - The effective reference (after applying environment overrides)
    - Whether the reference came from an environment override or the workspace default

    Also prints the workspace and environment files in use.
    """

    OPERATION = "deploy_show"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ):
        super().__init__(
            file=file,
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self._resolved_remotes: List[Dict[str, str]] = []

    # -------------------------------------------------------------------------
    # Core logic
    # -------------------------------------------------------------------------

    def _execute(self) -> bool:
        ok = self._collect()
        if self._is_console_output():
            self._print_output()
        return ok

    # -------------------------------------------------------------------------
    # Implementation
    # -------------------------------------------------------------------------

    def _collect(self) -> bool:
        """Resolve remote references and populate self._output_data."""
        if self._deployment_service is None or self._configuration_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        # Determine which remotes have an environment-level override
        env_override_names: set[str] = set()
        env_name: Optional[str] = None
        env_path: Optional[str] = None
        ws_path: Optional[str] = None

        try:
            env_service = self._deployment_service.get_environment_service()
            if env_service:
                env_name = env_service.get_name()
                env_path = str(env_service.path) if env_service.path else None
                spec = env_service.model.spec if env_service.model else None
                if spec and spec.overrides and spec.overrides.remotes:
                    for r in spec.overrides.remotes:
                        env_override_names.add(str(r.remote))
        except Exception:
            pass

        ws = self._deployment_service._workspace_service
        if ws:
            ws_path = str(ws.path) if ws.path else None

        # Build remote rows — config_remote.reference is already the effective ref
        # because apply_environment_overrides() mutated it in-place before we get here
        remotes_out: List[Dict[str, str]] = []
        config_model = self._configuration_service.model
        if config_model and config_model.spec and config_model.spec.remotes:
            for remote in config_model.spec.remotes:
                name = str(remote.name)
                effective_ref = str(remote.reference) if remote.reference else "(none)"
                if name in env_override_names:
                    source = f"{env_name} (override)" if env_name else "env override"
                else:
                    source = "workspace default"
                remotes_out.append(
                    {
                        "name": name,
                        "reference": effective_ref,
                        "source": source,
                    }
                )

        self._resolved_remotes = remotes_out
        self._output_data: Dict[str, Any] = {
            "file": str(self._file_path),
            "deployment": self._deployment_service.get_name(),
            "workspace": ws_path,
            "environment": env_name,
            "environment_file": env_path,
            "remotes": remotes_out,
        }
        return True

    def _print_output(self) -> None:
        """Render deployment show summary to console."""
        ds = self._deployment_service
        if ds is None:
            return

        click.echo(f"\n📋  Deployment:   {ds.get_name()}")
        click.echo(f"    File:         {self._file_path}")

        ws = ds._workspace_service
        if ws:
            click.echo(f"    Workspace:    {ws.path}")

        try:
            env = ds.get_environment_service()
            if env:
                label = f"{env.get_name()} ({env.path})" if env.path else env.get_name()
                click.echo(f"    Environment:  {label}")
        except Exception:
            pass

        if not self._resolved_remotes:
            click.echo("\n    (no remotes configured)\n")
            return

        click.echo("\n    Remote Versions:\n")
        name_w = max(len(r["name"]) for r in self._resolved_remotes)
        ref_w = max(len(r["reference"]) for r in self._resolved_remotes)
        click.echo(f"    {'Remote':<{name_w}}  {'Effective Ref':<{ref_w}}  Source")
        click.echo("    " + "─" * (name_w + ref_w + 18))
        for r in self._resolved_remotes:
            click.echo(f"    {r['name']:<{name_w}}  {r['reference']:<{ref_w}}  {r['source']}")
        click.echo()
