"""Command to revoke a per-workspace ingest token on a running strata state-service server."""

from __future__ import annotations

from typing import ClassVar, Optional

import click
import requests

from strata.commands.base_command import BaseCommand
from strata.logger import get_logger


class RevokeTokenServeCommand(BaseCommand):
    """DELETE <url>/v1/tokens/<token_id> on a remote strata state-service server (ADR-0065 Step 2.4)."""

    OPERATION = "serve_token_revoke"
    SHOW_CHROME: ClassVar[bool] = False

    def __init__(
        self,
        url: str,
        admin_token: str,
        token_id: str,
        timeout: float = 10.0,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self.logger = get_logger(self.__class__.__module__)
        self._url = url.rstrip("/")
        self._admin_token = admin_token
        self._token_id = token_id
        self._timeout = timeout

    def _initialize(self, show_header: bool = True) -> bool:
        # Works without an initialized workspace — the state service is standalone.
        return self._initialize_session(show_header=show_header)

    def _execute(self) -> bool:
        token_url = f"{self._url}/v1/tokens/{self._token_id}"
        try:
            response = requests.delete(
                token_url,
                headers={"Authorization": f"Bearer {self._admin_token}"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            self._errors.append(f"Failed to reach {token_url}: {exc}")
            if self._is_console_output():
                click.echo(f"✗  {token_url} unreachable: {exc}", err=True)
            return False

        if response.status_code != 200:
            self._errors.append(f"{token_url} returned status {response.status_code}: {response.text}")
            if self._is_console_output():
                click.echo(f"✗  {token_url} — {response.status_code}: {response.text}", err=True)
            return False

        self._output_data["token_id"] = self._token_id
        self._output_data["revoked"] = True
        if self._is_console_output():
            click.echo(f"✓  Token revoked: {self._token_id}")
        return True
