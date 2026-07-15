"""Command to update package-owned files in an existing Strata solution workspace."""

from typing import Dict, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.logger import get_logger


class UpdateSolutionCommand(BaseCommand):
    """
    Update package-owned files in an existing Strata solution workspace.

    Refreshes files that ship with the strata package (schemas, templates,
    CI workflows, devcontainer config) while preserving user-customised
    files (CLI preferences, VS Code settings, workspace file, README).

    Intended to be run after upgrading the strata package to pick up new
    schemas, template changes, and CI improvements.
    """

    OPERATION = "sln_update"

    def __init__(
        self,
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

    # ------------------------------------------------------------------
    # BaseCommand interface
    # ------------------------------------------------------------------

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def _execute(self) -> bool:
        if not self._run_lifecycle_phase(
            "solution_update_before",
            context={"work_path": str(self._work_path)},
        ):
            if self._is_console_output():
                click.echo("\n❌  Pre-update lifecycle hook failed")
            return False

        if not self._run_execution():
            return False

        if not self._run_lifecycle_phase(
            "solution_update_after",
            context={"work_path": str(self._work_path)},
        ):
            if self._is_console_output():
                click.echo("\n❌  Post-update lifecycle hook failed")
            return False

        return True

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def _run_execution(self) -> bool:
        """Run the update logic via the solution controller."""
        ok, errors = self._solution_controller.update()
        self._messages.extend(self._solution_controller.get_messages())
        self._errors.extend(errors)

        if not ok:
            return False

        self._output_data = {
            "work_path": str(self._work_path),
            "updated_files": [m for m in self._messages if m.startswith("Updated:")],
            "skipped_files": [m for m in self._messages if m.startswith("Skipped:")],
        }

        return True

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def _after_execute(self) -> bool:
        if self._is_console_output():
            updated = [m for m in self._messages if m.startswith("Updated:")]
            skipped = [m for m in self._messages if m.startswith("Skipped:")]
            click.echo("\n✅  Solution workspace updated")
            click.echo(f"    • Work path      : {self._work_path}")
            click.echo(f"    • Files updated  : {len(updated)}")
            click.echo(f"    • Files skipped  : {len(skipped)}")
            click.echo("")
        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        return super()._finalize(success=success, show_footer=show_footer)


# ------------------------------------------------------------------
# Click command wiring
# ------------------------------------------------------------------


@click.command(name="update", help="Update package-owned files after a strata upgrade.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def update_command(
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Refresh package-owned workspace files (schemas, templates, CI config)."""
    command = UpdateSolutionCommand(
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
