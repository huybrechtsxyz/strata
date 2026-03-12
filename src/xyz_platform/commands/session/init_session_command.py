#!/usr/bin/env python3
"""
===============================================================================
Script Name   : init_session_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Command to initialize a new XYZ Platform session workspace.
===============================================================================
"""

import json
import os
from pathlib import Path
from typing import Optional

import click
import yaml

from xyz_platform.commands.session.base_session_command import BaseSessionCommand


class InitSessionCommand(BaseSessionCommand):
    """
    Initialize a new XYZ Platform session workspace.

    This command prepares a workspace for XYZ Platform development by:
    - Creating the workspace folder structure
    - Generating a VSCode workspace file
    - Creating .xyz-platform configuration directory
    - Initializing session.yaml state file
    """

    def __init__(
        self,
        name: str,
        work_path: Optional[str] = None,
        output: str = None,
        verbose: bool = None,
        quiet: bool = None,
    ):
        """
        Initialize the session init command.

        Args:
            name: Name of the VSCode workspace
            work_path: Root working directory (defaults to current directory)
            output: Output format (json, yaml, text)
            verbose: Enable verbose output
            quiet: Disable all console output
        """
        super().__init__(
            work_path=work_path, output=output, verbose=verbose, quiet=quiet
        )

        self._workspace_name = name
        self._session_folder = self._work_path / ".xyz-platform"
        self._session_file = self._session_folder / "session.yaml"
        self._workspace_file = self._work_path / f"{name}.code-workspace"

    def execute(self) -> bool:
        """
        Execute the session init command - create workspace structure.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        try:
            # Initialize
            if not self._initialize(operation="session_init"):
                self.logger.error(f"Initialization failed in {self.__class__.__name__}")
                if self._allow_output:
                    click.echo("\n❌  Initialization failed")
                self._finalize(success=False)
                return False

            # Before
            if not self._before_execute():
                self.logger.error(
                    f"Pre-execution validation failed in {self.__class__.__name__}"
                )
                if self._allow_output:
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(success=False)
                return False

            # Create workspace structure
            if not self._create_workspace_structure():
                self.logger.error(
                    f"Failed to create workspace structure in {self.__class__.__name__}"
                )
                if self._allow_output:
                    click.echo("\n❌  Failed to create workspace structure")
                self._finalize(success=False)
                return False

            # After
            if not self._after_execute():
                self.logger.error(
                    f"Post-execution hook failed in {self.__class__.__name__}"
                )
                if self._allow_output:
                    click.echo("\n❌  Post-execution hook failed")
                self._finalize(success=False)
                return False

            # Finalize
            if not self._finalize(operation="session_init", success=True):
                self.logger.error(f"Finalization failed in {self.__class__.__name__}")
                if self._allow_output:
                    click.echo("\n❌  Finalization failed")
                return False

            return True

        except Exception as e:
            error_msg = f"Failed to initialize session workspace: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False

    def _initialize(self, operation: str = None) -> bool:
        """
        Initialize session init command - validate parameters.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        # Call parent first
        if not super()._initialize(operation=operation):
            return False

        self.logger.debug(
            "Session init command initialized",
            extra={
                "command_class": self.__class__.__name__,
                "workspace_name": self._workspace_name,
                "work_path": str(self._work_path),
            },
        )

        return True

    def _before_execute(self) -> bool:
        """
        Validate workspace parameters before execution.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        # Call parent first
        if not super()._before_execute():
            return False

        # Validate work path exists
        if not self._work_path.exists():
            error_msg = f"Work path does not exist: {self._work_path}"
            self.logger.error(error_msg)
            self._errors.append(error_msg)
            return False

        # Validate work path is a directory
        if not self._work_path.is_dir():
            error_msg = f"Work path is not a directory: {self._work_path}"
            self.logger.error(error_msg)
            self._errors.append(error_msg)
            return False

        # Check if workspace already exists
        if self._workspace_file.exists():
            warning_msg = f"Workspace file already exists: {self._workspace_file}"
            self.logger.warning(warning_msg)
            self._messages.append(warning_msg)

        # Check if .xyz-platform folder already exists
        if self._session_folder.exists():
            warning_msg = f".xyz-platform folder already exists: {self._session_folder}"
            self.logger.warning(warning_msg)
            self._messages.append(warning_msg)

        self.logger.debug(
            "Session init pre-execution validation passed",
            extra={"command_class": self.__class__.__name__},
        )

        return True

    def _create_workspace_structure(self) -> bool:
        """
        Create the workspace folder structure and files.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        try:
            # Create .xyz-platform folder
            self.logger.info(f"Creating .xyz-platform folder: {self._session_folder}")
            self._session_folder.mkdir(parents=True, exist_ok=True)

            if self._allow_output:
                click.echo(f"✅  Created .xyz-platform folder: {self._session_folder}")

            # Create VSCode workspace file
            self.logger.info(f"Creating VSCode workspace file: {self._workspace_file}")
            workspace_content = self._generate_workspace_file()
            with open(self._workspace_file, "w", encoding="utf-8") as f:
                json.dump(workspace_content, f, indent=2)

            if self._allow_output:
                click.echo(f"✅  Created VSCode workspace file: {self._workspace_file}")

            # Create session.yaml
            self.logger.info(f"Creating session state file: {self._session_file}")
            session_content = self._generate_session_file()
            with open(self._session_file, "w", encoding="utf-8") as f:
                yaml.dump(session_content, f, default_flow_style=False, sort_keys=False)

            if self._allow_output:
                click.echo(f"✅  Created session state file: {self._session_file}")

            self._messages.append(
                f"Session workspace '{self._workspace_name}' initialized successfully"
            )

            return True

        except Exception as e:
            error_msg = f"Failed to create workspace structure: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            return False

    def _generate_workspace_file(self) -> dict:
        """
        Generate VSCode workspace file content.

        Returns:
            dict: Workspace file content
        """
        return {
            "folders": [
                {"path": ".", "name": self._workspace_name},
            ],
            "settings": {
                "files.exclude": {
                    "**/__pycache__": True,
                    "**/*.pyc": True,
                    ".xyz-platform": False,
                }
            },
            "extensions": {
                "recommendations": [
                    "ms-python.python",
                    "ms-python.vscode-pylance",
                    "ms-azuretools.vscode-docker",
                ]
            },
        }

    def _generate_session_file(self) -> dict:
        """
        Generate session.yaml state file content.

        Returns:
            dict: Session state content
        """
        from datetime import datetime

        return {
            "session": {
                "name": self._workspace_name,
                "created": datetime.now().isoformat(),
                "work_path": str(self._work_path.absolute()),
            },
            "workspace": {
                "active": None,
                "config_path": None,
            },
            "environment": {
                "active": None,
            },
            "repositories": [],
        }
