"""Read a single secret value from its configured store backend."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.logger import get_logger
from strata.services.deployment_service import DeploymentService
from strata.utils.secret_generator import mask_secret
from strata.utils.system import resolve_path

logger = get_logger(__name__)


class GetSecretCommand(BaseCommand):
    """Read a secret value from the configured store."""

    OPERATION = "secret_get"
    INIT_REQUIRED = True

    def __init__(
        self,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
        file: Optional[str] = None,
        key: Optional[str] = None,
        unmask: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._file = file
        self._key = key
        self._unmask = unmask

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

        # Resolve the value from the store
        from strata.controllers.value_controller import ValueController

        vc = ValueController(work_path=str(self._work_path))
        val, err, note = vc._resolve_secret(item)
        if err:
            self._add_error(err)
            return False

        display = str(val) if self._unmask else mask_secret(str(val))

        result: Dict[str, Any] = {
            "key": item.key,
            "store": item.store.value,
            "found": val is not None,
        }
        if val is not None:
            result["value"] = str(val) if self._unmask else display
            result["masked"] = not self._unmask
        if note:
            result["note"] = note

        self._output_data = result

        if self._is_json_output():
            click.echo(json.dumps(result, indent=2))
        elif self._is_console_output():
            click.echo(display if val is not None else "(not found)")

        return val is not None
