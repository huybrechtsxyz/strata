#!/usr/bin/env python3
"""
===============================================================================
Script Name   : session_controller.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Controller for managing XYZ Platform project.
===============================================================================
"""


from pathlib import Path
from typing import Dict, List, Optional

from xyz_platform.logger.logger import get_logger
from xyz_platform.services.configuration_service import ConfigurationService


class SessionController:
    """
    Controller for managing XYZ Platform session.

    Handles loading and saving session state, as well as providing access to
    session data for commands.
    """

    @staticmethod
    def _project_folder_path(work_path: Path) -> Path:
        """Return the .xyz-platform folder path for a given work_path."""
        config_service = ConfigurationService.get_instance()
        return config_service.get_default_state_path(
            work_path=work_path, create_path=False
        )

    @staticmethod
    def _project_file_path(work_path: Path) -> Path:
        """Return the project.json file path for a given work_path."""
        config_service = ConfigurationService.get_instance()
        temp_path = config_service.get_default_state_path(
            work_path=work_path, create_path=False
        )
        return config_service.get_default_state_file(
            state_path=temp_path, create_path=False
        )

    # Initialize the controller with empty error and message lists
    def __init__(self):
        """Initialize the project controller."""
        self.logger = get_logger(self.__class__.__module__)
        self._errors: List[str] = []
        self._messages: List[str] = []
        # In-memory project state — loaded once at command start, saved once at end
        self._project_data: Optional[Dict] = None
        self._project_file: Optional[Path] = None

    # Error and message handling methods

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

    # Session in-memory load / save

    def load_project(self, work_path: Path) -> bool:
        """
        Load project.json into memory.

        Called once at the start of every command via BaseCommand._initialize().
        Silent no-op when the file does not exist yet (e.g. before ``project init``).

        Args:
            work_path: Working directory containing .xyz-platform/project.json

        Returns:
            bool: True if loaded successfully, False if file missing or unreadable
        """
        try:
            project_file = self._project_file_path(work_path)
            if not project_file.exists():
                self.logger.debug(
                    "Session file not found (starting with empty project)",
                    extra={"project_file": str(project_file)},
                )
                return False
            with open(project_file, "r", encoding="utf-8") as f:
                self._project_data = json.load(f)
            self._project_file = project_file
            self.logger.debug(
                "Session loaded into memory",
                extra={"project_file": str(project_file)},
            )
            return True
        except Exception as e:
            self.logger.debug(f"Could not load project: {e}")
            return False

    def save_project(self) -> bool:
        """
        Write in-memory project data back to project.json.

        Called once at the end of every command via BaseCommand._finalize().
        No-op when project was never loaded (e.g. command ran before ``project init``).

        Returns:
            bool: True if saved successfully, False otherwise
        """
        if self._project_data is None or self._project_file is None:
            return False
        try:
            with open(self._project_file, "w", encoding="utf-8") as f:
                json.dump(self._project_data, f, indent=2)
            self.logger.debug(
                "Session saved to disk",
                extra={"project_file": str(self._project_file)},
            )
            return True
        except Exception as e:
            self.logger.debug(f"Could not save project: {e}")
            return False

    def get_project_id(self) -> Optional[str]:
        """Return the project_id from in-memory project data, or None if not loaded."""
        if self._project_data is None:
            return None
        return self._project_data.get("project", {}).get("project_id")

    def get_project_data(self) -> Optional[Dict]:
        """Return the in-memory project data dict, or None if not loaded."""
        return self._project_data
