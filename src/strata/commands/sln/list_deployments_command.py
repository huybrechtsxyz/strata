"""Command to list deployment files registered in the current Strata solution."""

from typing import Dict, List, Optional

import click

from strata.commands.base_command import BaseCommand


class ListDeploymentsCommand(BaseCommand):
    """List deployment files registered in the current solution.

    Prints all entries from ``solution.json``, showing name and path.
    Pass ``--name`` to filter to a single deployment.
    """

    OPERATION = "solution_deployment_list"

    def __init__(
        self,
        name: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._filter_name = name
        self._deployments: List[Dict] = []

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def _execute(self) -> bool:
        deployments, errors = self._solution_controller.get_deployments(self._filter_name)
        if errors:
            self._errors.extend(errors)
            return False

        self._deployments = [{"name": d.name, "path": d.path, "created": d.created} for d in deployments]
        self._output_data = {"deployments": self._deployments}
        return True

    def _after_execute(self) -> bool:
        if not self._is_quiet() and self._is_console_output():
            if not self._deployments:
                click.echo("\n  📋  No deployments registered in solution.\n")
            else:
                click.echo(f"\n  📋  Deployments ({len(self._deployments)}):\n")
                for d in self._deployments:
                    click.echo(f"    • {d['name']}")
                    click.echo(f"      Path: {d['path']}")
                click.echo("")
        return super()._after_execute()
