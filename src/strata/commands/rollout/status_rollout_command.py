"""Command to scan a workspace or directory and summarize deployment status per manifest."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import click
import yaml

from strata.commands.deploy.base_deploy_command import BaseDeployCommand


class StatusRolloutCommand(BaseDeployCommand):
    """Scan for deployment manifests and show a one-line status summary per deployment.

    Fleet-wide concern: either ``--path DIR`` (scan a specific directory) or
    ``--all`` (scan the entire workspace) — always offline, reading only the
    build cache (``<stage>.tf-outputs.json``) written by ``strata deploy run``.

    For a single deployment's live, per-stage detail use ``strata deploy status``.
    """

    OPERATION = "rollout_status"

    def __init__(
        self,
        work_path: Optional[str] = None,
        path: Optional[str] = None,
        all_deployments: bool = False,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ):
        super().__init__(
            file=None,
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self._path = path
        self._all = all_deployments

    def get_required_integrations(self) -> Dict[str, str]:
        # Always offline — reads the build cache only, no terraform call needed.
        return {}

    def _before_execute(self) -> bool:
        # No deployment file needed — skip BaseDeployCommand's file-loading validation.
        return True

    def _execute(self) -> bool:
        """Scan for deployment YAML files and show a one-line status per deployment."""
        scan_root = Path(self._path).resolve() if self._path else self._work_path
        if not scan_root.exists() or not scan_root.is_dir():
            self._errors.append(f"Path does not exist or is not a directory: {scan_root}")
            return False

        entries: List[Dict[str, Any]] = []
        for yaml_file in sorted(scan_root.rglob("*.yaml")):
            entry = self._extract_deployment_status(yaml_file)
            if entry is not None:
                entries.append(entry)

        if self._is_console_output():
            if not entries:
                click.echo(f"\n  (no deployment manifests found under {scan_root})\n")
            else:
                mode_label = f"--path {scan_root}" if self._path else "--all"
                click.echo(f"\n📊  Deployment Status — {len(entries)} deployment(s) [{mode_label}]\n")
                for entry in entries:
                    self._print_deployment_summary(entry)

        self._output_data = {
            "scan_path": str(scan_root),
            "deployments": entries,
        }
        return True

    def _extract_deployment_status(self, yaml_path: Path) -> Optional[Dict[str, Any]]:
        """Parse a YAML file and return a status summary if it is a deployment manifest."""
        try:
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        except Exception:
            return None

        if not isinstance(raw, dict):
            return None
        if raw.get("kind") != "deployment":
            return None

        meta = raw.get("meta") or {}
        spec = raw.get("spec") or {}
        stages = spec.get("stages") or []

        stage_summaries: List[Dict[str, Any]] = []
        for stage in stages:
            stage_name = stage.get("name") or ""
            provisioner = stage.get("provisioner") or "terraform"
            cache_info = self._read_output_cache_by_name(str(stage_name))
            stage_summaries.append(
                {
                    "name": str(stage_name),
                    "provisioner": str(provisioner),
                    "cached": cache_info is not None,
                    "cache": cache_info,
                }
            )

        cached_count = sum(1 for s in stage_summaries if s["cached"])
        return {
            "file": str(yaml_path.resolve()),
            "name": meta.get("name") or "",
            "stages": stage_summaries,
            "stage_count": len(stage_summaries),
            "cached_count": cached_count,
        }

    def _print_deployment_summary(self, entry: Dict[str, Any]) -> None:
        name = entry["name"] or entry["file"]
        stage_count = entry["stage_count"]
        cached_count = entry["cached_count"]
        stages = entry["stages"]

        if stage_count == 0:
            status_icon = "⬜"
        elif cached_count == stage_count:
            status_icon = "✅"
        elif cached_count > 0:
            status_icon = "⚠️ "
        else:
            status_icon = "⬜"

        click.echo(f"  {status_icon} {name}  ({cached_count}/{stage_count} stages cached)")

        for stage in stages:
            stage_icon = "✓" if stage["cached"] else "○"
            cache = stage.get("cache")
            cache_detail = ""
            if cache:
                refreshed = cache.get("refreshed_at", "unknown")
                out_count = cache.get("output_count", 0)
                cache_detail = f"  {refreshed}  {out_count} output(s)"
            click.echo(f"      {stage_icon} {stage['name']}{cache_detail}")

        click.echo()

    # ------------------------------------------------------------------
    # Cache helper (mirrors deploy/status_deploy_command.py's cache reader)
    # ------------------------------------------------------------------

    def _read_output_cache_by_name(self, stage_name: str) -> Optional[Dict[str, Any]]:
        """Read the cached ``.tf-outputs.json`` for a stage given by name."""
        import json

        cache_file = self._build_path / f"{stage_name}.tf-outputs.json"
        if not cache_file.exists():
            return None
        try:
            with open(cache_file, encoding="utf-8") as fh:
                data = json.load(fh)
            refreshed = data.get("refreshed_at")
            outputs = data.get("outputs", {})
            return {
                "refreshed_at": refreshed,
                "output_count": len(outputs),
                "output_keys": list(outputs.keys()),
            }
        except (OSError, json.JSONDecodeError):
            return None
