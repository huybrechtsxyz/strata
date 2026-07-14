"""Show activity log for a specific promotion (strata promote log)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import click

from strata.commands.promote.base_promote_command import BasePromoteCommand
from strata.controllers.promote_controller import PromoteController


class LogPromoteCommand(BasePromoteCommand):
    """Show the local diagnostic activity log for a specific promotion."""

    OPERATION = "promote_log"

    def __init__(
        self,
        remote: Optional[str] = None,
        module: Optional[str] = None,
        to: str = "",
        version: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._remote = remote
        self._module = module
        self._to = to
        self._version = version
        self._controller: Optional[PromoteController] = None
        self._result: Optional[dict] = None

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
        if not target_name:
            self._errors.append("One of --remote or --module is required.")
            return False

        self._result = self._controller.get_log(
            work_path=Path(str(self._work_path)),
            target_name=target_name,
            to_ring=self._to,
            version=self._version,
        )
        if self._result is None:
            self._errors.append(
                f"No activity log found for '{target_name}' → ring '{self._to}'. "
                "Activity logs are local to the machine that ran promote start."
            )
            return False
        self._output_data = self._result
        self._render()
        return True

    def _render(self) -> None:
        r = self._result or {}
        if self._output_format == "json":
            click.echo(json.dumps({"success": True, **r}, indent=2))
        elif self._output_format == "text":
            for event in r.get("events", []):
                click.echo(f"{event.get('timestamp', '?')}\t{event.get('action', '?')}")
        elif not self._output_quiet:
            click.echo(f"Promotion log: {r.get('target')} → {r.get('ring')}  {r.get('version')}")
            click.echo(f"  Strategy:    {r.get('strategy')} / {r.get('progression')}")
            click.echo(f"  Branch:      {r.get('branch', '(not set)')}")
            click.echo(f"  Prev:        {r.get('previous_version', '(unknown)')}")
            events = r.get("events", [])
            if events:
                click.echo(f"\n  Events ({len(events)}):")
                for ev in events:
                    detail = f"  {ev.get('detail', '')}" if ev.get("detail") else ""
                    click.echo(f"    {ev.get('timestamp', '?')}  {ev.get('action', '?')}{detail}")
