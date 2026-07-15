"""List all secrets defined in the deployment's environment YAML (no store access)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.logger import get_logger
from strata.services.deployment_service import DeploymentService
from strata.utils.system import resolve_path

logger = get_logger(__name__)


class ListSecretCommand(BaseCommand):
    """List secrets declared in the deployment environment YAML."""

    OPERATION = "secret_list"

    def __init__(
        self,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
        file: Optional[str] = None,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._file = file
        self._deployment_service: Optional[DeploymentService] = None

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def _execute(self) -> bool:

        if not self._file:
            self._errors.append("--file / -f is required.")
            return False

        file_path = resolve_path(str(self._work_path), self._file)
        dep_svc = DeploymentService.load(str(file_path))
        if dep_svc is None or not dep_svc.is_valid:
            self._errors.append(f"Cannot load deployment file: {self._file}")
            return False
        self._deployment_service = dep_svc

        env_svc = dep_svc.get_environment_service()
        if env_svc is None:
            self._errors.append("No environment defined in deployment.")
            return False

        secrets = env_svc.get_secrets()
        rows: List[Dict[str, Any]] = []
        for s in secrets:
            row: Dict[str, Any] = {
                "key": s.key,
                "store": s.store.value,
                "value_ref": str(s.value) if s.value else None,
            }
            if s.generate:
                row["generate"] = f"{s.generate.type.value}/{s.generate.length}"
            if s.rotate:
                row["rotate"] = f"{s.rotate.max_age}d/{s.rotate.policy.value}"
            rows.append(row)

        self._output_data = {"secrets": rows, "count": len(rows)}

        if self._output_format == "json":
            click.echo(json.dumps(self._output_data, indent=2))
        elif self._is_console_output() and not self._output_quiet:
            if not rows:
                click.echo("No secrets defined.")
            else:
                key_w = max(len(r["key"]) for r in rows)
                store_w = max(len(r["store"]) for r in rows)
                for r in rows:
                    parts = [f"{r['key']:<{key_w}}  {r['store']:<{store_w}}"]
                    if r.get("generate"):
                        parts.append(f"generate:{r['generate']}")
                    if r.get("rotate"):
                        parts.append(f"rotate:{r['rotate']}")
                    click.echo("  ".join(parts))

        return True
