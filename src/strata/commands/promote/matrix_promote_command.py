"""Show version matrix across rings (strata promote matrix)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import click

from strata.commands.promote.base_promote_command import BasePromoteCommand
from strata.controllers.promote_controller import PromoteController


class MatrixPromoteCommand(BasePromoteCommand):
    """Show version matrix across all rings by reading versions/*.yaml lock files directly.

    No fleet-wide EnvironmentService traversal needed — lock files are the version index.
    Un-locked targets fall back to showing '(not pinned)'.
    """

    OPERATION = "promote_matrix"

    def __init__(
        self,
        remote: Optional[str] = None,
        module: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._remote = remote
        self._module = module
        self._controller: Optional[PromoteController] = None
        self._result: dict = {}

    def get_required_integrations(self) -> dict:
        return {}

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        self._controller = PromoteController()
        return True

    def _run(self) -> bool:
        assert self._controller is not None
        target_name = self._remote or self._module
        self._result = self._controller.get_matrix(
            Path(str(self._work_path)),
            target_name=target_name,
        )
        if self._controller.has_errors():
            for err in self._controller.get_errors():
                self._errors.append(err)
            return False
        self._output_data = self._result
        self._render()
        return True

    def _render(self) -> None:
        rings = self._result.get("rings", [])
        if self._output_format == "json":
            click.echo(json.dumps({"success": True, "matrix": self._result}, indent=2))
        elif self._output_format == "text":
            for r in rings:
                for target, version in r.get("versions", {}).items():
                    click.echo(f"{r['ring']}\t{target}\t{version}")
        elif not self._output_quiet:
            if not rings:
                click.echo("No version matrix data found.")
                return
            for ring_data in rings:
                ring = ring_data["ring"]
                envs = ", ".join(ring_data.get("environments", []))
                req = f" (require: {ring_data['require']})" if ring_data.get("require") else ""
                click.echo(f"\nRing: {ring}{req}")
                click.echo(f"  Environments: {envs or '(none)'}")
                versions = ring_data.get("versions", {})
                if versions:
                    for target, version in sorted(versions.items()):
                        click.echo(f"  {target:<40} {version}")
                else:
                    click.echo("  (no pinned versions)")
