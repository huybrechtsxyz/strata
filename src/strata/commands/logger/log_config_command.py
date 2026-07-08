"""Command to manage the workspace logging configuration (logging.yaml)."""

from typing import Any, Dict, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.controllers.logging_controller import LoggingController


class LogConfigCommand(BaseCommand):
    """Read and write `.strata/logging.yaml` workspace logging settings.

    Actions:
    - ``list``   — print the full logging.yaml content
    - ``get``    — retrieve a single key (dot-notation)
    - ``set``    — write a key/value (dot-notation);  ``level`` is a shorthand
                   that updates both handler and logger levels at once
    - ``unset``  — remove a key (dot-notation)
    - ``reset``  — restore the workspace logging.yaml to the package default
    """

    OPERATION = "log_config"
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
        self._action = action
        self._key = key
        self._value = value
        self._result: Dict[str, Any] = {}

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def _before_execute(self) -> bool:
        return super()._before_execute()

    def _run(self) -> bool:
        ctrl = LoggingController(self._work_path)

        if self._action == "list":
            data = ctrl.list_values()
            self._result = {"config": data}
            self._output_data = self._result
            return True

        if self._action == "get":
            if not self._key:
                self._errors.append("Key required for 'get'.")
                return False
            found, value = ctrl.get_value(self._key)
            if not found:
                self._errors.append(f"Key '{self._key}' not found in logging.yaml.")
                return False
            self._result = {self._key: value}
            self._output_data = self._result
            return True

        if self._action == "set":
            if not self._key or self._value is None:
                self._errors.append("Key and value required for 'set'.")
                return False
            ok, errors = ctrl.set_value(self._key, self._value)
            self._errors.extend(errors)
            if ok:
                self._result = {"key": self._key, "value": self._value}
                self._output_data = self._result
                self._messages.append(f"Set {self._key} = {self._value}")
            return ok

        if self._action == "unset":
            if not self._key:
                self._errors.append("Key required for 'unset'.")
                return False
            ok, errors = ctrl.unset_value(self._key)
            self._errors.extend(errors)
            if ok:
                self._result = {"unset": self._key}
                self._output_data = self._result
                self._messages.append(f"Removed {self._key} from logging.yaml")
            return ok

        if self._action == "reset":
            ok, errors = ctrl.reset()
            self._errors.extend(errors)
            if ok:
                self._result = {"reset": True}
                self._output_data = self._result
                self._messages.append("Logging config reset to package default")
            return ok

        self._errors.append(f"Unknown action '{self._action}'.")
        return False

    def _after_execute(self) -> bool:
        if not self._is_quiet() and self._is_console_output():
            click.echo("")

            if self._action == "list":
                config = self._result.get("config", {})
                if not config:
                    click.echo("  ℹ️   No workspace logging.yaml found (using package defaults).\n")
                else:
                    click.echo("  📋  Logging configuration:\n")
                    self._print_yaml_tree(config, indent=4)
                    click.echo("")

            elif self._action == "get":
                for k, v in self._result.items():
                    click.echo(f"  {k}: {v}\n")

            elif self._action == "set":
                click.echo(f"  ✅  {self._key} = {self._value}\n")

            elif self._action == "unset":
                click.echo(f"  🗑️   Removed: {self._key}\n")

            elif self._action == "reset":
                click.echo("  ✅  Logging configuration reset to package defaults.\n")

        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        return super()._finalize(success=success, show_footer=show_footer)

    @staticmethod
    def _print_yaml_tree(data: Any, indent: int = 0) -> None:
        """Recursively print a dict/list as indented key: value pairs."""
        prefix = " " * indent
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, (dict, list)):
                    click.echo(f"{prefix}{k}:")
                    LogConfigCommand._print_yaml_tree(v, indent + 2)
                else:
                    click.echo(f"{prefix}{k}: {v}")
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, (dict, list)):
                    click.echo(f"{prefix}-")
                    LogConfigCommand._print_yaml_tree(item, indent + 2)
                else:
                    click.echo(f"{prefix}- {item}")
        else:
            click.echo(f"{prefix}{data}")
