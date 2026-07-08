"""Command to register a deployment file in the current Strata solution."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import click
import yaml

from strata.commands.base_command import BaseCommand
from strata.models.solution_model import SolutionSpecDeploymentModel


class AddDeploymentCommand(BaseCommand):
    """Register a deployment YAML file in the current solution.

    Reads the file to verify it has ``kind: deployment`` and extracts
    ``meta.name``, then adds it to ``solution.json``.  The path stored
    is relative to the work-path when possible, so the registry is
    portable across machines.
    """

    OPERATION = "solution_deployment_add"
    INIT_REQUIRED = True

    def __init__(
        self,
        path: str,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._input_path = path
        self._added: Dict = {}

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        if not self._input_path:
            self._errors.append("Deployment file path is required.")
            return False
        return True

    def _run(self) -> bool:
        file_path = Path(self._input_path)
        if not file_path.is_absolute():
            file_path = self._work_path / file_path

        if not file_path.exists():
            self._errors.append(f"File not found: {file_path}")
            return False

        if not file_path.is_file():
            self._errors.append(f"Path is not a file: {file_path}")
            return False

        # Read and validate the YAML
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                doc = yaml.safe_load(f)
        except Exception as exc:
            self._errors.append(f"Cannot read YAML from {file_path}: {exc}")
            return False

        if not isinstance(doc, dict):
            self._errors.append(f"File does not contain a YAML mapping: {file_path}")
            return False

        kind = doc.get("kind", "")
        if kind != "deployment":
            self._errors.append(f"Expected kind: deployment, got kind: '{kind}' in {file_path}")
            return False

        meta = doc.get("meta") or {}
        name = meta.get("name", "").strip()
        if not name:
            self._errors.append(f"Deployment file has no meta.name: {file_path}")
            return False

        # Store relative path when the file is under work_path
        try:
            stored_path = str(file_path.relative_to(self._work_path))
        except ValueError:
            stored_path = str(file_path)

        deployment = SolutionSpecDeploymentModel(
            name=name,
            path=stored_path,
            created=datetime.now(timezone.utc).isoformat(),
        )

        ok, errors = self._solution_controller.add_deployment(deployment)
        if not ok:
            self._errors.extend(errors)
            return False

        ok, errors = self._solution_controller.save()
        if not ok:
            self._errors.extend(errors)
            return False

        self._added = {"name": name, "path": stored_path}
        self._output_data = {"deployment": self._added}
        return True

    def _after_execute(self) -> bool:
        if not self._is_quiet() and self._is_console_output() and self._added:
            click.echo(f"\n  ✅  Registered deployment '{self._added['name']}' → {self._added['path']}\n")
        return super()._after_execute()
