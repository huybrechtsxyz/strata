"""Command to mask a secret value for safe display."""

from __future__ import annotations

from typing import Dict, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.utils.secret_generator import mask_secret as mask_secret  # noqa: F401 — re-exported for CLI use


class MaskSecretCommand(BaseCommand):
    """Mask a secret value, keeping a configurable number of leading characters visible.

    A pure, stateless utility — it needs no workspace or ``solution.json`` at
    all. Console and ``--output text`` modes intentionally print the bare
    masked value only (no envelope, no chrome) so the output stays pipeable;
    only ``--output json`` uses the standard structured envelope.
    """

    OPERATION = "secret_mask"
    SHOW_CHROME = False  # Utility command — no header/footer banner.

    def __init__(
        self,
        value: str,
        show: int = 4,
        char: str = "*",
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._value = value
        self._show = show
        self._char = char

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
        if len(self._char) != 1:
            raise click.UsageError("--char must be exactly one character.")

        masked = mask_secret(self._value, show=self._show, char=self._char)

        self._output_data = {"masked": masked, "show": self._show, "char": self._char}

        if self._output_format != "json":
            # console (default) and text — bare value only, so it can be piped directly.
            click.echo(masked)

        return True
