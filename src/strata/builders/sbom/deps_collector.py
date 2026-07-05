"""DependencyFileCollector — scans workspace repos for application dependency files."""

import fnmatch
import json
import re
from pathlib import Path
from typing import Any, List, Optional

import yaml

from strata.builders.sbom.base_sbom_collector import BaseSbomCollector
from strata.builders.sbom.lockfile_parsers import DEFAULT_REGISTRY, LockfileParserRegistry
from strata.logger import get_logger
from strata.models.platform_artifact_model import PlatformArtifactModel
from strata.models.sbom_model import (
    SbomComponentModel,
    SbomIgnoreConfigModel,
    SbomIgnoreFileRuleModel,
    SbomIgnorePackageRuleModel,
    SbomIgnorePathRuleModel,
)
from strata.utils.config import SOLUTION_DIR, SOLUTION_FILE, SOLUTION_SBOM_IGNORE_FILE

logger = get_logger(__name__)

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
    - ``ignore_paths`` / ``ignore_files`` / ``ignore_packages`` /
      ``ignore_dependency_types`` from ``.strata/sbom-ignore.yaml`` are applied
      on top of the built-in defaults.

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
        ignore_cfg = self._load_ignore_config(work_path)

        ignore_paths_globs: List[str] = list(_DEFAULT_IGNORE_PATHS) + [r.pattern for r in ignore_cfg.ignore_paths]
        ignored_types: set[str] = {r.type for r in ignore_cfg.ignore_dependency_types}

        seen_purls: dict[str, SbomComponentModel] = {}

        for scan_root in scan_paths:
            if not scan_root.exists():
                logger.debug("Dependency scan path does not exist, skipping", path=str(scan_root))
                continue
            for pattern in patterns:
                for file_path in scan_root.rglob(pattern):
                    if not file_path.is_file():
                        continue
                    if self._is_path_ignored(file_path, scan_root, ignore_paths_globs):
                        continue
                    if self._is_filename_ignored(file_path.name, ignore_cfg.ignore_files):
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
                        if dep.dep_type and dep.dep_type in ignored_types:
                            continue
                        if self._is_package_ignored(dep.name, ignore_cfg.ignore_packages):
                            continue
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
    # Orphan-detection helper (used by validate sbom-ignore)
    # ------------------------------------------------------------------

    def scan_raw_items(
        self,
        work_path: Path,
    ) -> tuple[list[tuple[Path, Path]], list[str]]:
        """Return raw scannable items *before* any ignore rules are applied.

        Used by ``validate sbom-ignore`` to detect orphaned rules.

        Returns:
            ``(file_entries, package_names)`` where *file_entries* is a list of
            ``(file_path, scan_root)`` pairs for every lockfile found, and
            *package_names* is the deduplicated list of package names across all
            parsed files.
        """
        patterns = self._registry.all_patterns()
        if not patterns:
            return [], []

        scan_paths = self._resolve_scan_paths(work_path)
        file_entries: list[tuple[Path, Path]] = []
        package_names: list[str] = []
        seen_packages: set[str] = set()

        for scan_root in scan_paths:
            if not scan_root.exists():
                continue
            for pattern in patterns:
                for file_path in scan_root.rglob(pattern):
                    if not file_path.is_file():
                        continue
                    # Apply only the built-in default path ignores (not user rules)
                    if self._is_path_ignored(file_path, scan_root, list(_DEFAULT_IGNORE_PATHS)):
                        continue
                    file_entries.append((file_path, scan_root))
                    parser = self._registry.find(file_path.name)
                    if parser is None:
                        continue
                    try:
                        for dep in parser.parse(file_path):
                            if dep.name not in seen_packages:
                                seen_packages.add(dep.name)
                                package_names.append(dep.name)
                    except ValueError:
                        pass

        return file_entries, package_names

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
        solution_path = work_path / SOLUTION_DIR / SOLUTION_FILE
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

    @staticmethod
    def load_ignore_config(work_path: Path) -> SbomIgnoreConfigModel:
        """Load and validate ``.strata/sbom-ignore.yaml``.

        Returns an empty ``SbomIgnoreConfigModel`` when the file is absent.
        Logs a warning and returns an empty config when the file exists but
        fails Pydantic validation (so a bad ignore file never blocks a build).
        """
        ignore_path = work_path / SOLUTION_DIR / SOLUTION_SBOM_IGNORE_FILE
        if not ignore_path.exists():
            return SbomIgnoreConfigModel()
        try:
            with ignore_path.open("r", encoding="utf-8") as fh:
                raw: Any = yaml.safe_load(fh)
            if not isinstance(raw, dict):
                return SbomIgnoreConfigModel()
            return SbomIgnoreConfigModel.model_validate(raw)
        except Exception as exc:
            logger.warning("Could not load sbom-ignore.yaml — ignoring file", error=str(exc))
            return SbomIgnoreConfigModel()

    # Alias kept for internal callers (collectors should use the static method)
    def _load_ignore_config(self, work_path: Path) -> SbomIgnoreConfigModel:
        return DependencyFileCollector.load_ignore_config(work_path)

    @staticmethod
    def _is_path_ignored(
        file_path: Path,
        scan_root: Path,
        ignore_paths_globs: List[str],
    ) -> bool:
        """Return True when *file_path* should be excluded by a path glob rule."""
        try:
            rel = file_path.relative_to(scan_root)
        except ValueError:
            return False

        rel_str = rel.as_posix()
        parts = rel.parts  # e.g. ("node_modules", "pkg", "file.txt")

        for glob in ignore_paths_globs:
            segment = glob.lstrip("*/")
            if fnmatch.fnmatch(rel_str, glob):
                return True
            # Match individual path components to handle **/node_modules style
            if "/" not in segment:
                for part in parts[:-1]:  # ancestor dirs only, not the filename
                    if fnmatch.fnmatch(part, segment):
                        return True
        return False

    @staticmethod
    def _is_filename_ignored(
        filename: str,
        rules: List[SbomIgnoreFileRuleModel],
    ) -> bool:
        """Return True when *filename* matches any file ignore rule.

        Rules with ``is_regex=True`` are matched via ``re.fullmatch``; all
        others use exact string equality (case-sensitive).
        """
        for rule in rules:
            if rule.is_regex:
                try:
                    if re.fullmatch(rule.pattern, filename):
                        return True
                except re.error:
                    logger.warning("Invalid regex in sbom-ignore.yaml ignore_files", pattern=rule.pattern)
            else:
                if filename == rule.pattern:
                    return True
        return False

    @staticmethod
    def _is_package_ignored(
        name: str,
        rules: List[SbomIgnorePackageRuleModel],
    ) -> bool:
        """Return True when *name* matches any package-name glob rule."""
        for rule in rules:
            if fnmatch.fnmatch(name.lower(), rule.pattern.lower()):
                return True
        return False

    @staticmethod
    def _is_path_ignored_by_rules(
        file_path: Path,
        scan_root: Path,
        rules: List[SbomIgnorePathRuleModel],
    ) -> bool:
        """Return True when *file_path* matches any ``SbomIgnorePathRuleModel``."""
        return DependencyFileCollector._is_path_ignored(file_path, scan_root, [r.pattern for r in rules])

    @staticmethod
    def _build_purl(ecosystem: str, name: str, version: Optional[str]) -> str:
        """Build a minimal purl string.  No encoding — callers supply clean names."""
        purl = f"pkg:{ecosystem}/{name}"
        if version:
            purl += f"@{version}"
        return purl
