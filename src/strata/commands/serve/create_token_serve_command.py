"""Command to create a per-workspace ingest token on a running strata state-service server."""

from __future__ import annotations

from typing import ClassVar, Optional

import click
import requests

from strata.commands.base_command import BaseCommand
from strata.logger import get_logger


class CreateTokenServeCommand(BaseCommand):
    """POST <url>/v1/tokens on a remote strata state-service server (ADR-0065 Step 2.4).

    Admin-token-protected — a separate, higher-privilege credential than the
    per-workspace ingest tokens this command creates. The secret is printed
    exactly once; only its hash is ever stored server-side.
    """

    OPERATION = "serve_token_create"
    SHOW_CHROME: ClassVar[bool] = False

    def __init__(
        self,
        url: str,
        admin_token: str,
        workspace: str,
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
        self._workspace = workspace
        self._timeout = timeout

    def _initialize(self, show_header: bool = True) -> bool:
        # Works without an initialized workspace — the state service is standalone.
        return self._initialize_session(show_header=show_header)

    def _execute(self) -> bool:
        tokens_url = f"{self._url}/v1/tokens"
        try:
            response = requests.post(
                tokens_url,
                params={"workspace": self._workspace},
                headers={"Authorization": f"Bearer {self._admin_token}"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            self._errors.append(f"Failed to reach {tokens_url}: {exc}")
            if self._is_console_output():
                click.echo(f"✗  {tokens_url} unreachable: {exc}", err=True)
            return False

        if response.status_code != 201:
            self._errors.append(f"{tokens_url} returned status {response.status_code}: {response.text}")
            if self._is_console_output():
                click.echo(f"✗  {tokens_url} — {response.status_code}: {response.text}", err=True)
            return False

        data = response.json()
        self._output_data["token_id"] = data.get("token_id")
        self._output_data["token"] = data.get("token")
        self._output_data["workspace"] = self._workspace
        if self._is_console_output():
            click.echo(f"✓  Token created for workspace '{self._workspace}'")
            click.echo(f"  token_id: {data.get('token_id')}")
            click.echo(f"  token:    {data.get('token')}")
            click.echo("  Save this token now — it will not be shown again.")
        return True
