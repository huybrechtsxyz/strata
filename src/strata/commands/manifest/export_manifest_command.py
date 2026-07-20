"""Command to export deployment manifests as a compliance evidence package."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional

import click

from strata.commands.schemas.schema_base_command import SchemaBaseCommand
from strata.services.deployment_manifest_service import DeploymentManifestService


class ExportManifestCommand(SchemaBaseCommand):
    """Export deployment manifests as a compliance evidence package."""

    OPERATION = "manifest_export"

    def __init__(
        self,
        out_dir: str,
        deployment: Optional[str] = None,
        last: Optional[int] = None,
        include_sbom: bool = False,
        include_platform: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._out_dir = out_dir
        self._deployment = deployment
        self._last = last
        self._include_sbom = include_sbom
        self._include_platform = include_platform
        self._exported_files: List[str] = []

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

        out = Path(self._out_dir)
        out.mkdir(parents=True, exist_ok=True)
        manifests_out = out / "manifests"
        manifests_out.mkdir(exist_ok=True)

        exported: List[str] = []
        for manifest_path in manifests:
            dest = manifests_out / manifest_path.name
            shutil.copy2(manifest_path, dest)
            exported.append(str(dest.relative_to(out)))

            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                spec = data.get("spec", {})

                if self._include_sbom:
                    sbom = spec.get("sbom", {})
                    sbom_path = sbom.get("path")
                    if sbom_path:
                        src = self._work_path / sbom_path
                        if src.exists():
                            sbom_dest = out / "sbom" / Path(sbom_path).name
                            sbom_dest.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src, sbom_dest)
                            exported.append(str(sbom_dest.relative_to(out)))

                if self._include_platform:
                    platform = spec.get("artifacts", {}).get("platform", {})
                    platform_path = platform.get("path")
                    if platform_path:
                        src = self._work_path / platform_path
                        if src.exists():
                            platform_dest = out / "platform" / Path(platform_path).name
                            platform_dest.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src, platform_dest)
                            exported.append(str(platform_dest.relative_to(out)))
            except (json.JSONDecodeError, OSError):
                pass

        self._exported_files = exported
        self._output_data = {
            "output_dir": str(out),
            "manifest_count": len(manifests),
            "files": exported,
        }
        return True

    def _after_execute(self) -> bool:
        if self._is_console_output():
            self._render_console()
        return super()._after_execute()

    def _render_console(self) -> None:
        click.echo(
            f"Exported {self._output_data.get('manifest_count', 0)} manifest(s) to {self._output_data.get('output_dir')}"
        )
        for file_path in self._exported_files:
            click.echo(f"  {file_path}")
