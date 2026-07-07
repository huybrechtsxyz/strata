"""On-demand secret rotation — generates a new value and writes it to the store."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.logger import get_logger
from strata.services.deployment_service import DeploymentService
from strata.utils.secret_generator import generate_secret
from strata.utils.system import resolve_path

logger = get_logger(__name__)


class RotateSecretCommand(BaseCommand):
    """Rotate a secret by generating a new value and writing it to the store."""

    OPERATION = "secret_rotate"
    INIT_REQUIRED = True

    def __init__(
        self,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
        file: Optional[str] = None,
        key: Optional[str] = None,
        force: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._file = file
        self._key = key
        self._force = force

    def get_required_integrations(self) -> List[str]:
        return []

    def execute(self) -> bool:
        ok, _ = self._initialize()
        if not ok:
            return False

        if not self._file:
            self._add_error("--file / -f is required.")
            return False
        if not self._key:
            self._add_error("KEY argument is required.")
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

        item = None
        for s in env_svc.get_secrets():
            if s.key == self._key:
                item = s
                break
        if item is None:
            self._add_error(f"Secret '{self._key}' not found in environment definition.")
            return False

        if item.generate is None:
            self._add_error(
                f"Secret '{self._key}' has no generate spec — cannot auto-rotate. "
                "Use 'secret put --value' to manually set a new value."
            )
            return False

        # Confirmation prompt unless --force
        if not self._force:
            click.confirm(
                f"Rotate secret '{item.key}' in {item.store.value}? This will overwrite the current value.",
                abort=True,
            )

        new_value = generate_secret(item.generate.type.value, item.generate.length)

        from strata.controllers.value_controller import ValueController

        vc = ValueController(work_path=str(self._work_path))
        integration = vc._get_integration_by_type(item.store.value)
        if integration is None:
            self._add_error(f"No integration registered for store type '{item.store.value}'.")
            return False

        rotated = integration.update_secret(str(item.value), new_value)

        result: Dict[str, Any] = {
            "key": item.key,
            "store": item.store.value,
            "rotated": rotated,
            "generator": f"{item.generate.type.value}/{item.generate.length}",
        }
        self._output_data = result

        if self._is_json_output():
            click.echo(json.dumps(result, indent=2))
        elif self._is_console_output() and not self._output_quiet:
            if rotated:
                click.echo(f"  ✓  Secret '{item.key}' rotated in {item.store.value}.")
            else:
                click.echo(f"  ✗  Failed to rotate secret '{item.key}' in {item.store.value}.")

        return rotated
