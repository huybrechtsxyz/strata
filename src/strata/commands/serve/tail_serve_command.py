"""Command to show the most recent events on a running strata state-service server."""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional

import click
import requests

from strata.commands.base_command import BaseCommand
from strata.logger import get_logger


class TailServeCommand(BaseCommand):
    """GET <url>/v1/events/tail on a remote strata state-service server (ADR-0065 Step 2.6).

    Workspace-optional, same as every other `serve` HTTP client. Accepts either a
    per-workspace ingest token (always scoped server-side to its own workspace,
    regardless of any `--workspace` filter passed here) or the admin token (can see
    any/all workspaces). Returns a lean projection, not the full stored payload.
    """

    OPERATION = "serve_tail"
    SHOW_CHROME: ClassVar[bool] = False

    def __init__(
        self,
        url: str,
        token: str,
        limit: int = 100,
        workspace: Optional[str] = None,
        timeout: float = 10.0,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self.logger = get_logger(self.__class__.__module__)
        self._url = url.rstrip("/")
        self._token = token
        self._limit = limit
        self._workspace = workspace
        self._timeout = timeout

    def _initialize(self, show_header: bool = True) -> bool:
        # Works without an initialized workspace — the state service is standalone.
        return self._initialize_session(show_header=show_header)

    def _execute(self) -> bool:
        tail_url = f"{self._url}/v1/events/tail"
        params: Dict[str, Any] = {"limit": self._limit}
        if self._workspace:
            params["workspace"] = self._workspace

        try:
            response = requests.get(
                tail_url,
                params=params,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            self._errors.append(f"Failed to reach {tail_url}: {exc}")
            if self._is_console_output():
                click.echo(f"✗  {tail_url} unreachable: {exc}", err=True)
            return False

        if response.status_code != 200:
            self._errors.append(f"{tail_url} returned status {response.status_code}: {response.text}")
            if self._is_console_output():
                click.echo(f"✗  {tail_url} — {response.status_code}: {response.text}", err=True)
            return False

        events = response.json().get("events", [])
        self._output_data["events"] = events
        if self._is_console_output():
            self._print_events(events)
        return True

    def _print_events(self, events: List[Dict[str, Any]]) -> None:
        if not events:
            click.echo("No events found.")
            return
        click.echo(f"{'received_at':<26} {'record_type':<45} {'workspace':<20} {'deployment':<20} {'outcome'}")
        for event in events:
            click.echo(
                f"{event.get('received_at', ''):<26} {event.get('record_type', ''):<45} "
                f"{event.get('workspace', ''):<20} {event.get('deployment', ''):<20} "
                f"{event.get('outcome', '')}"
            )
