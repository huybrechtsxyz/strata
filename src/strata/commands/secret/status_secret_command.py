"""Live rotation health check — reports age and policy status for all secrets with a rotate spec."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.logger import get_logger
from strata.services.deployment_service import DeploymentService
from strata.utils.system import resolve_path

logger = get_logger(__name__)


class StatusSecretCommand(BaseCommand):
    """Check rotation health for secrets with a rotate policy."""

    OPERATION = "secret_status"
    INIT_REQUIRED = True

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

    def get_required_integrations(self) -> List[str]:
        return []

    def execute(self) -> bool:
        ok, _ = self._initialize()
        if not ok:
            return False

        if not self._file:
            self._add_error("--file / -f is required.")
            return False

        file_path = resolve_path(str(self._work_path), self._file)
        dep_svc = DeploymentService.load(str(file_path))
        if dep_svc is None or not dep_svc.is_valid:
            self._add_error(f"Cannot load deployment file: {self._file}")
            return False

        env_svc = dep_svc.get_environment_service()
        if env_svc is None:
            self._add_error("No environment defined in deployment.")
            return False

        # Only check secrets that have a rotate spec
        secrets = [s for s in env_svc.get_secrets() if s.rotate is not None]
        if not secrets:
            if self._is_json_output():
                click.echo(json.dumps({"secrets": [], "overdue": 0}))
            elif self._is_console_output():
                click.echo("No secrets with rotation policy defined.")
            return True

        from strata.controllers.value_controller import ValueController

        vc = ValueController(work_path=str(self._work_path))

        rows: List[Dict[str, Any]] = []
        overdue_count = 0
        now = datetime.now(timezone.utc)

        for item in secrets:
            assert item.rotate is not None
            integration = vc._get_integration_by_type(item.store.value)
            row: Dict[str, Any] = {
                "key": item.key,
                "store": item.store.value,
                "max_age": item.rotate.max_age,
                "policy": item.rotate.policy.value,
                "status": "unknown",
            }

            if integration is None:
                row["status"] = "no_integration"
                rows.append(row)
                continue

            meta = integration.get_secret_metadata(str(item.value))
            if meta is None:
                row["status"] = "no_metadata"
                rows.append(row)
                continue

            reference_time = meta.updated_at or meta.created_at
            if reference_time is None:
                row["status"] = "no_timestamp"
                rows.append(row)
                continue

            age_days = (now - reference_time).days
            row["age_days"] = age_days
            row["last_updated"] = reference_time.isoformat()

            if age_days >= item.rotate.max_age:
                row["status"] = "overdue"
                overdue_count += 1
            else:
                row["status"] = "ok"
                row["days_remaining"] = item.rotate.max_age - age_days

            rows.append(row)

        self._output_data = {"secrets": rows, "overdue": overdue_count}

        if self._is_json_output():
            click.echo(json.dumps(self._output_data, indent=2))
        elif self._is_console_output() and not self._output_quiet:
            key_w = max(len(r["key"]) for r in rows) if rows else 10
            for r in rows:
                if r["status"] == "overdue":
                    tag = f"⚠  OVERDUE ({r.get('age_days', '?')}d / {r['max_age']}d)"
                elif r["status"] == "ok":
                    tag = f"✓  ok ({r.get('age_days', '?')}d / {r['max_age']}d, {r.get('days_remaining', '?')}d left)"
                else:
                    tag = f"?  {r['status']}"
                click.echo(f"  {r['key']:<{key_w}}  [{r['policy']}]  {tag}")

            if overdue_count:
                click.echo(f"\n  {overdue_count} secret(s) overdue for rotation.")

        # Exit code 3 if any secret is overdue (validation failure convention)
        if overdue_count > 0:
            raise click.exceptions.Exit(3)

        return True
