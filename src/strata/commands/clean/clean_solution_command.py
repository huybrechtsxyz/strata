"""Command to clean workspace artifacts from an Strata solution."""

from typing import Optional

import click

from strata.commands.base_command import BaseCommand


class CleanSolutionCommand(BaseCommand):
    """
    Clean workspace artifacts without modifying solution state.

    By default removes all files in the logs/ folder.
    Solution state (solution.json, repositories) is untouched.
    """

    OPERATION = "solution_clean"
    INIT_REQUIRED = True

    def __init__(
        self,
        work_path: Optional[str] = None,
        dry_run: bool = False,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ):
        """
        Initialize the clean command.

        Args:
            work_path: Root working directory
            dry_run: If True, report what would be deleted without removing anything
            output: Output format (json, text)
            verbose: Enable verbose output
            quiet: Suppress all console output
        """
        super().__init__(
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self._dry_run = dry_run
        self._clean_stats: dict = {}

    def get_required_integrations(self):
        """
        Declare required integrations for this command.

        Returns:
            Dict[str, str]: Required integrations with operation descriptions
        """
        return {}

    def _run(self) -> bool:
        if not self._run_lifecycle_phase(
            "solution_clean_before",
            context={"work_path": str(self._work_path), "dry_run": self._dry_run},
        ):
            if self._is_console_output():
                click.echo("\n❌  Pre-clean lifecycle hook failed")
            return False

        success, self._clean_stats = self._solution_controller.clean_solution(
            work_path=self._work_path,
            dry_run=self._dry_run,
        )

        self._messages.extend(self._solution_controller.get_messages())
        self._errors.extend(self._solution_controller.get_errors())

        if not success:
            if self._is_console_output():
                click.echo("\n❌  Clean failed")
            return False

        if not self._run_lifecycle_phase(
            "solution_clean_after",
            context={"work_path": str(self._work_path), "dry_run": self._dry_run},
        ):
            if self._is_console_output():
                click.echo("\n❌  Post-clean lifecycle hook failed")
            return False

        return True

    def _initialize(self, show_header: bool = True) -> bool:
        """
        Initialize the command and optionally allow running without an initialized
        solution by passing `require_solution=False`.

        This temporarily overrides the command's `INIT_REQUIRED` flag so the
        base initializer can enforce (or skip) the solution presence check.
        """
        if not super()._initialize(show_header=show_header):
            return False
        self.logger.debug(
            "InitSolutionCommand initializing",
            extra={"command_class": self.__class__.__name__},
        )
        self.logger.debug(
            "InitSolutionCommand initialized successfully",
            extra={"command_class": self.__class__.__name__},
        )
        return True

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        self.logger.debug(
            "Clean solution command pre-execution validation",
            extra={"command_class": self.__class__.__name__},
        )
        return True

    def _after_execute(self) -> bool:
        """Populate output data and render console feedback."""
        self.logger.debug(
            "Clean solution command post-execution validation",
            extra={"command_class": self.__class__.__name__},
        )

        if not self._is_quiet():
            self._output_data = {k: str(v) for k, v in self._clean_stats.items() if v is not None}

            if self._is_console_output():
                label = "🔍  Would clean (dry-run):" if self._dry_run else "🧹  Solution cleaned:"
                click.echo(f"\n{label}")
                deleted = self._clean_stats.get("logs_deleted", 0)
                folder = self._clean_stats.get("logs_folder", "")
                action = "would delete" if self._dry_run else "deleted"
                click.echo(f"    • Logs:   {deleted} file(s) {action}")
                if folder:
                    click.echo(f"    • Folder: {folder}")
                click.echo("")

        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        self.logger.debug(
            "Clean solution command finalizing",
            extra={"command_class": self.__class__.__name__, "success": success},
        )
        return super()._finalize(success, show_footer)
