"""Command to validate .strata/sbom-ignore.yaml and detect orphaned rules."""

import fnmatch
import re
from typing import Any, Dict, List, Optional

from strata.commands.base_command import BaseCommand
from strata.models.sbom_model import SbomIgnoreConfigModel


class SbomIgnoreValidateCommand(BaseCommand):
    """Validate ``.strata/sbom-ignore.yaml`` schema and detect orphaned rules.

    **Schema validation** — loads the file through ``SbomIgnoreConfigModel``;
    any unknown keys or type errors are reported as errors.

    **Orphan detection** — runs the dependency scanner *without* user-defined
    ignore rules (built-in defaults only) and checks each rule in the file
    against the raw scan results.  A rule is *orphaned* when it matches nothing
    in the current workspace, which typically means the file or dependency has
    been removed and the rule is stale.

    Orphaned rules are reported as warnings, not errors — the command succeeds
    (exit 0) even when orphaned rules are found.  Treat them as maintenance
    hints rather than hard failures.
    """

    OPERATION = "validate sbom-ignore"
    INIT_REQUIRED = False

    def __init__(
        self,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._config: Optional[SbomIgnoreConfigModel] = None
        self._orphaned_rules: List[Dict[str, Any]] = []

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def has_validation_errors(self) -> bool:
        return bool(self.get_errors())

    def execute(self) -> bool:
        try:
            if not self._initialize():
                self._finalize(success=False)
                return False

            # ------------------------------------------------------------------
            # Phase 1: Schema validation
            # ------------------------------------------------------------------
            from strata.controllers.solution_controller import SolutionController

            ignore_path = SolutionController.get_sbom_ignore_path(self._work_path)

            if not ignore_path.exists():
                self._messages.append(f"sbom-ignore.yaml not found at {ignore_path} — nothing to validate.")
                self._finalize(success=True)
                return True

            try:
                import yaml

                with ignore_path.open("r", encoding="utf-8") as fh:
                    raw = yaml.safe_load(fh)

                if not isinstance(raw, dict):
                    self._errors.append("sbom-ignore.yaml must be a YAML mapping at the top level.")
                    self._finalize(success=False)
                    return False

                self._config = SbomIgnoreConfigModel.model_validate(raw)

            except Exception as exc:
                self._errors.append(f"sbom-ignore.yaml validation failed: {exc}")
                self._finalize(success=False)
                return False

            self._messages.append("sbom-ignore.yaml schema is valid.")
            self._messages.append(
                f"Rules loaded: {len(self._config.ignore_paths)} path(s), "
                f"{len(self._config.ignore_files)} file(s), "
                f"{len(self._config.ignore_packages)} package(s), "
                f"{len(self._config.ignore_dependency_types)} dep-type(s)."
            )

            # ------------------------------------------------------------------
            # Phase 2: Orphan detection
            # ------------------------------------------------------------------
            self._detect_orphans()

            self._finalize(success=True)
            return True

        except Exception as exc:
            self._errors.append(f"Unexpected error during sbom-ignore validation: {exc}")
            self._finalize(success=False)
            return False

    # ------------------------------------------------------------------
    # Orphan detection
    # ------------------------------------------------------------------

    def _detect_orphans(self) -> None:
        """Scan the workspace and report rules that match nothing."""
        if self._config is None:
            return

        try:
            from strata.builders.sbom.deps_collector import DependencyFileCollector

            collector = DependencyFileCollector()
            file_entries, package_names = collector.scan_raw_items(self._work_path)
        except Exception as exc:
            self._messages.append(f"Could not run orphan detection scan: {exc}")
            return

        # --- path rules ---
        for rule in self._config.ignore_paths:
            matched = any(
                DependencyFileCollector._is_path_ignored(fp, root, [rule.pattern]) for fp, root in file_entries
            )
            if not matched:
                self._orphaned_rules.append(
                    {"type": "ignore_paths", "pattern": rule.pattern, "justification": rule.justification}
                )
                self._messages.append(f"Orphaned path rule (no files matched): '{rule.pattern}'")

        # --- file rules ---
        all_filenames = {fp.name for fp, _ in file_entries}
        for file_rule in self._config.ignore_files:
            matched = self._filename_rule_matches_any(file_rule.pattern, file_rule.is_regex, all_filenames)
            if not matched:
                self._orphaned_rules.append(
                    {
                        "type": "ignore_files",
                        "pattern": file_rule.pattern,
                        "is_regex": file_rule.is_regex,
                        "justification": file_rule.justification,
                    }
                )
                self._messages.append(f"Orphaned file rule (no files matched): '{file_rule.pattern}'")

        # --- package rules ---
        package_name_set = set(package_names)
        for pkg_rule in self._config.ignore_packages:
            matched = any(fnmatch.fnmatch(n.lower(), pkg_rule.pattern.lower()) for n in package_name_set)
            if not matched:
                self._orphaned_rules.append(
                    {"type": "ignore_packages", "pattern": pkg_rule.pattern, "justification": pkg_rule.justification}
                )
                self._messages.append(f"Orphaned package rule (no packages matched): '{pkg_rule.pattern}'")

        # dep-type rules are skipped — parsers don't populate dep_type yet,
        # so every dep-type rule would appear orphaned.

        if not self._orphaned_rules:
            self._messages.append("No orphaned rules found — all rules are in use.")
        else:
            self._messages.append(
                f"{len(self._orphaned_rules)} orphaned rule(s) found. "
                "Consider removing stale rules to keep the file accurate."
            )

    @staticmethod
    def _filename_rule_matches_any(pattern: str, is_regex: bool, filenames: set[str]) -> bool:
        for name in filenames:
            if is_regex:
                try:
                    if re.fullmatch(pattern, name):
                        return True
                except re.error:
                    return False
            else:
                if name == pattern:
                    return True
        return False

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def get_result(self) -> Dict[str, Any]:
        """Return a structured result dict for JSON output."""
        result: Dict[str, Any] = {
            "valid": not self.has_validation_errors(),
            "messages": self.get_messages(),
            "errors": self.get_errors(),
            "orphaned_rules": self._orphaned_rules,
        }
        if self._config is not None:
            result["rule_counts"] = {
                "ignore_paths": len(self._config.ignore_paths),
                "ignore_files": len(self._config.ignore_files),
                "ignore_packages": len(self._config.ignore_packages),
                "ignore_dependency_types": len(self._config.ignore_dependency_types),
            }
        return result
