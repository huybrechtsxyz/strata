"""Command to initialize a new XYZ Platform solution workspace."""

from typing import Dict, Optional

import click

from xyz_platform.commands.base_command import BaseCommand
from xyz_platform.logger import get_logger


class InitSolutionCommand(BaseCommand):
    """
    Initialize a new XYZ Platform solution workspace.

    Creates the ``.platform/`` state directory, ``solution.json``, and a
    ``<name>.code-workspace`` file in the work path.
    """

    OPERATION = "solution_init"

    def __init__(
        self,
        name: str,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self.logger = get_logger(self.__class__.__module__)
        self._solution_name = name

    # ------------------------------------------------------------------
    # BaseCommand interface
    # ------------------------------------------------------------------

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

            ok, errors = self._solution_controller.init(self._solution_name)
            self._messages.extend(self._solution_controller.get_messages())
            self._errors.extend(errors)

            if not ok:
                self._finalize(success=False)
                return False

            self._output_data = {
                "solution_name": self._solution_name,
                "solution_id": self._solution_controller.get_solution_id(),
                "work_path": str(self._work_path),
            }

            if not self._after_execute():
                self._finalize(success=False)
                return False

            self._finalize(success=True)
            return True

        except Exception as e:
            error_msg = f"Failed to initialize solution workspace: {e}"
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
            "InitSolutionCommand initializing",
            extra={
                "solution_name": self._solution_name,
                "work_path": str(self._work_path),
            },
        )
        return True

    def _before_execute(self) -> bool:
        return super()._before_execute()

    def _after_execute(self) -> bool:
        if not super()._after_execute():
            return False
        if self._is_console_output():
            click.echo(f"\n✅  Solution '{self._solution_name}' initialised")
            click.echo(f"    • Work path    : {self._work_path}")
            click.echo(f"    • Solution ID  : {self._output_data.get('solution_id', '')}")
        return True

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        return super()._finalize(success=success, show_footer=show_footer)
