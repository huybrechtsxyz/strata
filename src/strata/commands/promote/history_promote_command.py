"""Query completed promotion records (strata promote history)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import click

from strata.commands.promote.base_promote_command import BasePromoteCommand
from strata.controllers.promote_controller import PromoteController


class HistoryPromoteCommand(BasePromoteCommand):
    """Query completed promotion-record documents from the local records store."""

    OPERATION = "promote_history"

    def __init__(
        self,
        ring: Optional[str] = None,
        remote: Optional[str] = None,
        module: Optional[str] = None,
        last: int = 10,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._ring = ring
        self._remote = remote
        self._module = module
        self._last = last
        self._controller: Optional[PromoteController] = None
        self._result: list = []

    def get_required_integrations(self) -> dict:
        return {}

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        self._controller = PromoteController()
        return True

    def _execute(self) -> bool:
        assert self._controller is not None
        target_name = self._remote or self._module
        self._result = self._controller.get_history(
            work_path=Path(str(self._work_path)),
            ring=self._ring,
            target_name=target_name,
            last=self._last,
        )
        self._output_data = self._result
        self._render()
        return True

    def _render(self) -> None:
        if self._output_format == "json":
            click.echo(json.dumps({"success": True, "records": self._result}, indent=2))
        elif self._output_format == "text":
            for r in self._result:
                click.echo(
                    f"{r.get('started_at', '?')}\t{r.get('target', '?')}\t"
                    f"{r.get('from_version', '?')}→{r.get('to_version', '?')}\t"
                    f"{r.get('ring', '?')}\t{r.get('outcome', '?')}"
                )
        elif not self._output_quiet:
            if not self._result:
                click.echo("No promotion records found.")
                return
            click.echo("Promotion history:")
            for r in self._result:
                icon = "✅" if r.get("outcome") == "completed" else ("↩️" if r.get("outcome") == "rolled-back" else "⚠️")
                click.echo(
                    f"  {icon}  {r.get('started_at', '?')[:10]}  "
                    f"{r.get('target', '?')}  "
                    f"{r.get('from_version', '?')} → {r.get('to_version', '?')}  "
                    f"ring:{r.get('ring', '?')}  "
                    f"[{r.get('outcome', '?')}]"
                )
                if r.get("branch"):
                    click.echo(f"            branch: {r['branch']}")
