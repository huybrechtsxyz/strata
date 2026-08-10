"""Command to list per-workspace ingest tokens on a running strata state-service server."""

from __future__ import annotations

from typing import ClassVar, Optional

import click
import requests

from strata.commands.base_command import BaseCommand
from strata.logger import get_logger


class ListTokensServeCommand(BaseCommand):
    """GET <url>/v1/tokens on a remote strata state-service server (ADR-0065 Step 2.4).

    Never returns the token hash or secret — only token_id/workspace/created_at/revoked_at.
    """

    OPERATION = "serve_token_list"
    SHOW_CHROME: ClassVar[bool] = False

    def __init__(
        self,
        url: str,
        admin_token: str,
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
        self._admin_token = admin_token
        self._workspace = workspace
        self._timeout = timeout

    def _initialize(self, show_header: bool = True) -> bool:
        # Works without an initialized workspace — the state service is standalone.
        return self._initialize_session(show_header=show_header)

    def _execute(self) -> bool:
        tokens_url = f"{self._url}/v1/tokens"
        params = {"workspace": self._workspace} if self._workspace else {}
        try:
            response = requests.get(
                tokens_url,
                params=params,
                headers={"Authorization": f"Bearer {self._admin_token}"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            self._errors.append(f"Failed to reach {tokens_url}: {exc}")
            if self._is_console_output():
                click.echo(f"✗  {tokens_url} unreachable: {exc}", err=True)
            return False

        if response.status_code != 200:
            self._errors.append(f"{tokens_url} returned status {response.status_code}: {response.text}")
            if self._is_console_output():
                click.echo(f"✗  {tokens_url} — {response.status_code}: {response.text}", err=True)
            return False

        tokens = response.json().get("tokens", [])
        self._output_data["tokens"] = tokens
        if self._is_console_output():
            self._print_tokens(tokens)
        return True

    def _print_tokens(self, tokens: list) -> None:
        if not tokens:
            click.echo("No tokens found.")
            return
        click.echo(f"{'token_id':<38} {'workspace':<20} {'created_at':<26} {'revoked_at'}")
        for token in tokens:
            click.echo(
                f"{token.get('token_id', ''):<38} {token.get('workspace', ''):<20} "
                f"{token.get('created_at', ''):<26} {token.get('revoked_at') or '-'}"
            )
