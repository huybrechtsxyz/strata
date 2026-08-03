"""Command to clear the resolved-model cache."""

from __future__ import annotations

from typing import ClassVar, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.controllers.cache_controller import CacheController


class ClearCacheCommand(BaseCommand):
    """Remove every entry from the resolved-model cache."""

    OPERATION = "cache_clear"
    SHOW_CHROME: ClassVar[bool] = False

    def __init__(
        self,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)

    def _initialize(self, show_header: bool = True) -> bool:
        return self._initialize_session(show_header=show_header)

    def _execute(self) -> bool:
        controller = CacheController(self._work_path)
        ok, errors = controller.clear()
        self._errors.extend(errors)
        if self._is_console_output():
            click.echo("Cache cleared." if ok else "Failed to clear cache.")
        return ok
