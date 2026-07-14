"""Show in-flight promotions (strata promote status)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import click

from strata.commands.promote.base_promote_command import BasePromoteCommand
from strata.controllers.promote_controller import PromoteController


class StatusPromoteCommand(BasePromoteCommand):
    """Show all in-flight promotions from the local activity log directory."""

    OPERATION = "promote_status"

    def __init__(
        self,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._controller: Optional[PromoteController] = None
        self._result: list = []

    def get_required_integrations(self) -> dict:
        return {}

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        self._controller = PromoteController()
        return True

    def _run(self) -> bool:
        self._result = self._controller.get_status(Path(str(self._work_path)))
        self._output_data = self._result
        self._render()
        return True

    def _render(self) -> None:
        if self._output_format == "json":
            click.echo(json.dumps({"success": True, "promotions": self._result}, indent=2))
        elif self._output_format == "text":
            for p in self._result:
                click.echo(f"{p['target']}\t{p.get('version', '?')}\t{p['ring']}\t{p['status']}")
        elif not self._output_quiet:
            if not self._result:
                click.echo("No in-flight promotions found.")
                return
            click.echo("In-flight promotions:")
            for p in self._result:
                status_icon = "🔄" if p["status"] == "in-progress" else "✅"
                click.echo(
                    f"  {status_icon}  {p['target']} → {p['ring']}  "
                    f"{p.get('previous_version', '?')} → {p.get('version', '?')}  "
                    f"[{p['status']}]"
                )
                if p.get("branch"):
                    click.echo(f"       branch: {p['branch']}")
