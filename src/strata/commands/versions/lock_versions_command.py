"""Compute and write spec.hash for a version-manifest file (strata versions lock)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import click

from strata.commands.versions.base_versions_command import BaseVersionsCommand
from strata.controllers.version_controller import VersionController


class LockVersionsCommand(BaseVersionsCommand):
    """Compute a SHA-256 hash over spec.pins and write it to spec.hash in place.

    Locking a version file makes it tamper-evident: any subsequent modification
    to spec.pins will invalidate the hash, causing ``strata deploy -v`` to warn
    (or error when ``--require-lock`` is active).
    """

    OPERATION = "versions_lock"

    def __init__(
        self,
        file: str,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._file = file
        self._controller: Optional[VersionController] = None
        self._result: dict = {}

    def get_required_integrations(self) -> dict:
        return {}

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        self._controller = VersionController()
        return True

    def _run(self) -> bool:
        file_path = Path(self._file)
        if not file_path.is_absolute():
            file_path = Path(str(self._work_path)) / file_path

        self._result = self._controller.lock_manifest(file_path)

        if self._controller.has_errors():
            for err in self._controller.get_errors():
                self._errors.append(err)
            return False

        self._output_data = self._result
        self._render()
        return True

    def _render(self) -> None:
        if self._output_format == "json":
            click.echo(json.dumps({"success": True, **self._result}, indent=2))
        elif self._output_format == "text":
            click.echo(self._result["hash"])
        elif not self._output_quiet:
            click.echo(f"🔒  Locked {self._result['file']}")
            click.echo(f"    hash: {self._result['hash']}")
