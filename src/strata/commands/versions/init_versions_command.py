"""Scaffold a new version-manifest file for a deployment ring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import click

from strata.commands.versions.base_versions_command import BaseVersionsCommand
from strata.controllers.version_controller import VersionController


class InitVersionsCommand(BaseVersionsCommand):
    """Create a starter version-manifest YAML for a given ring.

    The generated file lives at ``<work_path>/versions/<ring>.yaml`` by default,
    or at a custom path when ``--out`` is provided.
    """

    OPERATION = "versions_init"

    def __init__(
        self,
        ring: str,
        out: Optional[str],
        force: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._ring = ring
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

    def _execute(self) -> bool:
        assert self._controller is not None
        dest_raw = self._out or str(Path(str(self._work_path)) / "versions" / f"{self._ring}.yaml")
        dest = Path(dest_raw)

        self._result = self._controller.init_manifest(dest, self._ring, force=self._force)

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
                json.dumps({"success": True, "file": self._result["file"], "ring": self._result["ring"]}, indent=2)
            )
        elif self._output_format == "text":
            click.echo(self._result["file"])
        elif not self._output_quiet:
            click.echo(f"✅  Created {self._result['file']}")
            click.echo(f"    ring: {self._result['ring']}")
            click.echo("    Next: populate spec.pins and run 'strata versions apply'")
