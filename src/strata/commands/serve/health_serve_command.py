"""Command to check reachability of a running strata state-service server."""

from __future__ import annotations

from typing import ClassVar, Optional

import click
import requests

from strata.commands.base_command import BaseCommand
from strata.logger import get_logger


class HealthServeCommand(BaseCommand):
    """GET <url>/healthz on a remote strata state-service server.

    Workspace-optional (ADR-0065) — the state service is a standalone process
    shared across many workspaces, not scoped to the one this command happens
    to run from.
    """

    OPERATION = "serve_health"
    SHOW_CHROME: ClassVar[bool] = False

    def __init__(
        self,
        url: str,
        timeout: float = 5.0,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self.logger = get_logger(self.__class__.__module__)
        self._url = url.rstrip("/")
        self._timeout = timeout

    def _initialize(self, show_header: bool = True) -> bool:
        # Works without an initialized workspace — run super for side-effects only.
        return self._initialize_session(show_header=show_header)

    def _execute(self) -> bool:
        healthz_url = f"{self._url}/healthz"
        try:
            response = requests.get(healthz_url, timeout=self._timeout)
            reachable = response.status_code == 200
            self._output_data["url"] = healthz_url
            self._output_data["reachable"] = reachable
            self._output_data["status_code"] = response.status_code
            if not reachable:
                self._errors.append(f"{healthz_url} returned status {response.status_code}")
            if self._is_console_output():
                self._print_result(reachable, response.status_code)
            return reachable
        except requests.RequestException as exc:
            self._output_data["url"] = healthz_url
            self._output_data["reachable"] = False
            self._errors.append(f"Failed to reach {healthz_url}: {exc}")
            if self._is_console_output():
                click.echo(f"✗  {healthz_url} unreachable: {exc}", err=True)
            return False

    def _print_result(self, reachable: bool, status_code: int) -> None:
        if reachable:
            click.echo(f"✓  {self._url}/healthz — {status_code}")
        else:
            click.echo(f"✗  {self._url}/healthz — {status_code}", err=True)
