"""Command to remove a registered deployment from the current Strata solution."""

from typing import Dict, Optional

import click

from strata.commands.base_command import BaseCommand


class RemoveDeploymentCommand(BaseCommand):
    """Remove a registered deployment file entry from the current solution.

    Removes the entry from ``solution.json`` by name.  The deployment YAML
    file itself is not deleted from disk.
    """

    OPERATION = "solution_deployment_remove"
    INIT_REQUIRED = True

    def __init__(
        self,
        name: str,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._deployment_name = name
        self._removed: Dict = {}

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        if not self._deployment_name:
            self._errors.append("Deployment name is required.")
            return False
        return True

    def _run(self) -> bool:
        # Capture metadata before removing
        deployments, errors = self._solution_controller.get_deployments(self._deployment_name)
        if errors:
            self._errors.extend(errors)
            return False

        d = deployments[0]
        self._removed = {"name": d.name, "path": d.path}

        ok, errors = self._solution_controller.remove_deployment(self._deployment_name)
        if not ok:
            self._errors.extend(errors)
            return False

        ok, errors = self._solution_controller.save()
        if not ok:
            self._errors.extend(errors)
            return False

        self._output_data = {"deployment": self._removed}
        return True

    def _after_execute(self) -> bool:
        if not self._is_quiet() and self._is_console_output() and self._removed:
            click.echo(f"\n  🗑️   Removed deployment '{self._removed['name']}' from solution.\n")
        return super()._after_execute()
