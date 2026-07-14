"""Command to remove a profile from an Strata solution."""

from typing import Dict, Optional

import click

from strata.commands.base_command import BaseCommand


class RemoveProfileCommand(BaseCommand):
    """Remove a profile from the current solution.

    The profile must not be active — activate another profile first.
    """

    OPERATION = "solution_profile_remove"
    INIT_REQUIRED = True

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
        self._removed_profile: Dict = {}

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        return True

    def _execute(self) -> bool:
        """Capture profile metadata, remove from solution, persist."""
        profiles, errors = self._solution_controller.get_profiles(self._profile_name)
        if errors:
            self._errors.extend(errors)
            return False

        profile = profiles[0]
        self._removed_profile = {
            "name": str(profile.name),
            "active": profile.active,
        }

        ok, errors = self._solution_controller.remove_profile(self._profile_name)
        if not ok:
            self._errors.extend(errors)
            return False

        ok, errors = self._solution_controller.save()
        if not ok:
            self._errors.extend(errors)
            return False

        self._output_data = {"profile": self._removed_profile}
        return True

    def _after_execute(self) -> bool:
        if not self._is_quiet() and self._removed_profile:
            if self._is_console_output():
                click.echo("")
                click.echo(f"  🗑️   Removed profile:  {self._removed_profile['name']}")
                click.echo("")

        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        return super()._finalize(success=success, show_footer=show_footer)
