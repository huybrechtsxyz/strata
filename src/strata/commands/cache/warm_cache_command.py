"""Command to warm the resolved-model cache for one or all registered deployments."""

from __future__ import annotations

from typing import ClassVar, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.controllers.cache_controller import CacheController


class WarmCacheCommand(BaseCommand):
    """Resolve one (or all) deployments and store the result in the cache."""

    OPERATION = "cache_warm"
    SHOW_CHROME: ClassVar[bool] = False

    def __init__(
        self,
        deployment_file: Optional[str] = None,
        warm_all: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._deployment_file = deployment_file
        self._warm_all = warm_all
        self._has_errors = False

    def has_validation_errors(self) -> bool:
        return self._has_errors

    def _initialize(self, show_header: bool = True) -> bool:
        return self._initialize_session(show_header=show_header)

    def _execute(self) -> bool:
        if not self._deployment_file and not self._warm_all:
            self._errors.append("Specify a deployment with --file or use --all to warm every registered deployment.")
            self._has_errors = True
            return False

        controller = CacheController(self._work_path)

        if self._warm_all:
            ok, rows, errors = controller.warm_all()
            self._errors.extend(errors)
            self._output_data["entries"] = rows
            if self._is_console_output():
                click.echo("")
                click.echo("Cache Warm — All Registered Deployments")
                click.echo("-" * 60)
                for row in rows:
                    click.echo(f"{row['name']:<40} {row['indicator']}")
                click.echo("-" * 60)
            self._has_errors = not ok
            return ok

        ok, indicator, errors = controller.warm(self._deployment_file, refresh_cache=True)
        self._errors.extend(errors)
        self._output_data["name"] = self._deployment_file
        self._output_data["indicator"] = indicator
        if self._is_console_output():
            click.echo(f"Cache warm ({indicator}): {self._deployment_file}")
        self._has_errors = not ok
        return ok
