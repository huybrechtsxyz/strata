"""Load a version file and print the fully-resolved pin map."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import click

from strata.commands.versions.base_versions_command import BaseVersionsCommand
from strata.controllers.version_controller import VersionController
from strata.utils.system import resolve_path


class ExportVersionsCommand(BaseVersionsCommand):
    """Export the resolved version pins from a version-manifest or version-lock file.

    The output is a flat mapping of ``{type: {name: version}}`` entries.
    """

    OPERATION = "versions_export"

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

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        self._controller = VersionController()
        return True

    def _run(self) -> bool:
        assert self._controller is not None
        file_path = Path(resolve_path(str(self._work_path), self._file))
        self._result = self._controller.export_pins(file_path)

        if self._controller.has_errors():
            for err in self._controller.get_errors():
                self._errors.append(err)
            return False

        self._output_data = self._result
        self._render()
        return True

    # ── output ────────────────────────────────────────────────────────────────

    def _render(self) -> None:
        pins: dict = self._result.get("pins", {})

        if self._output_format == "json":
            click.echo(json.dumps({"success": True, "pins": pins}, indent=2))
        elif self._output_format == "text":
            for type_key, entries in sorted(pins.items()):
                for name, version in sorted(entries.items()):
                    click.echo(f"{type_key}/{name}={version}")
        elif not self._output_quiet:
            if not pins:
                click.echo("  (no resolved pins)")
                return
            click.echo("")
            for type_key, entries in sorted(pins.items()):
                click.echo(f"  {type_key.upper()}")
                for name, version in sorted(entries.items()):
                    click.echo(f"    {name:<32}  {version}")
            click.echo("")
