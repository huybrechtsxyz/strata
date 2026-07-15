"""Command to activate a profile in an Strata solution."""

from typing import Dict, Optional

import click

from strata.commands.base_command import BaseCommand


class ActivateProfileCommand(BaseCommand):
    """Activate a profile in the current solution.

    Sets the target profile as active and deactivates all others.
    """

    OPERATION = "solution_profile_activate"

    def __init__(
        self,
        name: str,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._profile_name = name

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        return True

    def _execute(self) -> bool:
        """Activate the profile and persist."""
        ok, errors = self._solution_controller.activate_profile(self._profile_name)
        self._messages.extend(self._solution_controller.get_messages())
        self._errors.extend(errors)
        if not ok:
            return False

        ok, errors = self._solution_controller.save()
        self._errors.extend(errors)
        if not ok:
            return False

        self._output_data = {"name": self._profile_name, "active": True}
        return True

    def _after_execute(self) -> bool:
        if not self._is_quiet() and self._is_console_output():
            click.echo("")
            click.echo(f"  ✅  Activated profile:  {self._profile_name}")
            click.echo("")

        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        return super()._finalize(success=success, show_footer=show_footer)
