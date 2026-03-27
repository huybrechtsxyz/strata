#!/usr/bin/env python3
"""
===============================================================================
Script Name   : remove_source_session_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Command to remove an item from the XYZ Platform session.
===============================================================================
"""

from typing import Optional

import click

from xyz_platform.commands.base_command import BaseCommand


class RemoveSourceSessionCommand(BaseCommand):
    """
    Remove an item from the XYZ Platform session workspace.

    Operates in two modes:
    - repo mode (default): removes a repository entry from session.json,
      optionally deletes the repository folder from disk.
    - config mode (--config): removes a config source entry from session.json
      and re-merges the active configuration.
    """

    OPERATION = "session_source_remove"

    def __init__(
        self,
        name: str,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ):
        """
        Initialize the remove command.

        Args:
            name: Name of the repository or config source to remove
            work_path: Root working directory
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
        self._item_name = name
        self._removed_item: dict = {}

        # Keep backward-compatible alias
        self._repo_name = name

    def execute(self) -> bool:
        """
        Execute the remove command.

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

            success, self._removed_item = self._session_controller.remove_repository(
                name=self._item_name, work_path=self._work_path, delete_folder=True
            )

            self._messages.extend(self._session_controller.get_messages())
            self._errors.extend(self._session_controller.get_errors())

            if not success:
                self.logger.error(f"Remove failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Remove failed")
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
            error_msg = f"Failed to remove item: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False

    def _initialize(
        self, require_session: bool = False, show_header: bool = True
    ) -> bool:
        if not super()._initialize(require_session, show_header):
            return False
        self.logger.debug(
            f"Initialized {self.__class__.__name__} with item_name={self._item_name}"
        )
        return True

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        self.logger.debug(
            f"Pre-execution validation passed in {self.__class__.__name__}"
        )
        return True

    def _after_execute(self) -> bool:
        """Populate output data and render console feedback."""
        if not self._is_quiet() and self._removed_item:
            self._output_data = {
                k: v for k, v in self._removed_item.items() if v is not None
            }

            if self._is_console_output():
                label = "🗑️   Removed item:"
                click.echo(f"\n{label}")
                if self._removed_item.get("name"):
                    click.echo(f"    • Name:   {self._removed_item['name']}")
                if self._removed_item.get("type"):
                    click.echo(f"    • Type:   {self._removed_item['type']}")
                if self._removed_item.get("url"):
                    click.echo(f"    • URL:    {self._removed_item['url']}")
                click.echo("    • Folder: deleted from disk")
            click.echo("")

        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer=True) -> bool:
        self.logger.debug(f"Finalized {self.__class__.__name__} with success={success}")
        return super()._finalize(success=success, show_footer=show_footer)
