"""Show stored Terraform output artifacts for a deployment."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand

_DEFAULT_OUTPUTS_PATH = ".strata/outputs"


class OutputsDeployCommand(BaseDeployCommand):
    """Show stored output artifacts written by ``deploy run``.

    Reads the JSON artifact files stored under the configured outputs path
    (default: ``.strata/outputs``).  These are written after each successful
    ``deploy run`` stage that produces Terraform outputs.

    ``--stage NAME``
        Limit display to a single stage.

    ``--key NAME``
        Print only a single output key.

    ``--version VERSION``
        Show artifacts for a specific version tag.  Defaults to the version
        from the deployment labels (the current version).

    ``--all-versions``
        Show artifacts for every version directory found in the outputs path.
    """

    OPERATION = "deploy_outputs"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        stage: Optional[str] = None,
        key: Optional[str] = None,
        version: Optional[str] = None,
        all_versions: bool = False,
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
        self._stage = stage
        self._key = key
        self._version = version
        self._all_versions = all_versions

    # -------------------------------------------------------------------------
    # Entry point
    # -------------------------------------------------------------------------

    def execute(self) -> bool:
        try:
            if not self._initialize():
                self._finalize(success=False)
                return False

            if not self._before_execute():
                self._finalize(success=False)
                return False

            ok = self._run()
            self._finalize(success=ok)
            return ok

        except Exception as exc:
            self._errors.append(f"Failed to execute deploy_outputs: {exc}")
            self.logger.exception("deploy_outputs failed")
            self._finalize(success=False)
            return False

    # -------------------------------------------------------------------------
    # Core logic
    # -------------------------------------------------------------------------

    def _run(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        deploy_meta = self._deployment_service.model.meta  # type: ignore[union-attr]
        deployment_name = str(deploy_meta.name)

        # Resolve outputs base directory from config, or fall back to default
        outputs_config = self._get_outputs_config()
        if outputs_config is not None and not outputs_config.enabled:
            if self._is_console_output():
                click.echo("  Outputs artifact storage is disabled for this deployment.")
            self._output_data = {"deployment": deployment_name, "artifacts": []}
            return True

        base_path = outputs_config.path if outputs_config else _DEFAULT_OUTPUTS_PATH
        outputs_dir = self._work_path / base_path / deployment_name

        if not outputs_dir.exists():
            if self._is_console_output():
                click.echo(f"\n  No stored outputs found for deployment '{deployment_name}'.")
                click.echo(f"  Expected location: {outputs_dir}")
                click.echo("  Run 'strata deploy run' with outputs configured to generate them.\n")
            self._output_data = {"deployment": deployment_name, "artifacts": []}
            return True

        # Determine which versions to show
        versions = self._resolve_versions(outputs_dir, deploy_meta)

        # Collect matching artifact files
        artifacts: List[Dict[str, Any]] = []
        for ver in versions:
            ver_dir = outputs_dir / ver
            if not ver_dir.is_dir():
                continue
            for artifact_file in sorted(ver_dir.glob("*.json")):
                stage_name = artifact_file.stem
                if self._stage and stage_name != self._stage:
                    continue
                entry = self._read_artifact(artifact_file)
                if entry is not None:
                    artifacts.append(entry)

        if self._is_console_output():
            self._render_console(deployment_name, artifacts)

        self._output_data = {
            "deployment": deployment_name,
            "artifacts": artifacts,
        }
        return True

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _resolve_versions(self, outputs_dir: Path, deploy_meta) -> List[str]:
        """Return the list of version strings to display."""
        if self._all_versions:
            return sorted(
                [v.name for v in outputs_dir.iterdir() if v.is_dir()],
                reverse=True,
            )
        if self._version:
            return [self._version]
        # Default: the version from the deployment YAML labels
        labels = deploy_meta.labels or {}
        return [str(labels.get("version", "unknown"))]

    def _read_artifact(self, path: Path) -> Optional[Dict[str, Any]]:
        """Read a single artifact JSON file, applying --key filter if set."""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data: Dict[str, Any] = json.load(fh)
            if self._key:
                outputs = data.get("outputs", {})
                data = dict(data)
                data["outputs"] = {self._key: outputs[self._key]} if self._key in outputs else {}
                data["key_filter"] = self._key
            return data
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.logger.warning("Failed to read outputs artifact", path=str(path), error=str(exc))
            self._messages.append(f"  ⚠  Skipped {path.name}: {exc}")
            return None

    def _render_console(self, deployment_name: str, artifacts: List[Dict[str, Any]]) -> None:
        """Render artifacts to the console, grouped by version."""
        click.echo(f"\n  Stored outputs — deployment '{deployment_name}'\n")

        if not artifacts:
            msg = "  No stored outputs found"
            if self._stage:
                msg += f" for stage '{self._stage}'"
            if self._version:
                msg += f" at version '{self._version}'"
            click.echo(msg + ".\n")
            return

        # Group by version
        by_version: Dict[str, List[Dict[str, Any]]] = {}
        for entry in artifacts:
            ver = entry.get("version", "unknown")
            by_version.setdefault(ver, []).append(entry)

        for ver, entries in by_version.items():
            click.echo(f"  Version: {ver}")
            for entry in entries:
                stage = entry.get("stage", "?")
                written_at = entry.get("written_at", "")
                outputs = entry.get("outputs", {})
                ts = f"  (written {written_at})" if written_at else ""
                click.echo(f"    Stage: {stage}{ts}")
                if outputs:
                    for k, v in outputs.items():
                        click.echo(f"      • {k}: {v}")
                else:
                    label = f"key '{self._key}' not found" if self._key else "no outputs stored"
                    click.echo(f"      ({label})")
                click.echo()
