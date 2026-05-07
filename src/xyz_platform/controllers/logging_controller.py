"""Controller for workspace logging configuration (`.platform/logging.yaml`)."""

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from xyz_platform.controllers.base_controller import BaseController
from xyz_platform.utils.config import SOLUTION_DIR, SOLUTION_LOGGING_FILE
from xyz_platform.utils.system import get_pkg_data_path

# Keys that the `level` shorthand touches when the user runs `xyz log config set level X`
_LEVEL_PATHS = [
    ["handlers", "console", "level"],
    ["loggers", "xyz_platform", "level"],
]

VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class LoggingController(BaseController):
    """Manage reading and writing of `<work_path>/.platform/logging.yaml`.

    Supports:
    - ``list()``  — return the whole parsed document
    - ``get(key)``  — retrieve a value by dot-notation path
    - ``set(key, value)``  — write a value by dot-notation path
    - ``unset(key)``  — remove a key by dot-notation path
    - ``reset()``  — copy the package-default logging.yaml into the workspace
    """

    def __init__(self, work_path: Path) -> None:
        super().__init__()
        self._work_path = work_path
        self._config_path = work_path / SOLUTION_DIR / SOLUTION_LOGGING_FILE

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_state_dir(self) -> None:
        (self._work_path / SOLUTION_DIR).mkdir(parents=True, exist_ok=True)

    def _pkg_template(self) -> Optional[Path]:
        """Return the path to the package-bundled logging.yaml, or None."""
        p = get_pkg_data_path() / SOLUTION_LOGGING_FILE
        return p if p.exists() else None

    def load(self) -> Tuple[bool, Dict[str, Any]]:
        """Load the workspace logging config.  Returns (ok, dict)."""
        if not self._config_path.exists():
            return True, {}
        try:
            with open(self._config_path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            if not isinstance(data, dict):
                self._errors.append("logging.yaml must contain a YAML mapping")
                return False, {}
            return True, data
        except Exception as e:
            self._errors.append(f"Failed to read logging.yaml: {e}")
            return False, {}

    def write(self, data: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Persist the full configuration dict to disk."""
        try:
            self._ensure_state_dir()
            with open(self._config_path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(data, fh, sort_keys=False)
            return True, []
        except Exception as e:
            self._errors.append(f"Failed to write logging.yaml: {e}")
            return False, self.get_errors()

    # ------------------------------------------------------------------
    # Dot-notation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _split_path(key: str) -> List[str]:
        return key.split(".")

    @staticmethod
    def _get_nested(data: Dict, parts: List[str]) -> Tuple[bool, Any]:
        node = data
        for p in parts:
            if not isinstance(node, dict) or p not in node:
                return False, None
            node = node[p]
        return True, node

    @staticmethod
    def _set_nested(data: Dict, parts: List[str], value: Any) -> None:
        node = data
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value

    @staticmethod
    def _unset_nested(data: Dict, parts: List[str]) -> bool:
        node = data
        for p in parts[:-1]:
            if not isinstance(node, dict) or p not in node:
                return False
            node = node[p]
        if isinstance(node, dict) and parts[-1] in node:
            del node[parts[-1]]
            return True
        return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_values(self) -> Dict[str, Any]:
        """Return the full logging config as a dict (empty dict if not configured)."""
        ok, data = self.load()
        return data if ok else {}

    def get_value(self, key: str) -> Tuple[bool, Any]:
        """Return the value at ``key`` (dot-notation).  Returns (found, value)."""
        ok, data = self.load()
        if not ok:
            return False, None
        found, value = self._get_nested(data, self._split_path(key))
        return found, value

    def set_value(self, key: str, value: str) -> Tuple[bool, List[str]]:
        """Set ``key`` (dot-notation) to ``value`` in the workspace logging.yaml.

        The special key ``level`` is expanded to update both the console handler
        level and the ``xyz_platform`` logger level in one operation.
        """
        ok, data = self.load()
        if not ok:
            return False, self.get_errors()

        coerced = self._coerce(value)

        if key == "level":
            upper = str(value).upper()
            if upper not in VALID_LEVELS:
                self._errors.append(f"Invalid level '{value}'. Valid values: {', '.join(sorted(VALID_LEVELS))}")
                return False, self.get_errors()
            for path in _LEVEL_PATHS:
                self._set_nested(data, path, upper)
        else:
            self._set_nested(data, self._split_path(key), coerced)

        return self.write(data)

    def unset_value(self, key: str) -> Tuple[bool, List[str]]:
        """Remove ``key`` (dot-notation) from the workspace logging.yaml."""
        ok, data = self.load()
        if not ok:
            return False, self.get_errors()

        if key == "level":
            for path in _LEVEL_PATHS:
                self._unset_nested(data, path)
        else:
            self._unset_nested(data, self._split_path(key))

        return self.write(data)

    def reset(self) -> Tuple[bool, List[str]]:
        """Reset workspace logging.yaml to the package default."""
        tpl = self._pkg_template()
        if tpl is None:
            self._errors.append("Package logging template not found.")
            return False, self.get_errors()
        try:
            self._ensure_state_dir()
            shutil.copy(tpl, self._config_path)
            return True, []
        except Exception as e:
            self._errors.append(f"Failed to reset logging config: {e}")
            return False, self.get_errors()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce(value: str) -> Any:
        """Convert string values to appropriate Python types."""
        lower = value.lower()
        if lower == "true":
            return True
        if lower == "false":
            return False
        try:
            return int(value)
        except ValueError:
            pass
        return value
