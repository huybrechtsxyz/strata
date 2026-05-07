"""Command to add a profile to an XYZ Platform solution."""

from datetime import datetime, timezone
from typing import Dict, Optional

import click

from xyz_platform.commands.base_command import BaseCommand
from xyz_platform.models.solution_model import SolutionSpecProfileModel


class AddProfileCommand(BaseCommand):
    """Add a new profile to the current solution.

    Creates a profile entry in ``solution.json``.  If it is the first
    profile, it is automatically set as active.
    """

    OPERATION = "solution_profile_add"
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
        self._added_profile: Dict = {}

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
            error_msg = f"Failed to add profile: {e}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        if not self._profile_name:
            self._errors.append("Profile name is required.")
            return False
        return True

    def _run_execution(self) -> bool:
        """Create the profile in the already-loaded solution and persist."""
        profile = SolutionSpecProfileModel(
            name=self._profile_name,
            active=False,
            created=datetime.now(timezone.utc).isoformat(),
            configfile_paths=[],
            envfile_paths=[],
            datafile_paths=[],
            secretfile_paths=[],
        )

        ok, errors = self._solution_controller.add_profile(profile)
        self._messages.extend(self._solution_controller.get_messages())
        self._errors.extend(errors)
        if not ok:
            return False

        ok, errors = self._solution_controller.save()
        self._errors.extend(errors)
        if not ok:
            return False

        self._added_profile = {
            "name": self._profile_name,
            "active": profile.active,
            "created": profile.created,
        }
        self._output_data = {k: v for k, v in self._added_profile.items() if v is not None}
        return True

    def _after_execute(self) -> bool:
        if not self._is_quiet() and self._added_profile:
            if self._is_console_output():
                click.echo("")
                click.echo(f"  ✅  Added profile:  {self._added_profile['name']}")
                click.echo(f"      Active: {self._added_profile['active']}")
                click.echo("")

        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        return super()._finalize(success=success, show_footer=show_footer)
