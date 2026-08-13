"""Command to resolve a strata:// URI to a file and line."""

from typing import Dict, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.controllers.diagram_resolve_controller import DiagramResolveController


class ResolveDiagramCommand(BaseCommand):
    """Resolve a ``strata://`` URI to the workspace location it names."""

    OPERATION = "diagram_resolve"

    def __init__(
        self,
        uri: str,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._uri = uri
        self._location: Optional[dict] = None

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def _initialize(self, show_header: bool = True) -> bool:
        # Works without an initialized workspace — this is a read-only lookup.
        return self._initialize_session(show_header=show_header)

    def _execute(self) -> bool:
        controller = DiagramResolveController(work_path=self._work_path)
        location = controller.resolve(self._uri)
        if location is None:
            self._errors.extend(controller.get_errors())
            return False
        self._location = location
        self._output_data = location
        return True

    def _after_execute(self) -> bool:
        if self._location and self._is_console_output() and not self._output_quiet:
            line = self._location.get("line")
            click.echo(f"{self._location['file']}:{line}" if line else self._location["file"])
        return super()._after_execute()
