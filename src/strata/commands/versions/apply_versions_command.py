"""Convert a version-manifest into a version-lock file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import click

from strata.commands.versions.base_versions_command import BaseVersionsCommand
from strata.controllers.version_controller import VersionController
from strata.utils.system import resolve_path


class ApplyVersionsCommand(BaseVersionsCommand):
    """Convert a version-manifest (``kind: version``) into a version-lock file.

    By default the lock file is written alongside the manifest with the
    ``.lock.yaml`` extension.  Use ``--out`` to choose a custom path.
    """

    OPERATION = "versions_apply"

    def __init__(
        self,
        file: str,
        out: Optional[str],
        force: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._file = file
        self._out = out
        self._force = force
        self._controller: Optional[VersionController] = None
        self._result: dict = {}

    def get_required_integrations(self) -> dict:
        return {}

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        self._controller = VersionController()
        return True

    def _run(self) -> bool:
        assert self._controller is not None
        file_path = Path(resolve_path(str(self._work_path), self._file))

        if self._out:
            lock_path = Path(self._out)
        else:
            lock_path = file_path.with_suffix("").with_suffix(".lock.yaml")

        self._result = self._controller.apply_manifest(file_path, lock_path, force=self._force)

        if self._controller.has_errors():
            for err in self._controller.get_errors():
                self._errors.append(err)
            return False

        self._output_data = self._result
        self._render()
        return True

    # ── output ────────────────────────────────────────────────────────────────

    def _render(self) -> None:
        if self._output_format == "json":
            click.echo(
                json.dumps(
                    {
                        "success": True,
                        "lock_file": self._result["lock_file"],
                        "ring": self._result["ring"],
                        "pins_count": self._result["pins_count"],
                    },
                    indent=2,
                )
            )
        elif self._output_format == "text":
            click.echo(self._result["lock_file"])
        elif not self._output_quiet:
            click.echo(f"✅  Lock file written: {self._result['lock_file']}")
            click.echo(f"    ring:  {self._result['ring']}")
            click.echo(f"    pins:  {self._result['pins_count']}")
