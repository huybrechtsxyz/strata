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

from xyz_platform.commands.session.base_session_command import BaseSessionCommand


class CleanSessionCommand(BaseSessionCommand):
    """
    Clean workspace artifacts without modifying session state.

    By default removes all files in the logs/ folder.
    Session state (session.json, repositories) is untouched.
    """

    def __init__(
        self,
        work_path: Optional[str] = None,
        logs: bool = True,
        dry_run: bool = False,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ):
        """
        Initialize the clean command.

        Args:
            work_path: Root working directory
            logs: If True (default), delete files in the logs/ folder
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
        self._clean_logs = logs
        self._dry_run = dry_run
        self._clean_stats: dict = {}

    def execute(self) -> bool:
        """
        Execute the clean command.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        try:
            if not self._initialize(operation="session_clean"):
                self.logger.error(f"Initialization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(operation="session_clean", success=False)
                return False

            if not self._before_execute():
                self.logger.error(
                    f"Pre-execution validation failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(operation="session_clean", success=False)
                return False

            success, self._clean_stats = self._session_controller.clean_session(
                work_path=self._work_path,
                logs=self._clean_logs,
                dry_run=self._dry_run,
            )

            self._errors.extend(self._session_controller.get_errors())
            self._messages.extend(self._session_controller.get_messages())

            if not success:
                self.logger.error(f"Clean failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Clean failed")
                self._finalize(operation="session_clean", success=False)
                return False

            if not self._after_execute():
                self.logger.error(
                    f"Post-execution hook failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Post-execution hook failed")
                self._finalize(operation="session_clean", success=False)
                return False

            if not self._finalize(operation="session_clean", success=True):
                self.logger.error(f"Finalization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Finalization failed")
                return False

            return True

        except Exception as e:
            error_msg = f"Failed to clean session: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(operation="session_clean", success=False)
            return False

    def _after_execute(self) -> bool:
        """Populate output data and render console feedback."""
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
                if self._clean_logs:
                    deleted = self._clean_stats.get("logs_deleted", 0)
                    folder = self._clean_stats.get("logs_folder", "")
                    action = "would delete" if self._dry_run else "deleted"
                    click.echo(f"    • Logs:   {deleted} file(s) {action}")
                    if folder:
                        click.echo(f"    • Folder: {folder}")
                click.echo("")

        return super()._after_execute()
