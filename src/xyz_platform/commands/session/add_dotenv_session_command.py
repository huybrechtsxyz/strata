#!/usr/bin/env python3
"""
===============================================================================
Script Name   : add_dotenv_session_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Command to add items to an XYZ Platform session.
===============================================================================
"""

from pathlib import Path

import click

from typing import Optional

from xyz_platform.commands.base_command import BaseCommand


class AddDotEnvSessionCommand(BaseCommand):
    """
    Add items to an XYZ Platform session.

    This command:
    - Adds dotenv
    - Updates session.json with item metadata
    - Optionally updates the VSCode workspace
    """

    OPERATION = "session_dotenv_add"

    def __init__(
        self,
        name: str,
        env_file: str,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ):
        """
        Initialize the add command.

        Args:
            name: name for the item
            env_file: URL or path to the dotenv
            work_path: Root working directory (defaults to current directory)
            output: Output format
            verbose: Enable verbose output
            quiet: Disable all console output
        """
        super().__init__(
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )

        self._item_name = name
        self._item_path = Path(env_file)
        self._added_items = {}

    def execute(self) -> bool:
        """
        Execute the add command - add item to session.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        try:
            # Initialize
            if not self._initialize():
                self.logger.error(f"Initialization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(success=False)
                return False

            # Before
            if not self._before_execute():
                self.logger.error(
                    f"Pre-execution validation failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(success=False)
                return False

            # Add dotenv to session via controller
            repo_map = self._get_workspace_controller().get_workspace_repo_maps()
            success, self._added_dotenv = self._session_controller.add_dotenv(
                env_name=self._item_name,
                env_path=self._item_path,
                work_path=self._work_path,
                repo_map=repo_map,
            )

            # Copy controller errors/messages to command
            self._messages.extend(self._session_controller.get_messages())
            self._errors.extend(self._session_controller.get_errors())

            if not success:
                self.logger.error(f"Item add failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Item add failed")
                self._finalize(success=False)
                return False

            # After
            if not self._after_execute():
                self.logger.error(
                    f"Post-execution hook failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Post-execution hook failed")
                self._finalize(success=False)
                return False

            # Finalize
            if not self._finalize(success=True):
                self.logger.error(f"Finalization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Finalization failed")
                return False

            return True

        except Exception as e:
            error_msg = f"Failed to add item: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False

    def get_required_integrations(self):
        """
        Declare required integrations for this command.

        Returns:
            Dict[str, str]: Required integrations with operation descriptions
        """
        return {}

    def _initialize(
        self, require_session: bool = True, show_header: bool = True
    ) -> bool:
        """
        Initialize add command - validate parameters.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        # Call parent first
        if not super()._initialize(
            require_session=require_session, show_header=show_header
        ):
            return False

        if not self._item_path:
            error_msg = "--env-file for a .env session source is required"
            self.logger.error(error_msg)
            self._errors.append(error_msg)
            return False

        self.logger.debug(
            "Add session dotenv command initializing",
            extra={
                "command_class": self.__class__.__name__,
                "env_name": self._item_name,
                "env_path": self._item_path,
                "work_path": str(self._work_path),
            },
        )

        return True

    def _before_execute(self) -> bool:
        """
        Validate item parameters before execution.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        # Call parent first
        if not super()._before_execute():
            return False

        self.logger.debug(
            "Add session dotenv command pre-execution validation",
            extra={
                "command_class": self.__class__.__name__,
                "env_name": self._item_name,
            },
        )

        return True

    def _after_execute(self) -> bool:
        """
        Post-execution logic - display added item info.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        self.logger.debug(
            "Add session dotenv command post-executing",
            extra={
                "command_class": self.__class__.__name__,
                "env_name": self._item_name,
            },
        )

        if not self._is_quiet() and self._added_dotenv:
            # Always populate structured output data
            self._output_data = {
                k: v for k, v in self._added_dotenv.items() if v is not None
            }

            if self._is_console_output():
                click.echo("\n📦  Added item:")
                if self._added_dotenv.get("name"):
                    click.echo(f"    • Name:   {self._added_dotenv['name']}")
                if self._added_dotenv.get("path"):
                    click.echo(f"    • Path:   {self._added_dotenv['path']}")
                click.echo("")

        # Call parent last
        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        """
        Finalize add command.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        self.logger.debug(
            "Add session dotenv command finalizing",
            extra={
                "command_class": self.__class__.__name__,
                "env_name": self._item_name,
            },
        )

        # Call parent last
        return super()._finalize(success=success)
