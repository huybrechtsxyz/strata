"""Command to list configuration paths for a profile in an Strata solution."""

from typing import Dict, List, Optional

import click

from strata.commands.base_command import BaseCommand


class ListProfilePathCommand(BaseCommand):
    """List all configuration paths for a profile.

    Shows config, dotenv, data, and secret paths grouped by type
    for the specified profile.
    """

    OPERATION = "solution_profile_path_list"
    INIT_REQUIRED = True

    def __init__(
        self,
        profile: str,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._profile_name = profile
        self._paths: Dict[str, List[Dict]] = {}

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
            error_msg = f"Failed to list profile paths: {e}"
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
        return True

    def _run_execution(self) -> bool:
        """Read profile paths from the already-loaded solution."""
        paths_by_type, errors = self._solution_controller.get_profile_paths(self._profile_name)
        if errors:
            self._errors.extend(errors)
            return False

        self._paths = {
            path_type: [{"name": str(c.name), "path": c.path, "type": c.type, "created": c.created} for c in configs]
            for path_type, configs in paths_by_type.items()
        }
        self._output_data = {"profile": self._profile_name, "paths": self._paths}
        return True

    def _after_execute(self) -> bool:
        if not self._is_quiet() and self._is_console_output():
            total = sum(len(v) for v in self._paths.values())
            if total == 0:
                click.echo(f"\n  📋  No paths registered for profile '{self._profile_name}'.\n")
            else:
                click.echo(f"\n  📋  Paths for profile '{self._profile_name}' ({total}):\n")
                for path_type, entries in self._paths.items():
                    if entries:
                        click.echo(f"    [{path_type}]")
                        for e in entries:
                            click.echo(f"      • {e['name']}")
                            click.echo(f"        Path: {e['path']}")
                click.echo("")

        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        return super()._finalize(success=success, show_footer=show_footer)
