"""Command to export the resolved-model cache to JSON for debugging."""

from __future__ import annotations

from typing import ClassVar, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.controllers.cache_controller import CacheController


class ExportCacheCommand(BaseCommand):
    """Write the full cache (decompressed) to a JSON file."""

    OPERATION = "cache_export"
    SHOW_CHROME: ClassVar[bool] = False

    def __init__(
        self,
        output_path: str = "cache-export.json",
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._output_path = output_path

    def _initialize(self, show_header: bool = True) -> bool:
        return self._initialize_session(show_header=show_header)

    def _execute(self) -> bool:
        controller = CacheController(self._work_path)
        ok, errors = controller.export(self._output_path)
        self._errors.extend(errors)
        self._output_data["path"] = self._output_path
        if self._is_console_output():
            click.echo(f"Cache exported to: {self._output_path}" if ok else "Failed to export cache.")
        return ok
