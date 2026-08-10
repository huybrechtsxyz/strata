"""Command to apply/verify the strata state-service event-store schema."""

from __future__ import annotations

from typing import ClassVar, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.logger import get_logger


class MigrateServeCommand(BaseCommand):
    """Apply/verify the `events` table schema against a configured database.

    Run separately from `serve run` — a deliberate privilege split (ADR-0065
    Step 2.2): this is the one place anything needs CREATE TABLE/ALTER TABLE
    rights, while `serve run`'s long-lived connection only ever needs
    INSERT/SELECT on an already-existing table.
    """

    OPERATION = "serve_migrate"
    SHOW_CHROME: ClassVar[bool] = False

    def __init__(
        self,
        db_url: str,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self.logger = get_logger(self.__class__.__module__)
        self._db_url = db_url

    def _initialize(self, show_header: bool = True) -> bool:
        # Works without an initialized workspace — the state service is standalone.
        return self._initialize_session(show_header=show_header)

    def _execute(self) -> bool:
        try:
            from strata.server.db.engine import create_engine_from_url
            from strata.server.db.schema import metadata
        except ImportError as exc:
            message = "The 'server' optional dependency is required.\nInstall it with: pip install xyz-strata[server]"
            self._errors.append(message)
            if self._is_console_output():
                click.echo(f"✗  {message}\n({exc})", err=True)
            return False

        try:
            engine = create_engine_from_url(self._db_url)
            try:
                metadata.create_all(engine, checkfirst=True)
            finally:
                engine.dispose()
        except Exception as exc:
            self._errors.append(f"Failed to apply schema: {exc}")
            if self._is_console_output():
                click.echo(f"✗  Failed to apply schema: {exc}", err=True)
            return False

        self._output_data["db_url"] = self._db_url
        self._output_data["applied"] = True
        if self._is_console_output():
            click.echo(f"✓  schema applied: {self._db_url}")
        return True
