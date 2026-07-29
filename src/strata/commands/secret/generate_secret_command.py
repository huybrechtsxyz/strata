"""Command to generate a cryptographically secure secret value."""

from __future__ import annotations

from typing import Any, Dict, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.utils.secret_generator import (  # noqa: F401 — re-exported for CLI use
    _ALPHANUMERIC,
    _NUMERIC,
    _PASSWORD_CHARS,
    _SYMBOLS,
    _uuid7,
    generate_secret,
)

_UUID_FORMATS = {"uuid4", "uuid7"}


class GenerateSecretCommand(BaseCommand):
    """Generate a cryptographically secure secret value.

    A pure, stateless utility — it needs no workspace or ``solution.json`` at
    all. Console and ``--output text`` modes intentionally print the bare
    value only (no envelope, no chrome) so the output stays pipeable directly
    into another command (e.g. ``secret put --value $(strata secret generate)``);
    only ``--output json`` uses the standard structured envelope.
    """

    OPERATION = "secret_generate"
    SHOW_CHROME = False  # Utility command — no header/footer banner.

    def __init__(
        self,
        fmt: str = "urlsafe",
        length: int = 32,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._fmt = fmt
        self._length = length

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def _initialize(self, show_header: bool = True) -> bool:
        # Pure utility command — works with no workspace/solution.json at all.
        return self._initialize_session(show_header=show_header)

    def _is_structured_output(self) -> bool:
        """Only ``--output json`` gets the structured envelope.

        ``--output text`` intentionally behaves like the console default
        (bare value only) to stay pipeable — this command's entire purpose
        is producing a single value for scripting, not a report.
        """
        return self._output_format == "json"

    def _execute(self) -> bool:
        try:
            value = generate_secret(self._fmt, self._length)
        except ValueError as exc:
            raise click.UsageError(str(exc)) from exc

        data: Dict[str, Any] = {"secret": value, "format": self._fmt}
        if self._fmt not in _UUID_FORMATS:
            data["length"] = self._length
        self._output_data = data

        if self._output_format != "json":
            # console (default) and text — bare value only, so it can be piped directly.
            click.echo(value)

        return True
