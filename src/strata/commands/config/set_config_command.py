"""Command to set / unset / list persistent CLI defaults in workspace config."""

from typing import Dict, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.commands.cli_common import OUTPUT_FORMATS
from strata.controllers.configuration_controller import ConfigurationController
from strata.logger import get_logger


class SetConfigCommand(BaseCommand):
    """Persist workspace defaults into `.strata/cli.yaml`.

    Usage:
        - set <key> <value>
        - set list
        - set unset <key>
    """

    OPERATION = "config_set"

    ALLOWED_KEYS = ("output", "verbose", "quiet", "work_path")

    def __init__(
        self,
        action: str,
        key: Optional[str] = None,
        value: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self.logger = get_logger(self.__class__.__module__)
        self._action = action
        self._key = key
        self._value = value

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def _initialize(self, show_header: bool = True) -> bool:
        self._initialize_session(show_header=show_header)
        self.logger.debug(
            "SetConfigCommand initializing",
            extra={
                "work_path": str(self._work_path),
            },
        )
        self.logger.debug(
            "SetConfigCommand initialized successfully",
            extra={
                "work_path": str(self._work_path),
            },
        )
        return True

    def _before_execute(self) -> bool:
        return super()._before_execute()

    def _execute(self) -> bool:
        controller = ConfigurationController(self._work_path)

        if self._action == "list":
            values = controller.list_cli_values()
            self._output_data = {"values": values}
            self._messages.append("Workspace values loaded")
            return True

        if self._action == "unset":
            if not self._key:
                self._errors.append("Key required for unset")
                self._finalize(success=False)
                return False
            ok, errs = controller.unset_cli_value(self._key)
            self._messages.extend(controller.get_messages())
            self._errors.extend(errs)
            self._output_data = {"unset": self._key}
            return ok

        # default action == set
        if not self._key or self._value is None:
            self._errors.append("Key and value required for set")
            self._finalize(success=False)
            return False

        key = self._key
        val: Optional[object] = self._value

        # Validate allowed keys
        if key not in self.ALLOWED_KEYS:
            self._errors.append(f"Invalid key: {key}. Allowed: {', '.join(self.ALLOWED_KEYS)}")
            self._finalize(success=False)
            return False

        # Coerce types
        if key in ("verbose", "quiet"):
            sval = str(self._value).lower()
            val = sval not in ("0", "false", "no", "off")

        if key == "output":
            if self._value not in OUTPUT_FORMATS:
                self._errors.append(f"Invalid output value: {self._value}. Allowed: {', '.join(OUTPUT_FORMATS)}")
                self._finalize(success=False)
                return False
            val = self._value

        ok, errs = controller.set_cli_value(key, val)
        self._messages.extend(controller.get_messages())
        self._errors.extend(errs)
        self._output_data = {"set": {key: val}}
        return ok

    def _after_execute(self) -> bool:
        """Render output for the executed action.

        Supports three modes:
        - Console (human-friendly)
        - Structured output handled by BaseCommand (JSON / text)
        - Plain text is produced by BaseCommand when `--output text` is used
        """

        # Derive output data that was populated during _run_execution
        data = self._output_data or {}

        # LIST action: show all values
        if self._action == "list":
            values = data.get("values", {})
            if self._is_console_output():
                click.echo("")
                click.echo("💬  Workspace values:")
                if values:
                    for k, v in values.items():
                        click.echo(f"    • {k}: {v}")
                else:
                    click.echo("    (no values set)")
                click.echo("")

            # structured/plain-text output will be handled by BaseCommand using _output_data
            return super()._after_execute()

        # UNSET action: indicate which key was removed
        if self._action == "unset":
            key = data.get("unset") or self._key
            if self._is_console_output():
                click.echo("")
                click.echo(f"💬  Unset workspace default: {key}")
                click.echo("")
            return super()._after_execute()

        # Default: SET action
        set_map = data.get("set") or {}
        if set_map:
            k = next(iter(set_map))
            v = set_map.get(k)
        else:
            k = getattr(self, "_key", None)
            v = getattr(self, "_value", None)

        if self._is_console_output():
            click.echo("")
            click.echo(f"💬  Setting workspace default: {k} = {v}")
            click.echo("")

        # For structured/plain output, BaseCommand._finalize will use self._output_data
        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        return super()._finalize(success=success, show_footer=show_footer)
