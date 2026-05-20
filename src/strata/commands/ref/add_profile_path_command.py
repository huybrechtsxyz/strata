"""Command to add a configuration path to a profile in an Strata solution."""

from datetime import datetime, timezone
from typing import Dict, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.models.solution_model import SolutionSpecProfileConfigModel


class AddProfilePathCommand(BaseCommand):
    """Add a configuration path entry to a profile.

    Adds a named path entry of the given type (config, dotenv, data, secret)
    to the specified profile in ``solution.json``.
    """

    OPERATION = "solution_profile_path_add"
    INIT_REQUIRED = True

    def __init__(
        self,
        profile: str,
        type: str,
        name: str,
        path: str,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._profile_name = profile
        self._path_type = type
        self._path_name = name
        self._path_value = path
        self._added_path: Dict = {}

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
            error_msg = f"Failed to add profile path: {e}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        if not self._profile_name:
            active, errors = self._solution_controller.get_active_profile()
            if errors or active is None:
                self._errors.append("No profile specified and no active profile found. Use 'strata profile add' first.")
                return False
            self._profile_name = str(active.name)
        if not self._path_name:
            self._errors.append("Path name is required.")
            return False
        if not self._path_value:
            self._errors.append("Path value is required.")
            return False
        return True

    def _run_execution(self) -> bool:
        """Add the path to the profile and persist."""
        config = SolutionSpecProfileConfigModel(
            name=self._path_name,
            path=self._path_value,
            type=self._path_type,
            created=datetime.now(timezone.utc).isoformat(),
        )

        ok, errors = self._solution_controller.add_profile_path(self._profile_name, self._path_type, config)
        self._messages.extend(self._solution_controller.get_messages())
        self._errors.extend(errors)
        if not ok:
            return False

        ok, errors = self._solution_controller.save()
        self._errors.extend(errors)
        if not ok:
            return False

        self._added_path = {
            "profile": self._profile_name,
            "type": self._path_type,
            "name": self._path_name,
            "path": self._path_value,
        }
        self._output_data = {k: v for k, v in self._added_path.items() if v is not None}
        return True

    def _after_execute(self) -> bool:
        if not self._is_quiet() and self._added_path:
            if self._is_console_output():
                click.echo("")
                click.echo(f"  ✅  Added {self._path_type} path to profile '{self._profile_name}':")
                click.echo(f"      Name: {self._path_name}")
                click.echo(f"      Path: {self._path_value}")
                click.echo("")

        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        return super()._finalize(success=success, show_footer=show_footer)
