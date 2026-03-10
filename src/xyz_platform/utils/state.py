#!/usr/bin/env python3
"""
===============================================================================
Script Name   : state.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Manages workspace state across CLI invocations.
===============================================================================
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Any, Dict
from xyz_platform.logger import get_logger
from xyz_platform.utils.config import DEFAULT_STATE_DIR, DEFAULT_STATE_FILE

logger = get_logger(__name__)


class WorkspaceState:
    """Manages workspace state across CLI invocations.

    State is loaded once at initialization and kept in memory.
    Call save() at program end to persist changes to disk.
    """

    def __init__(self, work_path: str):
        self.state_name = DEFAULT_STATE_DIR
        self.state_json = DEFAULT_STATE_FILE
        self._work_path = Path(work_path)
        self.state_dir = self._work_path / self.state_name
        self.state_file = self.state_dir / self.state_json
        self._ensure_state_dir()
        self._state = self._load_state()  # Load immediately on init

    def get(self, key: str, default: Any = None) -> Any:
        """Get state value from in-memory state."""
        return self._state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set state value in memory. Call save() to persist to disk.

        Args:
            key: State key
            value: State value
        """
        self._state[key] = value
        self._state["last_updated"] = datetime.now().isoformat()

    def save(self) -> None:
        """Save in-memory state to disk. Call this at program end."""
        self._save_state()

    def clear(self) -> None:
        """Clear all state data from memory and disk."""
        logger.info(
            "Clearing workspace state", extra={"work_path": str(self._work_path)}
        )
        if self.state_file.exists():
            self.state_file.unlink()
            logger.debug(
                "Deleted state file", extra={"state_file": str(self.state_file)}
            )
        self._state = {}

    def _ensure_state_dir(self):
        """Create state directory if it doesn't exist."""
        if not self.state_dir.exists():
            logger.debug(
                "Creating state directory", extra={"state_dir": str(self.state_dir)}
            )
        self.state_dir.mkdir(exist_ok=True)
        # Add to .gitignore
        gitignore = self._work_path / ".gitignore"
        if gitignore.exists():
            content = gitignore.read_text()
            if f"{self.state_name}/" not in content:
                logger.debug(
                    "Adding state directory to .gitignore",
                    extra={"state_name": self.state_name},
                )
                with gitignore.open("a") as f:
                    f.write(f"\n{self.state_name}/\n")

    def _load_state(self) -> Dict:
        """Load state from file."""
        if not self.state_file.exists():
            logger.debug(
                "State file not found, returning empty state",
                extra={"state_file": str(self.state_file)},
            )
            return {}

        try:
            state = json.loads(self.state_file.read_text())
            logger.debug(
                "Loaded state from file",
                extra={"state_file": str(self.state_file), "keys": list(state.keys())},
            )
            return state
        except json.JSONDecodeError:
            logger.error(
                "Failed to parse state file, returning empty state",
                extra={"state_file": str(self.state_file)},
                exc_info=True,
            )
            return {}

    def _save_state(self) -> None:
        """Save in-memory state to file."""
        try:
            self.state_file.write_text(json.dumps(self._state, indent=2))
            logger.debug(
                "Saved state to file",
                extra={
                    "state_file": str(self.state_file),
                    "keys": list(self._state.keys()),
                },
            )
        except Exception:
            logger.error(
                "Failed to save state to file",
                extra={"state_file": str(self.state_file)},
                exc_info=True,
            )
