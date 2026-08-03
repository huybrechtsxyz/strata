"""Command to show resolved-model cache status."""

from __future__ import annotations

from typing import ClassVar, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.controllers.cache_controller import CacheController


class StatusCacheCommand(BaseCommand):
    """Show cache hit/stale/cold state, or list all cached entries."""

    OPERATION = "cache_status"
    SHOW_CHROME: ClassVar[bool] = False

    def __init__(
        self,
        deployment_file: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._deployment_file = deployment_file
        self._has_errors = False

    def has_validation_errors(self) -> bool:
        return self._has_errors

    def _initialize(self, show_header: bool = True) -> bool:
        return self._initialize_session(show_header=show_header)

    def _execute(self) -> bool:
        controller = CacheController(self._work_path)
        ok, rows, errors = controller.status(self._deployment_file)
        self._errors.extend(errors)
        self._output_data["entries"] = rows
        self._has_errors = not ok

        if not ok:
            return False

        if self._is_console_output():
            click.echo("")
            click.echo(f"Model Cache — {controller.cache.db_path}")
            click.echo("-" * 70)
            if self._deployment_file:
                for row in rows:
                    click.echo(f"{row['name']:<40} {row['status']}")
            elif not rows:
                click.echo("(empty — no entries cached yet; run 'strata cache warm --all')")
            else:
                header = f"{'Name':<32} {'Kind':<12} {'Size':<10} {'Written At':<26} {'Version'}"
                click.echo(header)
                click.echo("-" * 70)
                for row in rows:
                    click.echo(
                        f"{row['name']:<32} {row['kind']:<12} {row['size_bytes']:<10} "
                        f"{row['written_at']:<26} {row['strata_version']}"
                    )
            click.echo("-" * 70)

        return True
