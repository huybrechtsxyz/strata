"""Drift detection controller.

Orchestrates per-stage drift detection by delegating to the deployer's
``drift()`` method, classifying resource changes against built-in and
workspace-level severity rules, and persisting per-deployment history.
"""

from __future__ import annotations

import fnmatch
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import yaml

from strata.controllers.base_controller import BaseController
from strata.deployers.factory import DeployerFactory
from strata.logger import get_logger
from strata.models.drift_model import DriftEntry, DriftReport, DriftSeverity, DriftSummary
from strata.utils.config import get_drift_rules_path
from strata.utils.drift_history import DriftHistoryStore
from strata.utils.system import get_pkg_data_path

if TYPE_CHECKING:
    from strata.controllers.solution_controller import SolutionController
    from strata.models.deployment_model import DeploymentStageModel
    from strata.services.configuration_service import ConfigurationService
    from strata.services.deployment_service import DeploymentService

logger = get_logger(__name__)

# Path to the built-in drift classification rules
_BUILTIN_RULES_PATH = get_pkg_data_path() / "drift_rules.yaml"
_WORKSPACE_RULES_FILE = "drift_rules.yaml"  # optional override in .strata/


class DriftClassifier:
    """Classifies a resource change into a DriftSeverity based on YAML rules.

    Resolution order (first match wins):
    1. Attribute-level rules (check each changed attribute)
    2. Resource-type rules (exact or glob match, case-insensitive)
    3. defaults.severity
    """

    def __init__(self, rules_data: Dict[str, Any]) -> None:
        raw_rules: List[Dict[str, Any]] = rules_data.get("rules", [])
        defaults: Dict[str, Any] = rules_data.get("defaults", {})

        self._attr_rules: List[Tuple[str, DriftSeverity]] = []
        self._type_rules: List[Tuple[str, DriftSeverity]] = []
        self._default_severity = DriftSeverity(defaults.get("severity", "medium"))

        for rule in raw_rules:
            sev_str = rule.get("severity", "medium")
            try:
                severity = DriftSeverity(sev_str)
            except ValueError:
                severity = DriftSeverity.MEDIUM

            if "attribute" in rule:
                self._attr_rules.append((rule["attribute"], severity))
            elif "resource_type" in rule:
                self._type_rules.append((rule["resource_type"], severity))

    def classify(self, resource_type: str, changed_attributes: List[str]) -> DriftSeverity:
        """Return the severity for the given resource change."""
        # 1. Attribute-level check (most specific, highest priority)
        for attr_pattern, sev in self._attr_rules:
            for attr in changed_attributes:
                if fnmatch.fnmatchcase(attr.lower(), attr_pattern.lower()):
                    return sev

        # 2. Resource-type check
        for type_pattern, sev in self._type_rules:
            if fnmatch.fnmatchcase(resource_type.lower(), type_pattern.lower()):
                return sev

        # 3. Default
        return self._default_severity

    @classmethod
    def load(cls, work_path: Path) -> "DriftClassifier":
        """Load rules, merging workspace-level overrides over built-ins."""
        builtin = _load_yaml(_BUILTIN_RULES_PATH)
        workspace_rules_path = get_drift_rules_path(work_path)
        if workspace_rules_path.exists():
            workspace = _load_yaml(workspace_rules_path)
            # Workspace rules prepend (take priority over built-ins)
            merged_rules = workspace.get("rules", []) + builtin.get("rules", [])
            merged_defaults = {**builtin.get("defaults", {}), **workspace.get("defaults", {})}
            return cls({"rules": merged_rules, "defaults": merged_defaults})
        return cls(builtin)


def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        return yaml.safe_load(text) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("drift_rules_load_failed", path=str(path), error=str(exc))
        return {}


def _extract_changed_attributes(change: Dict[str, Any]) -> List[str]:
    """Return a list of top-level attribute keys that differ between before/after."""
    before: Dict[str, Any] = change.get("before") or {}
    after: Dict[str, Any] = change.get("after") or {}
    all_keys = set(before.keys()) | set(after.keys())
    changed = [k for k in all_keys if before.get(k) != after.get(k)]
    return sorted(changed)


