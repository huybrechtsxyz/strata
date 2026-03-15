#!/usr/bin/env python3
"""
===============================================================================
Script Name   : remove_session_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Command to remove an item from the XYZ Platform session.
===============================================================================
"""

from typing import Optional

import click

from xyz_platform.commands.session.base_session_command import BaseSessionCommand


class RemoveSessionCommand(BaseSessionCommand):
    """
    Remove an item from the XYZ Platform session workspace.

    Removes the repository entry from session.json and optionally
    deletes the repository folder from disk.
    """

    def __init__(
        self,
        name: str,
        work_path: Optional[str] = None,
        delete_folder: bool = False,
        dry_run: bool = False,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ):
        """
        Initialize the remove command.

        Args:
            name: Name of the repository to remove
            work_path: Root working directory
            delete_folder: If True, also delete the repository folder from disk
            dry_run: If True, report what would happen without making changes
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
        self._repo_name = name
        self._delete_folder = delete_folder
        self._dry_run = dry_run
        self._removed_repo: dict = {}

    def execute(self) -> bool:
        """
        Execute the remove command.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        try:
            if not self._initialize(operation="session_remove"):
                self.logger.error(f"Initialization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(operation="session_remove", success=False)
                return False

            if not self._before_execute():
                self.logger.error(
                    f"Pre-execution validation failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(operation="session_remove", success=False)
                return False

            success, self._removed_repo = self._session_controller.remove_repository(
                name=self._repo_name,
                work_path=self._work_path,
                delete_folder=self._delete_folder,
                dry_run=self._dry_run,
            )

            self._errors.extend(self._session_controller.get_errors())
            self._messages.extend(self._session_controller.get_messages())

            if not success:
                self.logger.error(f"Remove failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Remove failed")
                self._finalize(operation="session_remove", success=False)
                return False

            if not self._after_execute():
                self.logger.error(
                    f"Post-execution hook failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Post-execution hook failed")
                self._finalize(operation="session_remove", success=False)
                return False

            if not self._finalize(operation="session_remove", success=True):
                self.logger.error(f"Finalization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Finalization failed")
                return False

            return True

        except Exception as e:
            error_msg = f"Failed to remove item: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(operation="session_remove", success=False)
            return False

    def _after_execute(self) -> bool:
        """Populate output data and render console feedback."""
        if not self._is_quiet() and self._removed_repo:
            self._output_data = {
                k: v for k, v in self._removed_repo.items() if v is not None
            }
            self._output_data["deleted_folder"] = self._delete_folder

            if self._is_console_output():
                label = (
                    "🔍  Would remove (dry-run):"
                    if self._dry_run
                    else "🗑️   Removed item:"
                )
                click.echo(f"\n{label}")
                if self._removed_repo.get("name"):
                    click.echo(f"    • Name:   {self._removed_repo['name']}")
                if self._removed_repo.get("type"):
                    click.echo(f"    • Type:   {self._removed_repo['type']}")
                if self._removed_repo.get("url"):
                    click.echo(f"    • URL:    {self._removed_repo['url']}")
                if self._delete_folder:
                    action = "would delete" if self._dry_run else "deleted"
                    click.echo(f"    • Folder: {action} from disk")
                click.echo("")

        return super()._after_execute()
