#!/usr/bin/env python3
"""
===============================================================================
Script Name   : add_session_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Command to add items to an XYZ Platform session.
===============================================================================
"""

from typing import Optional

import click

from xyz_platform.commands.session.base_session_command import BaseSessionCommand


class AddSessionCommand(BaseSessionCommand):
    """
    Add items to an XYZ Platform session.

    This command:
    - Adds repositories (clones/downloads), configs, or other items to the workspace
    - Updates session.json with item metadata
    - Optionally updates the VSCode workspace
    """

    def __init__(
        self,
        name: str = None,
        url: str = None,
        item_type: Optional[str] = None,
        branch: Optional[str] = None,
        config_path: Optional[str] = None,
        config_file: Optional[str] = None,
        work_path: Optional[str] = None,
        output: str = None,
        verbose: bool = None,
        quiet: bool = None,
    ):
        """
        Initialize the add command.

        Args:
            name: Optional name for the item (derived from path if omitted)
            url: URL or path to the repository (mutually exclusive with config_path/config_file)
            item_type: Item type (repo, git, local, archive)
            branch: Git branch to clone (default: main, for git repositories)
            config_path: Config directory to register as config source
            config_file: Single config file to register as config source
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
        self._config_path = config_path
        self._config_file = config_file
        self._added_repo = {}
        self._added_config_source = {}

        # Determine execution mode
        self._mode = "config" if (config_path or config_file) else "repo"

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

    def get_required_integrations(self):
        """
        Declare required integrations for this command.

        Returns:
            Dict[str, str]: Required integrations with operation descriptions
        """
        if self._mode == "config":
            return {}  # Config registration needs no external tools
        return self._session_controller.get_required_integrations_for_add_repository(
            url=self._repo_url,
            repo_type=self._repo_type,
            work_path=self._work_path,
        )

    def execute(self) -> bool:
        """
        Execute the add command - add item to session.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        try:
            # Initialize
            if not self._initialize(operation="session_add"):
                self.logger.error(f"Initialization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(operation="session_add", success=False)
                return False

            # Before
            if not self._before_execute():
                self.logger.error(
                    f"Pre-execution validation failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(operation="session_add", success=False)
                return False

            # Resolve required integrations for dependency injection
            integrations = self._resolve_required_integrations()

            # Branch: config-source registration OR repository add
            if self._mode == "config":
                success = self._execute_config_source_mode()
            else:
                # Execute via controller
                success, self._added_repo = self._session_controller.add_repository(
                    name=self._repo_name,
                    url=self._repo_url,
                    work_path=self._work_path,
                    repo_type=self._repo_type,
                    branch=self._repo_branch,
                    integrations=integrations,
                )

            # Copy controller errors/messages to command
            self._errors.extend(self._session_controller.get_errors())
            self._messages.extend(self._session_controller.get_messages())

            if not success:
                self.logger.error(f"Item add failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Item add failed")
                self._finalize(operation="session_add", success=False)
                return False

            # After
            if not self._after_execute():
                self.logger.error(
                    f"Post-execution hook failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Post-execution hook failed")
                self._finalize(operation="session_add", success=False)
                return False

            # Finalize
            if not self._finalize(operation="session_add", success=True):
                self.logger.error(f"Finalization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Finalization failed")
                return False

            return True

        except Exception as e:
            error_msg = f"Failed to add item: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(operation="session_add", success=False)
            return False

    def _initialize(self, operation: str = None) -> bool:
        """
        Initialize add command - validate parameters.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        # Call parent first
        if not super()._initialize(operation=operation):
            return False

        self.logger.debug(
            "Add command initialized",
            extra={
                "command_class": self.__class__.__name__,
                "mode": self._mode,
                "repo_name": self._repo_name,
                "repo_url": self._repo_url,
                "config_path": self._config_path,
                "config_file": self._config_file,
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

        # Validate session exists
        session_file = self._work_path / ".xyz-platform" / "session.json"
        if not session_file.exists():
            error_msg = f"Session not found. Run 'xyz session init' first. Looking for: {session_file}"
            self.logger.error(error_msg)
            self._errors.append(error_msg)
            return False

        if self._mode == "repo":
            if not self._repo_url:
                error_msg = "Either --url (for a repository) or --config-path/--config-file (for a config source) is required"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

        self.logger.debug(
            "Add pre-execution validation passed",
            extra={"command_class": self.__class__.__name__, "mode": self._mode},
        )

        return True

    def _execute_config_source_mode(self) -> bool:
        """
        Register a config file or directory as a config source in session state.

        Returns:
            bool: Success status
        """
        results = []

        if self._config_file:
            success, metadata = self._session_controller.add_config_source(
                path=self._config_file,
                source_type="file",
                name=self._repo_name,
                work_path=self._work_path,
            )
            if success:
                results.append(metadata)
            else:
                return False

        if self._config_path:
            success, metadata = self._session_controller.add_config_source(
                path=self._config_path,
                source_type="path",
                name=self._repo_name,
                work_path=self._work_path,
            )
            if success:
                results.append(metadata)
            else:
                return False

        # Merge and save configuration.yaml
        merge_success, merge_errors = self._session_controller.merge_config_and_save(
            work_path=self._work_path
        )
        if not merge_success:
            self._errors.extend(merge_errors)
            return False

        # Store results for _after_execute display
        self._added_config_source = {
            "sources": results,
            "merged_config": str(
                self._work_path / ".xyz-platform" / "configuration.yaml"
            ),
        }
        return True

    def _after_execute(self) -> bool:
        """
        Post-execution logic - display added item info.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        self.logger.debug(
            "Add post-execution",
            extra={"command_class": self.__class__.__name__},
        )

        if self._mode == "config" and not self._is_quiet() and self._added_config_source:
            self._output_data = self._added_config_source
            if self._is_console_output():
                click.echo("\n📄  Registered config source(s):")
                for src in self._added_config_source.get("sources", []):
                    click.echo(f"    • {src.get('name', '')}  [{src.get('type', '')}]  {src.get('path', '')}")
                click.echo(f"    • Merged config: {self._added_config_source.get('merged_config', '')}")

        # Display added repository
        elif self._mode == "repo" and not self._is_quiet() and self._added_repo:
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

    def _finalize(
        self, operation: str = None, success: bool = None, show_footer: bool = True
    ) -> bool:
        """
        Finalize add command.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        self.logger.debug(
            "Add command finalized",
            extra={"command_class": self.__class__.__name__},
        )

        # Call parent last
        return super()._finalize(operation=operation, success=success)
