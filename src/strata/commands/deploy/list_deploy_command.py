"""Command to list deployment manifests with metadata for CI matrix generation."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import click
import yaml

from strata.commands.base_command import BaseCommand
from strata.logger import get_logger


def _extract_entry(yaml_path: Path) -> Optional[Dict[str, Any]]:
    """Parse one YAML file and return a metadata dict if it is a deployment manifest.

    Uses a lightweight ``yaml.safe_load`` — no service loading, no ``@repo``
    resolution.  Returns ``None`` for files that are not ``kind: deployment``
    or that fail to parse.

    The returned dict always contains:

    - ``file``      — absolute path to the manifest
    - ``name``      — ``meta.name``
    - ``tenant``    — ``spec.tenant`` (or ``null``)
    - ``workspace`` — ``spec.workspace.name`` (or ``null``)

    All entries from ``spec.layers`` are promoted to the top level so that CI
    matrix consumers can reference any layer dimension directly.
    """
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

    entry: Dict[str, Any] = {
        "file": str(yaml_path.resolve()),
        "name": meta.get("name") or "",
    }

    # Promote all spec.layers entries as top-level fields
    layers = spec.get("layers") or {}
    if isinstance(layers, dict):
        for k, v in layers.items():
            entry[k] = v

    entry["tenant"] = spec.get("tenant") or None

    ws = spec.get("workspace") or {}
    entry["workspace"] = ws.get("name") if isinstance(ws, dict) else None

    return entry


class ListDeployCommand(BaseCommand):
    """List deployment manifests with extracted metadata.

    Scans a directory recursively for ``kind: deployment`` YAML files and
    emits a structured list — one entry per manifest — carrying the deployment
    name, all ``spec.layers`` dimensions, tenant, and workspace.

    Designed for CI matrix generation: pipe ``--output json`` output to
    ``jq`` or consume directly as a GitHub Actions matrix.

    ``INIT_REQUIRED = False`` — works without an initialised strata workspace.
    """

    OPERATION = "deploy_list"
    INIT_REQUIRED = False

    def __init__(
        self,
        path: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ):
        super().__init__(
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self.logger = get_logger(self.__class__.__module__)
        self._scan_path: Path = Path(path).resolve() if path else Path(os.getcwd()).resolve()

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    # -------------------------------------------------------------------------
    # Entry point
    # -------------------------------------------------------------------------

    def execute(self) -> bool:
        try:
            if not self._initialize():
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(success=False)
                return False

            ok = self._run_execution()

            if not self._after_execute():
                self._finalize(success=False)
                return False

            self._finalize(success=ok)
            return ok

        except Exception as exc:
            self._errors.append(f"Failed to execute deploy_list: {exc}")
            self.logger.exception("deploy_list failed")
            self._finalize(success=False)
            return False

    # -------------------------------------------------------------------------
    # Implementation
    # -------------------------------------------------------------------------

    def _run_execution(self) -> bool:
        if not self._scan_path.exists() or not self._scan_path.is_dir():
            self._errors.append(f"Path does not exist or is not a directory: {self._scan_path}")
            return False

        entries: List[Dict[str, Any]] = []
        for yaml_file in sorted(self._scan_path.rglob("*.yaml")):
            entry = _extract_entry(yaml_file)
            if entry is not None:
                entries.append(entry)
                self.logger.debug("Found deployment manifest", file=str(yaml_file))

        self._output_data = {"deployments": entries, "count": len(entries)}
        self.logger.info("deploy_list complete", count=len(entries), path=str(self._scan_path))

        if self._is_console_output():
            self._print_output(entries)

        return True

    def _print_output(self, entries: List[Dict[str, Any]]) -> None:
        """Render a human-readable table to the console."""
        if not entries:
            click.echo(f"\n  (no deployment manifests found under {self._scan_path})\n")
            return

        # Collect all layer dimension keys so the table adapts to what's present
        layer_keys: list[str] = []
        reserved = {"file", "name", "tenant", "workspace"}
        for e in entries:
            for k in e:
                if k not in reserved and k not in layer_keys:
                    layer_keys.append(k)

        # Column widths
        col_name = max(len(e.get("name") or "") for e in entries)
        col_name = max(col_name, 4)  # "name" header
        col_file = max(len(Path(e["file"]).name) for e in entries)
        col_file = max(col_file, 4)  # "file" header

        col_layer: Dict[str, int] = {}
        for k in layer_keys:
            vals = [str(e.get(k) or "") for e in entries]
            col_layer[k] = max(max(len(v) for v in vals), len(k))

        col_tenant = max(len(str(e.get("tenant") or "")) for e in entries)
        col_tenant = max(col_tenant, 8)  # "Tenant" header

        # Header
        header_parts = [
            f"{'name':<{col_name}}",
            f"{'file':<{col_file}}",
        ]
        for k in layer_keys:
            header_parts.append(f"{k:<{col_layer[k]}}")
        header_parts.append(f"{'Tenant':<{col_tenant}}")
        header = "  ".join(header_parts)

        click.echo(f"\n  {header}")
        click.echo(f"  {'-' * len(header)}")

        for e in entries:
            row_parts = [
                f"{(e.get('name') or ''):<{col_name}}",
                f"{Path(e['file']).name:<{col_file}}",
            ]
            for k in layer_keys:
                row_parts.append(f"{str(e.get(k) or ''):<{col_layer[k]}}")
            row_parts.append(f"{str(e.get('tenant') or ''):<{col_tenant}}")
            click.echo(f"  {'  '.join(row_parts)}")

        click.echo(f"\n  {len(entries)} deployment(s) found\n")
