#!/usr/bin/env python3
"""Built-in policy: required labels on platform artifact entities.

Evaluates at the ``build`` phase.  Checks that every entity of the configured
``targets`` in the built ``PlatformArtifactModel`` carries all labels declared
in the policy's ``required_labels`` list.

Configuration
-------------
``required_labels`` (required)
    List of label keys that must be present on every matched entity.

``targets`` (optional, default ``["namespaces"]``)
    Which entity collections to check. Available targets:

    ``namespaces`` – ``spec.namespaces[*]``
    ``resources``  – ``spec.resources[*]``
    ``modules``    – ``spec.modules[*]``

``filter`` (optional)
    Narrows *which* entities within the selected targets are checked, so a
    deployment can declare several narrowly-scoped policies (e.g. one for
    namespaces, one for a specific resource type) instead of a single policy
    that forces every entity to carry the same labels.

    ``name`` (list of glob patterns)
        Only entities whose name matches one of the patterns are checked
        (``fnmatch``-style, e.g. ``"prod-*"``).
    ``resource_type`` (list of strings, ``resources`` target only)
        Only resources whose ``properties.resource_type`` is in this list are
        checked (e.g. ``["vm", "postgres"]``).

Graceful degradation
--------------------
- No platform artifact in context → pass (skip)
- ``required_labels`` missing or empty in policy configuration → pass (skip)
- Unknown target name → policy fails with an explanatory violation
- Entity has no labels dict → treated as empty (all keys missing)
- ``filter`` excludes all entities in a target → nothing to check for that target

Example configuration YAML::

    policies:
      # Namespaces must carry env/owner/cost_center
      - name: namespace_labels
        type: required_labels
        phase: build
        enforcement: deny
        configuration:
          targets: [namespaces]
          required_labels: [env, owner, cost_center]

      # Only VM resources must carry an 'owner' label — other resource types
      # are untouched by this policy
      - name: vm_owner_label
        type: required_labels
        phase: build
        enforcement: warn
        configuration:
          targets: [resources]
          filter:
            resource_type: [vm]
          required_labels: [owner]

      # Only resources whose name starts with 'prod-' need a cost_center label
      - name: prod_cost_center
        type: required_labels
        phase: build
        enforcement: deny
        configuration:
          targets: [resources]
          filter:
            name: ["prod-*"]
          required_labels: [cost_center]
"""

from fnmatch import fnmatch
from typing import Any, Dict, List, Tuple

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult

_DEFAULT_TARGETS = ["namespaces"]
_ALL_TARGETS = frozenset({"namespaces", "resources", "modules"})


class RequiredLabelsPolicy(BasePolicy):
    """Deny builds where selected entities are missing required labels."""

    def __init__(self, policy_model: PolicyModel) -> None:
        super().__init__(policy_model)

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        # --- Guard: platform artifact required ---
        if context.platform_artifact is None:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no platform artifact available"},
            )

        # --- Read configuration ---
        configuration: Dict[str, Any] = self.policy.configuration or {}
        required_labels: List[str] = configuration.get("required_labels") or []
        if not required_labels:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no required_labels configured"},
            )

        targets: List[str] = configuration.get("targets") or _DEFAULT_TARGETS
        unknown = [t for t in targets if t not in _ALL_TARGETS]
        if unknown:
            valid = ", ".join(sorted(_ALL_TARGETS))
            return PolicyResult(
                passed=False,
                policy_name=self.name,
                enforcement=self.enforcement,
                violations=[f"Unknown target(s): {', '.join(unknown)}. Valid: {valid}"],
            )

        entity_filter: Dict[str, Any] = configuration.get("filter") or {}
        name_patterns: List[str] = entity_filter.get("name") or []
        resource_types: List[str] = entity_filter.get("resource_type") or []

        spec = getattr(context.platform_artifact, "spec", None)

        # --- Collect (label, name, labels) tuples for every matched entity ---
        entities: List[Tuple[str, str, Dict[str, Any]]] = []
        for target in targets:
            entities.extend(self._collect(target, spec, name_patterns, resource_types))

        # --- Check every entity ---
        violations: List[str] = []
        for label, name, labels in entities:
            for key in required_labels:
                if key not in labels:
                    violations.append(f"{label.capitalize()} '{name}' is missing required label '{key}'")

        return PolicyResult(
            passed=len(violations) == 0,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=violations,
        )

    # ------------------------------------------------------------------
    # Entity collection helpers
    # ------------------------------------------------------------------

    def _collect(
        self,
        target: str,
        spec: Any,
        name_patterns: List[str],
        resource_types: List[str],
    ) -> List[Tuple[str, str, Dict[str, Any]]]:
        """Return ``[(label, name, labels), ...]`` for entities matching ``target`` and any filters."""
        if target == "namespaces":
            items = getattr(spec, "namespaces", None) or []
            return [
                ("namespace", str(item.name), getattr(item, "labels", None) or {})
                for item in items
                if self._matches_name(item, name_patterns)
            ]

        if target == "resources":
            items = getattr(spec, "resources", None) or []
            return [
                ("resource", str(item.name), getattr(item, "labels", None) or {})
                for item in items
                if self._matches_name(item, name_patterns) and self._matches_resource_type(item, resource_types)
            ]

        if target == "modules":
            items = getattr(spec, "modules", None) or []
            return [
                ("module", str(item.name), getattr(item, "labels", None) or {})
                for item in items
                if self._matches_name(item, name_patterns)
            ]

        return []

    @staticmethod
    def _matches_name(item: Any, name_patterns: List[str]) -> bool:
        if not name_patterns:
            return True
        name = str(getattr(item, "name", ""))
        return any(fnmatch(name, pattern) for pattern in name_patterns)

    @staticmethod
    def _matches_resource_type(item: Any, resource_types: List[str]) -> bool:
        if not resource_types:
            return True
        resource_type = str(getattr(getattr(item, "properties", None), "resource_type", ""))
        return resource_type in resource_types
