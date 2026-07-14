"""Write a secret value to its configured store backend (create / bootstrap)."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.logger import get_logger
from strata.services.deployment_service import DeploymentService
from strata.utils.secret_generator import generate_secret
from strata.utils.system import resolve_path

logger = get_logger(__name__)


class PutSecretCommand(BaseCommand):
    """Write a secret value to the configured store (create-if-not-exists)."""

    OPERATION = "secret_put"
    INIT_REQUIRED = True

    def __init__(
        self,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
        file: Optional[str] = None,
        key: Optional[str] = None,
        value: Optional[str] = None,
        generate: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._file = file
        self._key = key
        self._value = value
        self._generate = generate

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def _execute(self) -> bool:

        if not self._file:
            self._errors.append("--file / -f is required.")
            return False
        if not self._key:
            self._errors.append("KEY argument is required.")
            return False
        if self._value and self._generate:
            self._errors.append("--value and --generate are mutually exclusive.")
            return False

        file_path = resolve_path(str(self._work_path), self._file)
        dep_svc = DeploymentService.load(str(file_path))
        if dep_svc is None or not dep_svc.is_valid:
            self._errors.append(f"Cannot load deployment file: {self._file}")
            return False

        env_svc = dep_svc.get_environment_service()
        if env_svc is None:
            self._errors.append("No environment defined in deployment.")
            return False

        item = None
        for s in env_svc.get_secrets():
            if s.key == self._key:
                item = s
                break
        if item is None:
            self._errors.append(f"Secret '{self._key}' not found in environment definition.")
            return False

        # Determine the value to write
        if self._value:
            secret_value = self._value
            source = "provided"
        elif self._generate:
            if item.generate is None:
                self._errors.append(
                    f"Secret '{self._key}' has no generate spec. "
                    "Use --value to provide a value, or add a generate: block to the YAML."
                )
                return False
            secret_value = generate_secret(item.generate.type.value, item.generate.length)
            source = "generated"
        else:
            self._errors.append("Either --value or --generate is required.")
            return False

        # Write to store (create-if-not-exists via set_secret)
        from strata.controllers.value_controller import ValueController

        vc = ValueController()
        integration = vc._get_integration_by_type(item.store.value)
        if integration is None:
            self._errors.append(f"No integration registered for store type '{item.store.value}'.")
            return False

        written = integration.set_secret(str(item.value), secret_value)

        result: Dict[str, Any] = {
            "key": item.key,
            "store": item.store.value,
            "source": source,
            "written": written,
        }
        self._output_data = result

        if self._output_format == "json":
            click.echo(json.dumps(result, indent=2))
        elif self._is_console_output() and not self._output_quiet:
            if written:
                click.echo(f"  ✓  Secret '{item.key}' written to {item.store.value} ({source}).")
            else:
                click.echo(f"  ✗  Failed to write secret '{item.key}' to {item.store.value}.")

        return written
