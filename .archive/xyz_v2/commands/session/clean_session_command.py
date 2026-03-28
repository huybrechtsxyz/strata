#!/usr/bin/env python3
"""
===============================================================================
Script Name   : clean_session_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Command to clean workspace artifacts from an XYZ Platform session.
===============================================================================
"""

from typing import Optional

import click

from xyz_platform.commands.base_command import BaseCommand


class CleanSessionCommand(BaseCommand):
    """
    Clean workspace artifacts without modifying session state.

    By default removes all files in the logs/ folder.
    Session state (session.json, repositories) is untouched.
    """

    OPERATION = "session_clean"

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

    def execute(self) -> bool:
        """
        Execute the clean command.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        try:
            if not self._initialize():
                self.logger.error(f"Initialization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(success=False)
                return False

            if not self._before_execute():
                self.logger.error(
                    f"Pre-execution validation failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(success=False)
                return False

            success, self._clean_stats = self._session_controller.clean_session(
                work_path=self._work_path,
                dry_run=self._dry_run,
            )

            self._messages.extend(self._session_controller.get_messages())
            self._errors.extend(self._session_controller.get_errors())

            if not success:
                self.logger.error(f"Clean failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Clean failed")
                self._finalize(success=False)
                return False

            if not self._after_execute():
                self.logger.error(
                    f"Post-execution hook failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Post-execution hook failed")
                self._finalize(success=False)
                return False

            if not self._finalize(success=True):
                self.logger.error(f"Finalization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Finalization failed")
                return False

            return True

        except Exception as e:
            error_msg = f"Failed to clean session: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False

    def _initialize(
        self, require_session: bool = True, show_header: bool = True
    ) -> bool:
        if not super()._initialize(require_session, show_header):
            return False
        self.logger.debug(
            "CleanSessionCommand initializing",
            extra={"command_class": self.__class__.__name__},
        )
        return True

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        self.logger.debug(
            "Clean session command pre-execution validation",
            extra={"command_class": self.__class__.__name__},
        )
        return True

    def _after_execute(self) -> bool:
        """Populate output data and render console feedback."""
        self.logger.debug(
            "Clean session command post-execution validation",
            extra={"command_class": self.__class__.__name__},
        )

        if not self._is_quiet():
            self._output_data = {
                k: str(v) for k, v in self._clean_stats.items() if v is not None
            }

            if self._is_console_output():
                label = (
                    "🔍  Would clean (dry-run):"
                    if self._dry_run
                    else "🧹  Session cleaned:"
                )
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
            "Clean session command finalizing",
            extra={"command_class": self.__class__.__name__, "success": success},
        )
        return super()._finalize(success, show_footer)
