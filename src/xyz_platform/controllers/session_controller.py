#!/usr/bin/env python3
"""
===============================================================================
Script Name   : session_controller.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Controller for managing XYZ Platform sessions.
===============================================================================
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from xyz_platform.logger.logger import get_logger


class SessionController:
    """
    Controller for managing XYZ Platform sessions.

    Handles session initialization, state management, and workspace operations.
    This is a stateless controller - it does not maintain session state between calls.
    """

    def __init__(self):
        """Initialize the session controller."""
        self.logger = get_logger(self.__class__.__module__)
        self._errors: List[str] = []
        self._messages: List[str] = []

    def has_errors(self) -> bool:
        """Check if any errors were accumulated."""
        return len(self._errors) > 0

    def get_errors(self) -> List[str]:
        """Get accumulated errors."""
        return self._errors.copy()

    def clear_errors(self) -> None:
        """Clear accumulated errors."""
        self._errors.clear()

    def get_messages(self) -> List[str]:
        """Get accumulated messages."""
        return self._messages.copy()

    def clear_messages(self) -> None:
        """Clear accumulated messages."""
        self._messages.clear()

    def initialize_session(
        self,
        workspace_name: str,
        work_path: Path,
    ) -> Tuple[bool, Dict[str, Path]]:
        """
        Initialize a new session workspace.

        Args:
            workspace_name: Name of the workspace
            work_path: Root working directory path

        Returns:
            Tuple[bool, Dict[str, Path]]: Success status and dict of created paths
                {
                    "session_folder": Path,
                    "session_file": Path,
                    "workspace_file": Path (or None if skipped)
                }
        """
        try:
            self._errors.clear()
            self._messages.clear()

            # Define paths
            session_folder = work_path / ".xyz-platform"
            session_file = session_folder / "session.json"
            workspace_file = work_path / f"{workspace_name}.code-workspace"

            created_paths = {
                "session_folder": None,
                "session_file": None,
                "workspace_file": None,
            }

            # Validate work path
            if not self._validate_work_path(work_path):
                return False, created_paths

            # Check existing files
            self._check_existing_files(workspace_file, session_folder)

            # Create session folder
            if not self._create_session_folder(session_folder):
                return False, created_paths
            created_paths["session_folder"] = session_folder

            # Create logs folder in workspace root
            logs_folder = work_path / "logs"
            self.logger.info(f"Creating logs folder: {logs_folder}")
            logs_folder.mkdir(parents=True, exist_ok=True)

            # Create workspace file (skip if already exists)
            workspace_created = self._create_workspace_file(
                workspace_file, workspace_name
            )
            if workspace_created:
                created_paths["workspace_file"] = workspace_file
            else:
                self._messages.append(
                    f"Skipped workspace file creation (already exists): {workspace_file}"
                )

            # Create logging configuration
            logging_config_path = session_folder / "logging.yaml"
            if not self._create_logging_config(logging_config_path, work_path):
                return False, created_paths
            created_paths["logging_config"] = logging_config_path

            # Create session file
            if not self._create_session_file(
                session_file, workspace_name, work_path, logging_config_path
            ):
                return False, created_paths
            created_paths["session_file"] = session_file

            self._messages.append(
                f"Session workspace '{workspace_name}' initialized successfully"
            )

            return True, created_paths

        except Exception as e:
            error_msg = f"Failed to initialize session: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            return False, {}

    def _validate_work_path(self, work_path: Path) -> bool:
        """
        Validate that work path exists and is a directory.

        Args:
            work_path: Path to validate

        Returns:
            bool: True if valid, False otherwise
        """
        if not work_path.exists():
            error_msg = f"Work path does not exist: {work_path}"
            self.logger.error(error_msg)
            self._errors.append(error_msg)
            return False

        if not work_path.is_dir():
            error_msg = f"Work path is not a directory: {work_path}"
            self.logger.error(error_msg)
            self._errors.append(error_msg)
            return False

        return True

    def _check_existing_files(self, workspace_file: Path, session_folder: Path) -> None:
        """
        Check for existing workspace files and log warnings.

        Args:
            workspace_file: Path to workspace file
            session_folder: Path to session folder
        """
        # Check for any existing .code-workspace files
        existing_workspaces = list(workspace_file.parent.glob("*.code-workspace"))
        if existing_workspaces:
            for ws_file in existing_workspaces:
                warning_msg = f"Found existing workspace file: {ws_file.name}"
                self.logger.warning(warning_msg)
                self._messages.append(warning_msg)

        # Check if .xyz-platform folder already exists
        if session_folder.exists():
            warning_msg = f".xyz-platform folder already exists: {session_folder}"
            self.logger.warning(warning_msg)
            self._messages.append(warning_msg)

    def _create_session_folder(self, session_folder: Path) -> bool:
        """
        Create .xyz-platform folder.

        Args:
            session_folder: Path to session folder

        Returns:
            bool: Success status
        """
        try:
            self.logger.info(f"Creating .xyz-platform folder: {session_folder}")
            session_folder.mkdir(parents=True, exist_ok=True)
            self._messages.append(f"Created .xyz-platform folder: {session_folder}")
            return True

        except Exception as e:
            error_msg = f"Failed to create session folder: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            return False

    def _create_workspace_file(self, workspace_file: Path, workspace_name: str) -> bool:
        """
        Create VSCode workspace file from template.
        Skips creation if file already exists.

        Args:
            workspace_file: Path to workspace file
            workspace_name: Name of the workspace

        Returns:
            bool: True if created, False if skipped or error
        """
        try:
            # Skip if already exists
            if workspace_file.exists():
                self.logger.info(
                    f"Workspace file already exists, skipping: {workspace_file}"
                )
                return False

            # Load template
            template_path = self._get_template_path("workspace.template.json")
            if not template_path.exists():
                error_msg = f"Workspace template not found: {template_path}"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

            # Read and process template
            with open(template_path, "r", encoding="utf-8") as f:
                template_content = f.read()

            # Replace placeholders
            workspace_content = template_content.replace(
                "{{workspace_name}}", workspace_name
            )

            # Parse as JSON to validate
            workspace_data = json.loads(workspace_content)

            # Write workspace file
            self.logger.info(f"Creating VSCode workspace file: {workspace_file}")
            with open(workspace_file, "w", encoding="utf-8") as f:
                json.dump(workspace_data, f, indent=2)

            self._messages.append(f"Created VSCode workspace file: {workspace_file}")
            return True

        except Exception as e:
            error_msg = f"Failed to create workspace file: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            return False

    def _create_logging_config(
        self, logging_config_path: Path, work_path: Path
    ) -> bool:
        """
        Create logging.yaml configuration file from template.
        Updates paths to be workspace-specific.

        Args:
            logging_config_path: Path to logging config file (.xyz-platform/logging.yaml)
            work_path: Root working directory

        Returns:
            bool: Success status
        """
        try:
            # Load template
            template_path = self._get_template_path("logging.yaml")
            if not template_path.exists():
                error_msg = f"Logging template not found: {template_path}"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

            # Read template as YAML
            with open(template_path, "r", encoding="utf-8") as f:
                logging_config = yaml.safe_load(f)

            # Update file handler path to be workspace-specific
            if "handlers" in logging_config and "file" in logging_config["handlers"]:
                # Update to absolute path in workspace root
                log_file_path = work_path / "logs" / "platform.json"
                logging_config["handlers"]["file"]["filename"] = str(log_file_path)

            # Write updated logging config
            self.logger.info(f"Creating logging configuration: {logging_config_path}")
            with open(logging_config_path, "w", encoding="utf-8") as f:
                yaml.dump(logging_config, f, default_flow_style=False, sort_keys=False)

            self._messages.append(
                f"Created logging configuration: {logging_config_path}"
            )
            return True

        except Exception as e:
            error_msg = f"Failed to create logging configuration: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            return False

    def _create_session_file(
        self,
        session_file: Path,
        workspace_name: str,
        work_path: Path,
        logging_config_path: Path,
    ) -> bool:
        """
        Create session.json state file from template.

        Args:
            session_file: Path to session file
            workspace_name: Name of the workspace
            work_path: Root working directory
            logging_config_path: Path to logging configuration file

        Returns:
            bool: Success status
        """
        try:
            # Load template
            template_path = self._get_template_path("session.template.json")
            if not template_path.exists():
                error_msg = f"Session template not found: {template_path}"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

            # Read and process template
            with open(template_path, "r", encoding="utf-8") as f:
                template_content = f.read()

            # Define log path
            log_path = work_path / "logs"

            # Replace placeholders (use as_posix() to avoid backslash issues in JSON on Windows)
            session_content = (
                template_content.replace("{{workspace_name}}", workspace_name)
                .replace("{{created_timestamp}}", datetime.now().isoformat())
                .replace("{{work_path}}", work_path.absolute().as_posix())
                .replace(
                    "{{logging_config_path}}", logging_config_path.absolute().as_posix()
                )
                .replace("{{log_path}}", log_path.absolute().as_posix())
            )

            # Parse as JSON to validate
            session_data = json.loads(session_content)

            # Write session file
            self.logger.info(f"Creating session state file: {session_file}")
            with open(session_file, "w", encoding="utf-8") as f:
                json.dump(session_data, f, indent=2)

            self._messages.append(f"Created session state file: {session_file}")
            return True

        except Exception as e:
            error_msg = f"Failed to create session file: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            return False

    def _get_template_path(self, template_name: str) -> Path:
        """
        Get path to template file in data directory.

        Args:
            template_name: Name of template file

        Returns:
            Path: Absolute path to template file
        """
        # Get the package data directory
        package_dir = Path(__file__).parent.parent
        template_path = package_dir / "data" / template_name
        return template_path
