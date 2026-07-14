"""Command to scan a directory for deployment YAML files and register them in the solution."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import click
import yaml

from strata.commands.base_command import BaseCommand
from strata.models.solution_model import SolutionSpecDeploymentModel

_YAML_SUFFIXES = {".yaml", ".yml"}


def _read_deployment_meta(file_path: Path) -> Optional[Dict]:
    """Return ``{"name": ..., "path": file_path}`` if the file is a deployment YAML, else None."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except Exception:
        return None

    if not isinstance(doc, dict):
        return None
    if doc.get("kind") != "deployment":
        return None

    meta = doc.get("meta") or {}
    name = (meta.get("name") or "").strip()
    if not name:
        return None

    return {"name": name, "file_path": file_path}


class ScanDeploymentsCommand(BaseCommand):
    """Scan a directory for ``kind: deployment`` YAML files and register them in the solution.

    Walks the given directory recursively, finds all YAML files with
    ``kind: deployment``, and registers each one that is not already
    tracked.  Duplicate names are skipped with a warning.
    """

    OPERATION = "solution_deployment_scan"
    INIT_REQUIRED = True

    def __init__(
        self,
        scan_path: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._scan_path = scan_path
        self._added: List[Dict] = []
        self._skipped: List[Dict] = []
        self._invalid: List[str] = []

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def _execute(self) -> bool:
        scan_root = Path(self._scan_path) if self._scan_path else self._work_path
        if not scan_root.is_absolute():
            scan_root = self._work_path / scan_root

        if not scan_root.exists():
            self._errors.append(f"Scan directory not found: {scan_root}")
            return False

        if not scan_root.is_dir():
            self._errors.append(f"Scan path is not a directory: {scan_root}")
            return False

        # Walk the directory tree
        candidates = [p for p in scan_root.rglob("*") if p.suffix in _YAML_SUFFIXES and p.is_file()]

        existing_deployments, _ = self._solution_controller.get_deployments()
        existing_names = {d.name for d in existing_deployments}

        now = datetime.now(timezone.utc).isoformat()

        for file_path in sorted(candidates):
            meta = _read_deployment_meta(file_path)
            if meta is None:
                continue  # Not a deployment YAML — skip silently

            name = meta["name"]

            try:
                stored_path = str(file_path.relative_to(self._work_path))
            except ValueError:
                stored_path = str(file_path)

            if name in existing_names:
                self._skipped.append({"name": name, "path": stored_path, "reason": "already registered"})
                continue

            deployment = SolutionSpecDeploymentModel(name=name, path=stored_path, created=now)
            ok, errors = self._solution_controller.add_deployment(deployment)
            if not ok:
                self._skipped.append({"name": name, "path": stored_path, "reason": errors[0] if errors else "error"})
            else:
                existing_names.add(name)
                self._added.append({"name": name, "path": stored_path})

        if self._added:
            ok, errors = self._solution_controller.save()
            if not ok:
                self._errors.extend(errors)
                return False

        self._output_data = {
            "scan_path": str(scan_root),
            "added": self._added,
            "skipped": self._skipped,
        }
        return True

    def _after_execute(self) -> bool:
        if not self._is_quiet() and self._is_console_output():
            click.echo(f"\n  🔍  Scanned: {self._scan_path or '.'}\n")
            if self._added:
                click.echo(f"  ✅  Registered ({len(self._added)}):")
                for d in self._added:
                    click.echo(f"      • {d['name']} → {d['path']}")
            if self._skipped:
                click.echo(f"\n  ⏭️   Skipped ({len(self._skipped)}):")
                for d in self._skipped:
                    click.echo(f"      • {d['name']} ({d['reason']})")
            if not self._added and not self._skipped:
                click.echo("  ℹ️   No deployment files found.\n")
            click.echo("")
        return super()._after_execute()
