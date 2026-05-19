"""Controller for workspace environment variable sources (`.strata/cli.yaml` → env section)."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from strata.controllers.base_controller import BaseController
from strata.controllers.configuration_controller import ConfigurationController


class EnvController(BaseController):
    """Manage ordered env-file sources stored in the ``env`` section of ``cli.yaml``.

    Each source has:
    - ``name``  — unique identifier (e.g. ``base``, ``production``, ``local``)
    - ``path``  — relative or ``@repo_name/…`` path to a ``.env`` file
    - ``order`` — integer controlling load order (ascending; later overrides earlier)

    At command startup the registered sources are resolved, loaded in order,
    and injected into ``os.environ``.
    """

    def __init__(self, work_path: Path) -> None:
        super().__init__()
        self._work_path = work_path
        self._config_ctrl = ConfigurationController(work_path)

    # ------------------------------------------------------------------
    # CRUD for env sources
    # ------------------------------------------------------------------

    def list_sources(self) -> List[Dict[str, Any]]:
        """Return all registered env sources sorted by order."""
        ok, cfg = self._config_ctrl.load_config()
        if not ok:
            return []
        sources = cfg.get("env", []) or []
        return sorted(sources, key=lambda s: s.get("order", 0))

    def get_source(self, name: str) -> Optional[Dict[str, Any]]:
        """Return a single source by name, or None."""
        for src in self.list_sources():
            if src.get("name") == name:
                return src
        return None

    def add_source(
        self,
        name: str,
        path: str,
        order: int = 50,
    ) -> Tuple[bool, List[str]]:
        """Register a new env source.  Fails if ``name`` already exists."""
        ok, cfg = self._config_ctrl.load_config()
        if not ok:
            cfg = {}

        sources: List[Dict[str, Any]] = cfg.get("env", []) or []

        # Duplicate check
        if any(s.get("name") == name for s in sources):
            self._errors.append(f"Env source '{name}' already exists.")
            return False, self._errors

        sources.append({"name": name, "path": path, "order": order})
        cfg["env"] = sorted(sources, key=lambda s: s.get("order", 0))
        return self._config_ctrl.write_config(cfg)

    def remove_source(self, name: str) -> Tuple[bool, List[str]]:
        """Unregister an env source by name."""
        ok, cfg = self._config_ctrl.load_config()
        if not ok:
            return False, self._errors

        sources: List[Dict[str, Any]] = cfg.get("env", []) or []
        updated = [s for s in sources if s.get("name") != name]

        if len(updated) == len(sources):
            self._errors.append(f"Env source '{name}' not found.")
            return False, self._errors

        if updated:
            cfg["env"] = updated
        else:
            cfg.pop("env", None)
        return self._config_ctrl.write_config(cfg)

    # ------------------------------------------------------------------
    # Resolve & load
    # ------------------------------------------------------------------

    def resolve_and_load(
        self,
        repo_map: Optional[Dict[str, str]] = None,
    ) -> Tuple[Dict[str, str], List[str]]:
        """Resolve all registered env files, load them in order, return the merged dict.

        ``@repo_name/…`` references are resolved via *repo_map*.
        Missing files produce warnings but do not abort — later sources still load.

        Returns:
            ``(merged_vars, warnings)``
        """
        from strata.utils.system import resolve_path

        sources = self.list_sources()
        merged: Dict[str, str] = {}
        warnings: List[str] = []

        for src in sources:
            raw_path = src.get("path", "")
            name = src.get("name", "?")

            try:
                resolved = resolve_path(str(self._work_path), raw_path, repo_map=repo_map)
            except ValueError as e:
                warnings.append(f"Env source '{name}': {e}")
                continue

            if not resolved.exists():
                warnings.append(f"Env source '{name}': file not found at {resolved}")
                continue

            file_vars = self._parse_env_file(resolved)
            merged.update(file_vars)
            self.logger.debug(
                "Loaded env source",
                name=name,
                path=str(resolved),
                vars_count=len(file_vars),
            )

        return merged, warnings

    def inject(self, repo_map: Optional[Dict[str, str]] = None) -> List[str]:
        """Resolve sources and inject into ``os.environ``.  Returns warnings."""
        merged, warnings = self.resolve_and_load(repo_map=repo_map)
        for key, value in merged.items():
            os.environ[key] = value
        return warnings

    # ------------------------------------------------------------------
    # .env parser
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_env_file(path: Path) -> Dict[str, str]:
        """Parse a standard ``.env`` file.  Skips comments and blank lines.

        Handles:
        - ``KEY=VALUE``
        - ``KEY="VALUE"``  / ``KEY='VALUE'``  (quotes stripped)
        - ``export KEY=VALUE``
        - Lines starting with ``#`` (comments)
        """
        result: Dict[str, str] = {}
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Strip optional 'export ' prefix
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Strip surrounding quotes
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                if key:
                    result[key] = value
        return result
