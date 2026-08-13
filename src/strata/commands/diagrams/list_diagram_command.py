"""Command to list available diagram definitions."""

from typing import Dict, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.controllers.diagram_controller import DiagramController


class ListDiagramCommand(BaseCommand):
    """List shipped built-in and workspace diagram definitions."""

    OPERATION = "diagram_list"

    def __init__(
        self,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._definitions: list = []

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def _initialize(self, show_header: bool = True) -> bool:
        # Works without an initialized workspace — built-ins ship with the package.
        return self._initialize_session(show_header=show_header)

    def _execute(self) -> bool:
        controller = DiagramController(work_path=self._work_path)
        self._definitions = controller.list_definitions()
        self._output_data = {"diagrams": self._definitions}
        return True

    def _after_execute(self) -> bool:
        if self._is_console_output() and not self._output_quiet:
            self._render_console()
        return super()._after_execute()

    def _render_console(self) -> None:
        if not self._definitions:
            click.echo("\n  No diagram definitions found.\n")
            return
        click.echo("")
        click.echo(f"  {'NAME':<24}  {'SOURCE':<10}  DESCRIPTION")
        click.echo(f"  {'-' * 24}  {'-' * 10}  {'-' * 40}")
        for entry in self._definitions:
            click.echo(f"  {entry['name']:<24}  {entry['source']:<10}  {entry['description']}")
        click.echo("")