class DriftController(BaseController):
    """Orchestrates drift detection across deployment stages."""

    def detect_drift(
        self,
        stages: List["DeploymentStageModel"],
        deployment_service: "DeploymentService",
        configuration_service: "ConfigurationService",
        build_path: Path,
        work_path: Path,
        verbose: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> DriftReport:
        """Run drift detection for each stage and return a consolidated DriftReport.

        Args:
            stages: Deployment stages to check (may be a subset filtered by --stage).
            deployment_service: Loaded deployment service.
            configuration_service: Loaded configuration service.
            build_path: Path to build artifacts directory.
            work_path: Workspace root path.
            verbose: Pass verbose flag through to deployers.
            solution_controller: Optional solution context for secret resolution.

        Returns:
            A DriftReport. Check ``.has_drift`` to decide exit code.
        """
        deployment_name = str(deployment_service.model.meta.name)  # type: ignore[union-attr]
        checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        classifier = DriftClassifier.load(work_path)
        history = DriftHistoryStore(work_path, deployment_name)
        history.load()

        all_entries: List[DriftEntry] = []
        stages_checked: List[str] = []

        for stage in stages:
            stage_entries = self._check_stage(
                stage=stage,
                deployment_service=deployment_service,
                configuration_service=configuration_service,
                build_path=build_path,
                work_path=work_path,
                verbose=verbose,
                solution_controller=solution_controller,
                classifier=classifier,
                history=history,
                checked_at=checked_at,
            )
            stages_checked.append(stage.name)
            all_entries.extend(stage_entries)

        summary = DriftSummary()
        for entry in all_entries:
            summary.increment(entry.severity)

        drifted_addresses = [e.address for e in all_entries]
        history.record_run(checked_at=checked_at, drifted_addresses=drifted_addresses)
        history.save()

        return DriftReport(
            deployment=deployment_name,
            checked_at=checked_at,
            stages_checked=stages_checked,
            entries=all_entries,
            summary=summary,
        )

    def _check_stage(
        self,
        stage: "DeploymentStageModel",
        deployment_service: "DeploymentService",
        configuration_service: "ConfigurationService",
        build_path: Path,
        work_path: Path,
        verbose: bool,
        solution_controller: Optional["SolutionController"],
        classifier: DriftClassifier,
        history: DriftHistoryStore,
        checked_at: str,
    ) -> List[DriftEntry]:
        """Run drift detection for a single stage and return classified DriftEntry list."""
        entries: List[DriftEntry] = []

        # Create deployer for this stage
        deployer_type, resolve_errors = DeployerFactory.resolve_type(stage, deployment_service)
        if deployer_type is None:
            self._errors.extend(resolve_errors)
            return entries

        deployer = DeployerFactory.create(
            deployer_type,
            stage=stage,
            deployment_service=deployment_service,
            configuration_service=configuration_service,
            build_path=build_path,
            work_path=work_path,
            verbose=verbose,
            solution_controller=solution_controller,
        )

        # Guard: deployer must support drift
        if not hasattr(deployer, "drift"):
            self._messages.append(
                f"Stage '{stage.name}': deployer '{deployer_type}' does not support drift detection — skipped."
            )
            return entries

        # Validate workspace and environment
        ok, msgs = deployer.validate_workspace()
        self._messages.extend(msgs)
        if not ok:
            self._errors.append(f"Stage '{stage.name}': workspace validation failed.")
            return entries

        ok, msgs = deployer.validate_environment()
        self._messages.extend(msgs)
        if not ok:
            self._errors.append(f"Stage '{stage.name}': environment validation failed.")
            return entries

        # Run setup (terraform init) so plan has fresh providers
        ok, msgs = deployer.setup()
        self._messages.extend(msgs)
        if not ok:
            self._errors.append(f"Stage '{stage.name}': setup failed.")
            return entries

        # Run drift detection
        ok, data, msgs = deployer.drift()
        self._messages.extend(msgs)
        if not ok:
            self._errors.append(f"Stage '{stage.name}': drift detection failed.")
            return entries

        resource_changes: List[Dict[str, Any]] = data.get("resource_changes", [])
        if not resource_changes:
            return entries  # no drift for this stage

        for rc in resource_changes:
            change = rc.get("change", {})
            actions = change.get("actions", ["update"])

            # Determine action verb (create/update/delete/replace)
            if "delete" in actions and "create" in actions:
                action = "replace"
            elif len(actions) == 1:
                action = actions[0]
            else:
                action = "+".join(actions)

            address = rc.get("address", rc.get("name", "unknown"))
            resource_type = rc.get("type", address.split(".")[0] if "." in address else "unknown")
            changed_attributes = _extract_changed_attributes(change)

            severity = classifier.classify(resource_type, changed_attributes)

            # Skip acknowledged entries — they are intentional / known drift
            if history.is_acknowledged(address):
                continue

            # Merge history metadata
            hist_entry = history.get_entry(address)
            first_detected = hist_entry["first_detected"] if hist_entry else checked_at
            consecutive_checks = (hist_entry.get("consecutive_checks", 0) + 1) if hist_entry else 1

            entries.append(
                DriftEntry(
                    address=address,
                    resource_type=resource_type,
                    action=action,
                    severity=severity,
                    stage=stage.name,
                    changed_attributes=changed_attributes,
                    before=change.get("before") or {},
                    after=change.get("after") or {},
                    first_detected=first_detected,
                    consecutive_checks=consecutive_checks,
                )
            )

        return entries
