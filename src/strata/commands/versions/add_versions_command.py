"""Create a new version-manifest snapshot file (strata versions add)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import click

from strata.commands.versions.base_versions_command import BaseVersionsCommand
from strata.controllers.version_controller import VersionController


class AddVersionsCommand(BaseVersionsCommand):
    """Scaffold a new version-manifest snapshot file.

    When ``--from`` is given the pins are copied from an existing version file.
    The resulting file is the canonical source that ``strata promote`` will
    reference when writing ring lock files.
    """

    OPERATION = "versions_add"

    def __init__(
        self,
        out: str,
        ring: str,
        from_file: Optional[str] = None,
        force: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._out = out
        self._ring = ring
        self._from_file = from_file
        self._force = force
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
        assert self._controller is not None
        dest = Path(self._out)
        if not dest.is_absolute():
            dest = Path(str(self._work_path)) / dest

        from_path: Optional[Path] = None
        if self._from_file:
            from_path = Path(self._from_file)
            if not from_path.is_absolute():
                from_path = Path(str(self._work_path)) / from_path

        self._result = self._controller.add_manifest(
            dest=dest,
            ring=self._ring,
            from_file=from_path,
            force=self._force,
        )

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
            click.echo(self._result["file"])
        elif not self._output_quiet:
            click.echo(f"✅  Created {self._result['file']}")
            click.echo(f"    ring: {self._result['ring']}")
            if self._result.get("from"):
                click.echo(f"    from: {self._result['from']}")
            click.echo("    Next: review pins, then run 'strata versions lock' to lock the file")
