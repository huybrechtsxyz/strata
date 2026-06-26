"""DependencyFileCollector — scans workspace repos for application dependency files."""

import fnmatch
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from strata.builders.sbom.base_sbom_collector import BaseSbomCollector
from strata.builders.sbom.lockfile_parsers import DEFAULT_REGISTRY, LockfileParserRegistry
from strata.logger import get_logger
from strata.models.platform_artifact_model import PlatformArtifactModel
from strata.models.sbom_model import SbomComponentModel
from strata.utils.config import SOLUTION_DIR, SOLUTION_SBOM_IGNORE_FILE

logger = get_logger(__name__)

_SBOM_IGNORE_CONFIG = f"{SOLUTION_DIR}/{SOLUTION_SBOM_IGNORE_FILE}"

# Default ignore path globs — always applied before sbom-ignore.yaml additions.
_DEFAULT_IGNORE_PATHS = (
    "**/node_modules",
    "**/.venv",
    "**/venv",
    "**/dist",
    "**/build",
    "**/__pycache__",
    "**/.git",
    "**/.tox",
    "**/.mypy_cache",
    "**/.pytest_cache",
)


class DependencyFileCollector(BaseSbomCollector):
    """Collect application dependency components using the ``LockfileParserRegistry``.

    Scans workspace repository paths (from ``solution.json``) for dependency
    manifests matched by the registry's filename patterns, parses them, and
    converts each ``RawDependency`` to a ``SbomComponentModel``.

    Scope control:
    - Paths scanned are taken from the workspace ``solution.json``
      ``spec.repositories`` list (resolved relative to *work_path*).  Falls
      back to *work_path* itself when no solution or no repos are registered.
    - Optional ``ignore_paths`` / ``ignore_files`` lists from
      ``.strata/sbom-ignore.yaml`` are applied on top of built-in defaults.

    Components are de-duplicated by purl across all scanned files and paths.

    Args:
        registry: Injectable ``LockfileParserRegistry``.  Defaults to
            ``DEFAULT_REGISTRY`` (all built-in parsers).  Pass a fresh
            registry for isolated testing.
    """

    def __init__(self, registry: Optional[LockfileParserRegistry] = None) -> None:
        super().__init__()
        self._registry = registry if registry is not None else DEFAULT_REGISTRY

    def get_collector_name(self) -> str:
        return "deps"

    def collect(
        self,
        platform: PlatformArtifactModel,
        work_path: Path,
        deployment_build_path: Path,
    ) -> List[SbomComponentModel]:
        self._reset_warnings()

        patterns = self._registry.all_patterns()
        if not patterns:
            return []

        scan_paths = self._resolve_scan_paths(work_path)
        ignore_config = self._load_ignore_config(work_path)
        ignore_paths_globs: List[str] = list(_DEFAULT_IGNORE_PATHS) + (ignore_config.get("ignore_paths") or [])
        ignore_files_set: set[str] = set(ignore_config.get("ignore_files") or [])

        seen_purls: Dict[str, SbomComponentModel] = {}

        for scan_root in scan_paths:
            if not scan_root.exists():
                logger.debug("Dependency scan path does not exist, skipping", path=str(scan_root))
                continue
            for pattern in patterns:
                for file_path in scan_root.rglob(pattern):
                    if not file_path.is_file():
                        continue
                    if self._is_ignored(file_path, scan_root, ignore_paths_globs, ignore_files_set):
                        continue
                    parser = self._registry.find(file_path.name)
                    if parser is None:
                        continue
                    try:
                        raw_deps = parser.parse(file_path)
                    except ValueError as exc:
                        self._warnings.append(f"Failed to parse {file_path.name}: {exc}")
                        continue
                    for dep in raw_deps:
                        purl = self._build_purl(parser.ecosystem, dep.name, dep.version)
                        if purl not in seen_purls:
                            seen_purls[purl] = SbomComponentModel(
                                component_type="library",
                                name=dep.name,
                                version=dep.version,
                                purl=purl,
                                source_collector=self.get_collector_name(),
                            )

        return list(seen_purls.values())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_scan_paths(self, work_path: Path) -> List[Path]:
        """Return the list of directories to scan for dependency files.

        Reads ``solution.json`` from the workspace ``.strata/`` directory.
        Each ``spec.repositories[].path`` is resolved relative to *work_path*.
        Falls back to ``[work_path]`` when the solution is absent or has no
        registered repositories.
        """
        solution_path = work_path / ".strata" / "solution.json"
        if not solution_path.exists():
            return [work_path]
        try:
            with solution_path.open(encoding="utf-8") as fh:
                solution: Any = json.load(fh)
            repos: List[Any] = (solution.get("spec") or {}).get("repositories") or []
            if not repos:
                return [work_path]
            paths: List[Path] = []
            for repo in repos:
                raw_path: str = str(repo.get("path") or "")
                if not raw_path:
                    continue
                p = Path(raw_path)
                if not p.is_absolute():
                    p = work_path / p
                paths.append(p)
            return paths if paths else [work_path]
        except Exception as exc:
            logger.debug("Could not read solution.json for dependency scan scoping", error=str(exc))
            return [work_path]

    def _load_ignore_config(self, work_path: Path) -> Dict[str, Any]:
        """Load ``.strata/sbom-ignore.yaml`` — returns empty dict if absent."""
        ignore_path = work_path / _SBOM_IGNORE_CONFIG
        if not ignore_path.exists():
            return {}
        try:
            with ignore_path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.debug("Could not load sbom-ignore.yaml", error=str(exc))
            return {}

    @staticmethod
    def _is_ignored(
        file_path: Path,
        scan_root: Path,
        ignore_paths_globs: List[str],
        ignore_files_set: set[str],
    ) -> bool:
        """Return True when *file_path* should be excluded from scanning."""
        if file_path.name in ignore_files_set:
            return True
        try:
            rel = file_path.relative_to(scan_root)
        except ValueError:
            return False

        rel_str = rel.as_posix()
        parts = rel.parts  # e.g. ("node_modules", "pkg", "file.txt")

        for glob in ignore_paths_globs:
            # Strip leading **/ for directory-segment matching
            segment = glob.lstrip("*/")
            # Check the full relative path
            if fnmatch.fnmatch(rel_str, glob):
                return True
            # Check each individual path component (handles **/node_modules matching
            # a directory anywhere in the tree — any ancestor named `node_modules`
            # means the file should be ignored)
            if "/" not in segment:
                for part in parts[:-1]:  # skip filename itself — check ancestor dirs
                    if fnmatch.fnmatch(part, segment):
                        return True
        return False

    @staticmethod
    def _build_purl(ecosystem: str, name: str, version: Optional[str]) -> str:
        """Build a minimal purl string.  No encoding — callers supply clean names."""
        purl = f"pkg:{ecosystem}/{name}"
        if version:
            purl += f"@{version}"
        return purl
