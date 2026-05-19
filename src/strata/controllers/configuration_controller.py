"""Controller for workspace configuration (persistent CLI defaults).

This controller reads/writes the workspace CLI preferences file
(`<work_path>/.strata/cli.yaml`) using the package template when available.
"""

import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from strata.controllers.base_controller import BaseController
from strata.utils.config import SOLUTION_CONFIG_FILE, SOLUTION_DIR
from strata.utils.system import get_pkg_templates_path


class ConfigurationController(BaseController):
    """Manage reading and writing of `<work_path>/.strata/cli.yaml`.

    Responsibilities:
    - Load existing YAML config (returns dict)
    - Initialise from template when missing
    - Set/unset keys in the top-level `values:` mapping
    """

    def __init__(self, work_path: Path) -> None:
        super().__init__()
        self._work_path = work_path
        self._config_path = self._work_path / SOLUTION_DIR / SOLUTION_CONFIG_FILE

    def _ensure_state_dir(self) -> None:
        (self._work_path / SOLUTION_DIR).mkdir(parents=True, exist_ok=True)

    def _ensure_from_template(self) -> None:
        """If the config file does not exist, try to copy the package template."""
        if self._config_path.exists():
            return
        tpl = get_pkg_templates_path() / "solution" / SOLUTION_CONFIG_FILE
        if tpl.exists():
            try:
                shutil.copy(tpl, self._config_path)
            except Exception:
                # Non-fatal; we'll create an empty file later if needed
                pass

    def load_config(self) -> Tuple[bool, Dict[str, Any]]:
        """Load the whole configuration file. Returns (ok, dict)."""
        if not self._config_path.exists():
            return True, {}
        try:
            with open(self._config_path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            if not isinstance(data, dict):
                self._errors.append("Configuration file must contain a mapping/dictionary")
                return False, {}
            return True, data
        except Exception as e:
            self._errors.append(f"Failed to read configuration file: {e}")
            return False, {}

    def write_config(self, data: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Write the full configuration dict to disk (creates state dir if needed)."""
        try:
            self._ensure_state_dir()
            # If file missing, try to seed from template first (avoid erasing helpful comments)
            if not self._config_path.exists():
                self._ensure_from_template()
            with open(self._config_path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(data, fh, sort_keys=False)
            return True, []
        except Exception as e:
            self._errors.append(f"Failed to write configuration file: {e}")
            return False, self._errors

    # high-level helpers -----------------------------------------------

    def list_cli_values(self) -> Dict[str, Any]:
        ok, cfg = self.load_config()
        if not ok:
            return {}
        return cfg.get("values", {}) or {}

    def set_cli_value(self, key: str, value: Any) -> Tuple[bool, List[str]]:
        ok, cfg = self.load_config()
        if not ok:
            cfg = {}
        values = cfg.get("values", {}) or {}
        values[key] = value
        cfg["values"] = values
        return self.write_config(cfg)

    def unset_cli_value(self, key: str) -> Tuple[bool, List[str]]:
        ok, cfg = self.load_config()
        if not ok:
            return False, self._errors
        values = cfg.get("values", {}) or {}
        if key in values:
            values.pop(key)
            if values:
                cfg["values"] = values
            else:
                cfg.pop("values", None)
            return self.write_config(cfg)
        # Key not present: treat as success
        return True, []
