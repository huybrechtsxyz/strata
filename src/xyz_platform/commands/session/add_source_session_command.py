#!/usr/bin/env python3
"""
===============================================================================
Script Name   : add_source_session_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Command to add items to an XYZ Platform session.
===============================================================================
"""

import click

from typing import Optional

from xyz_platform.commands.base_command import BaseCommand
from xyz_platform.controllers.workspace_controller import WorkspaceController
from xyz_platform.services.configuration_service import ConfigurationService


class AddSourceSessionCommand(BaseCommand):
    """
    Add items to an XYZ Platform session.

    This command:
    - Adds repositories (clones/downloads)
    - Updates session.json with item metadata
    - Optionally updates the VSCode workspace
    """

    OPERATION = "session_source_add"

    def __init__(
        self,
        name: str,
        url: str,
        item_type: Optional[str] = None,
        branch: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ):
        """
        Initialize the add command.

        Args:
            name: name for the item
            url: URL or path to the repository
            item_type: Item type (repo, git, local, archive)
            branch: Git branch to clone (default: main, for git repositories)
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

        self._repo_name = name
        self._repo_url = url
        self._repo_type = self._normalize_item_type(item_type)
        self._repo_branch = branch or "main"
        self._added_repo = {}
        self._added_config_source = {}

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

            # Resolve required integrations for dependency injection
            integrations = self._resolve_required_integrations()

            # Add repository
            success, self._added_repo = self._session_controller.add_repository(
                name=self._repo_name,
                url=self._repo_url,
                work_path=self._work_path,
                repo_type=self._repo_type,
                branch=self._repo_branch,
                integrations=integrations,
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
        return self._session_controller.get_required_integrations_for_add_repository(
            url=self._repo_url,
            repo_type=self._repo_type,
            work_path=self._work_path,
        )

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

        if not self._repo_url:
            error_msg = "Either --url (for a repository) or --config-path/--config-file (for a config source) is required"
            self.logger.error(error_msg)
            self._errors.append(error_msg)
            return False

        self.logger.debug(
            "Add session source command initializing",
            extra={
                "command_class": self.__class__.__name__,
                "repo_name": self._repo_name,
                "repo_url": self._repo_url,
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
            "Add session source pre-execution validating",
            extra={
                "command_class": self.__class__.__name__,
                "repo_name": self._repo_name,
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
            "Add session source command post-executing",
            extra={
                "command_class": self.__class__.__name__,
                "repo_name": self._repo_name,
            },
        )

        if not self._is_quiet() and self._added_repo:
            # Always populate structured output data
            self._output_data = {
                k: v for k, v in self._added_repo.items() if v is not None
            }

            if self._is_console_output():
                click.echo("\n📦  Added item:")
                if self._added_repo.get("name"):
                    click.echo(f"    • Name:   {self._added_repo['name']}")
                if self._added_repo.get("type"):
                    click.echo(f"    • Type:   {self._added_repo['type']}")
                if self._added_repo.get("url"):
                    click.echo(f"    • URL:    {self._added_repo['url']}")
                if self._added_repo.get("path"):
                    click.echo(f"    • Path:   {self._added_repo['path']}")
                if self._added_repo.get("branch"):
                    click.echo(f"    • Branch: {self._added_repo['branch']}")

        # Call parent last
        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        """
        Finalize add command.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        self.logger.debug(
            "Add session source command finalizing",
            extra={
                "command_class": self.__class__.__name__,
                "repo_name": self._repo_name,
            },
        )

        # Call parent last
        return super()._finalize(success=success)

    def _normalize_item_type(self, item_type: Optional[str]) -> Optional[str]:
        """
        Normalize CLI item type to repository type expected by controller.

        Args:
            item_type: Raw CLI item type value

        Returns:
            Optional[str]: Normalized repository type or None for auto-detect
        """
        if not item_type:
            return None

        normalized = item_type.lower()
        if normalized == "repo":
            return None
        if normalized in ["git", "local", "archive"]:
            return normalized

        return None
