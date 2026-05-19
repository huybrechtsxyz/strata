"""Command to list profiles registered in an XYZ Platform solution."""

from typing import Dict, List, Optional

import click

from strata.commands.base_command import BaseCommand


class ListProfileCommand(BaseCommand):
    """List profiles registered in the current solution.

    Prints all profile entries from ``solution.json``, showing name,
    active status, and creation timestamp.
    """

    OPERATION = "solution_profile_list"
    INIT_REQUIRED = True

    def __init__(
        self,
        name: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._filter_name = name
        self._profiles: List[Dict] = []

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
            error_msg = f"Failed to list profiles: {e}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        return True

    def _run_execution(self) -> bool:
        """Read profiles from the already-loaded solution."""
        profiles, errors = self._solution_controller.get_profiles(self._filter_name)
        if errors:
            self._errors.extend(errors)
            return False

        self._profiles = [
            {
                "name": str(p.name),
                "active": p.active,
                "created": p.created,
            }
            for p in profiles
        ]
        self._output_data = {"profiles": self._profiles}
        return True

    def _after_execute(self) -> bool:
        if not self._is_quiet() and self._is_console_output():
            if not self._profiles:
                click.echo("\n  📋  No profiles registered in solution.\n")
            else:
                click.echo(f"\n  📋  Profiles ({len(self._profiles)}):\n")
                for p in self._profiles:
                    marker = " ✔" if p["active"] else ""
                    click.echo(f"    • {p['name']}{marker}")
                    click.echo(f"      Active:  {p['active']}")
                    click.echo(f"      Created: {p['created']}")
                click.echo("")

        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        return super()._finalize(success=success, show_footer=show_footer)
