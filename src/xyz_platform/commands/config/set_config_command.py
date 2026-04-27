"""Command to set / unset / list persistent CLI defaults in workspace config."""

from typing import Dict, Optional

from xyz_platform.commands.base_command import BaseCommand
from xyz_platform.commands.cli_common import OUTPUT_FORMATS
from xyz_platform.controllers.configuration_controller import ConfigurationController
from xyz_platform.logger import get_logger


class SetConfigCommand(BaseCommand):
    """Persist workspace defaults into `.platform/cli.yaml`.

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

    def execute(self) -> bool:
        try:
            if not self._initialize():
                self._finalize(success=False)
                return False

            if not self._before_execute():
                self._finalize(success=False)
                return False

            controller = ConfigurationController(self._work_path)

            if self._action == "list":
                values = controller.list_cli_values()
                self._output_data = {"values": values}
                self._messages.append("Workspace values loaded")
                self._finalize(success=True)
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
                self._finalize(success=ok)
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
            self._finalize(success=ok)
            return ok

        except Exception as e:
            error_msg = f"Failed to execute set command: {e}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False
