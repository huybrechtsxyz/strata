"""Command to set / unset / list team-shared template variables in solution.json."""

from pathlib import Path
from typing import Dict, Optional

import click

from xyz_platform.commands.base_command import BaseCommand
from xyz_platform.controllers.context_controller import ContextController
from xyz_platform.logger import get_logger


class SetContextCommand(BaseCommand):
    """Manage ``spec.context`` in solution.json — team-shared template defaults.

    Actions:
        set   — store ``key = value`` in spec.context
        unset — remove ``key`` from spec.context
        list  — display all key/value pairs
    """

    OPERATION = "context_set"
    INIT_REQUIRED = True

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

    def execute(self) -> bool:
        try:
            if not self._initialize():
                self.logger.error(f"Initialization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(success=False)
                return False

            if not self._before_execute():
                self.logger.error(f"Pre-execution validation failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(success=False)
                return False

            if not self._run_execution():
                self.logger.error(f"Execution failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Execution failed")
                self._finalize(success=False)
                return False

            if not self._after_execute():
                self.logger.error(f"Post-execution processing failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Post-execution processing failed")
                self._finalize(success=False)
                return False

            self._finalize(success=True)
            return True

        except Exception as e:
            error_msg = f"Failed to execute context command: {e}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def _initialize(self, show_header: bool = True) -> bool:
        if not super()._initialize(show_header=show_header):
            return False
        self.logger.debug(
            "SetContextCommand initializing",
            action=self._action,
            work_path=str(self._work_path),
        )
        return True

    def _before_execute(self) -> bool:
        return super()._before_execute()

    def _run_execution(self) -> bool:
        controller = ContextController(self._work_path)

        if self._action == "list":
            ok, values, errors = controller.list()
            self._errors.extend(errors)
            if not ok:
                return False
            self._output_data = {"values": values}
            self._messages.append("Context variables loaded")
            return True

        if self._action == "unset":
            if not self._key:
                self._errors.append("Key required for unset")
                return False
            ok, errors = controller.unset(self._key)
            self._messages.extend(controller.get_messages())
            self._errors.extend(errors)
            self._output_data = {"unset": self._key}
            return ok

        # Default action: set
        if not self._key or self._value is None:
            self._errors.append("Key and value required for set")
            return False

        ok, errors = controller.set(self._key, self._value)
        self._messages.extend(controller.get_messages())
        self._errors.extend(errors)
        self._output_data = {"set": {self._key: self._value}}
        return ok

    def _after_execute(self) -> bool:
        data = self._output_data or {}

        if self._action == "list":
            values = data.get("values", {})
            if self._is_console_output():
                click.echo("")
                click.echo("💬  Context variables:")
                if values:
                    for k, v in values.items():
                        click.echo(f"    • {k}: {v}")
                else:
                    click.echo("    (no template variables set)")
                click.echo("")
            return super()._after_execute()

        if self._action == "unset":
            key = data.get("unset") or self._key
            if self._is_console_output():
                click.echo("")
                click.echo(f"💬  Removed context variable: {key}")
                click.echo("")
            return super()._after_execute()

        # SET action
        key = self._key
        value = self._value
        if self._is_console_output():
            click.echo("")
            click.echo(f"💬  Set context variable: {key} = {value}")
            click.echo("")
        return super()._after_execute()
