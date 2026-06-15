#!/usr/bin/env python3
"""Built-in policy: name pattern enforcement.

Evaluates at the ``validate`` phase.  Checks that configured names match a
user-supplied regular expression pattern.

Configuration
-------------
``pattern`` (required)
    A full-match regular expression applied to every collected name.

``targets`` (optional, default ``["config_name"]``)
    List of name sets to validate.  Available targets:

    ``config_name``       – ``configuration.meta.name``
    ``deployment_name``   – ``deployment.meta.name``
    ``stage_names``       – ``deployment.spec.stages[*].name``
    ``workspace_name``    – ``workspace.meta.name``
    ``topology_names``    – ``workspace.spec.topologies[*].name``
    ``resource_names``    – ``workspace.spec.resources[*].name``
    ``namespace_names``   – ``workspace.spec.namespaces[*].name``
    ``provisioner_names`` – ``workspace.spec.provisioners[*].name``
    ``module_names``      – ``workspace.spec.resources[*].modules[*].name``
    ``volume_names``      – ``workspace.spec.topologies[*].volumes[*].name``

    Targets whose required service is absent from the context are silently
    skipped (not counted as failures).

Graceful degradation
--------------------
- Required service not in context → target skipped, noted in ``details``
- ``pattern`` missing from policy configuration → pass (skip entire policy)
- Unknown target name → policy fails with an explanatory violation
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult

_DEFAULT_TARGETS = ["config_name"]

_ALL_TARGETS = frozenset(
    {
        "config_name",
        "deployment_name",
        "stage_names",
        "workspace_name",
        "topology_names",
        "resource_names",
        "namespace_names",
        "provisioner_names",
        "module_names",
        "volume_names",
    }
)


class NamingPolicy(BasePolicy):
    """Deny configurations whose names do not match the required pattern."""

    def __init__(self, policy_model: PolicyModel) -> None:
        super().__init__(policy_model)

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        configuration: Dict[str, Any] = self.policy.configuration or {}
        pattern: str = configuration.get("pattern", "")
        if not pattern:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no pattern configured"},
            )

        targets: List[str] = configuration.get("targets", _DEFAULT_TARGETS)

        unknown = [t for t in targets if t not in _ALL_TARGETS]
        if unknown:
            valid = ", ".join(sorted(_ALL_TARGETS))
            return PolicyResult(
                passed=False,
                policy_name=self.name,
                enforcement=self.enforcement,
                violations=[f"Unknown target(s): {', '.join(unknown)}. Valid: {valid}"],
            )

        # Collect (label, name) pairs and skip-reasons
        candidates: List[Tuple[str, str]] = []
        skipped: List[str] = []

        for target in targets:
            extracted, skip_reason = self._extract_names(target, context)
            if skip_reason:
                skipped.append(f"{target}: {skip_reason}")
            else:
                candidates.extend(extracted)

        # Validate each name against the pattern
        violations: List[str] = []
        for label, name in candidates:
            if not re.fullmatch(pattern, name):
                violations.append(f"{label} '{name}' does not match required pattern '{pattern}'")

        details: Optional[Dict[str, Any]] = {"skipped_targets": skipped} if skipped else None

        return PolicyResult(
            passed=len(violations) == 0,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=violations,
            details=details,
        )

    # ------------------------------------------------------------------
    # Name extraction helpers
    # ------------------------------------------------------------------

    def _extract_names(
        self,
        target: str,
        context: PolicyContext,
    ) -> Tuple[List[Tuple[str, str]], str]:
        """Return ``([(label, name), ...], skip_reason)``.

        ``skip_reason`` is non-empty when the required service is unavailable;
        in that case the name list is empty and the target is silently skipped.
        """
        cfg_svc = context.configuration_service
        dep_svc = context.deployment_service

        # --- configuration-only targets ---
        if target == "config_name":
            if cfg_svc is None or cfg_svc.model is None:
                return [], "no configuration service"
            return [("config", str(cfg_svc.model.meta.name))], ""

        # --- deployment-level targets ---
        if dep_svc is None or dep_svc.model is None:
            return [], "no deployment service"

        if target == "deployment_name":
            return [("deployment", str(dep_svc.model.meta.name))], ""

        if target == "stage_names":
            stages = dep_svc.model.spec.stages or []
            return [("stage", str(s.name)) for s in stages], ""

        # --- workspace-level targets ---
        ws_svc = dep_svc.get_workspace_service()
        if ws_svc is None or ws_svc.model is None:
            return [], "no workspace service"

        ws_spec = ws_svc.model.spec

        if target == "workspace_name":
            return [("workspace", str(ws_svc.model.meta.name))], ""

        if target == "topology_names":
            return [("topology", str(t.name)) for t in (ws_spec.topologies or [])], ""

        if target == "resource_names":
            return [("resource", str(r.name)) for r in (ws_spec.resources or [])], ""

        if target == "namespace_names":
            return [("namespace", str(n.name)) for n in (ws_spec.namespaces or [])], ""

        if target == "provisioner_names":
            return [("provisioner", str(p.name)) for p in (ws_spec.provisioners or [])], ""

        if target == "module_names":
            names = [("module", str(m.name)) for r in (ws_spec.resources or []) for m in (r.modules or [])]
            return names, ""

        if target == "volume_names":
            names = [("volume", str(v.name)) for t in (ws_spec.topologies or []) for v in (t.volumes or [])]
            return names, ""

        return [], f"unhandled target '{target}'"
