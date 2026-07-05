#!/usr/bin/env python3
"""Built-in policy: resource type allow/deny list.

Evaluates at the ``plan`` phase.  Reads the Terraform plan JSON and checks
every ``create`` or ``update`` resource change against a configured list of
allowed or denied resource types.

Two operating modes
-------------------
``deny`` (default)
    The policy fails if *any* resource being created or updated has a type
    that appears in the ``types`` list.  Use this to block specific resource
    types outright (e.g. prevent bare VMs when only managed services are
    allowed).

``allow``
    The policy fails if *any* resource being created or updated has a type
    that does **not** appear in the ``types`` list.  Use this to create a
    strict allowlist — only the declared types are permitted.

Configuration
-------------
``mode`` (optional, default ``deny``)
    Operating mode: ``deny`` or ``allow``.

``types`` (required)
    List of Terraform resource type strings to match against
    (e.g. ``azurerm_virtual_machine``, ``aws_instance``).

``actions`` (optional, default ``[create, update]``)
    Which plan actions trigger the check.  Supported values: ``create``,
    ``update``, ``delete``, ``replace``.

Examples
--------
::

    policies:
      # Block bare VMs — use managed node pools instead
      - name: no_bare_vms
        type: resource_type_restrictions
        phase: plan
        enforcement: deny
        configuration:
          mode: deny
          types:
            - azurerm_virtual_machine
            - aws_instance
            - google_compute_instance

      # Strict allowlist — only these storage types permitted
      - name: approved_storage_only
        type: resource_type_restrictions
        phase: plan
        enforcement: deny
        configuration:
          mode: allow
          types:
            - azurerm_storage_account
            - azurerm_storage_container

Graceful degradation
--------------------
- No plan data in context → pass (skip)
- ``types`` missing or empty in policy configuration → pass (skip)
- No resource_changes in plan → pass (nothing to check)
"""

from typing import Any, Dict, List

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult

_DEFAULT_ACTIONS = ("create", "update")


class ResourceTypeRestrictionsPolicy(BasePolicy):
    """Enforce an allow or deny list of Terraform resource types."""

    def __init__(self, policy_model: PolicyModel) -> None:
        super().__init__(policy_model)

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        # --- Guard: plan data required ---
        if context.plan_data is None:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no plan data available"},
            )

        # --- Read configuration ---
        configuration: Dict[str, Any] = self.policy.configuration or {}
        types: List[str] = configuration.get("types") or []
        if not types:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no types configured"},
            )

        mode: str = configuration.get("mode") or "deny"
        watch_actions: List[str] = configuration.get("actions") or list(_DEFAULT_ACTIONS)

        type_set = set(types)

        # --- Evaluate resource changes ---
        resource_changes: List[Any] = context.plan_data.get("resource_changes") or []
        if not resource_changes:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"mode": mode, "types": types},
            )

        violations: List[str] = []
        for change in resource_changes:
            actions: List[str] = change.get("change", {}).get("actions") or []
            if not any(a in watch_actions for a in actions):
                continue

            resource_type: str = change.get("type", "unknown")
            resource_name: str = change.get("name", "unknown")
            address: str = change.get("address") or f"{resource_type}.{resource_name}"

            if mode == "deny":
                if resource_type in type_set:
                    violations.append(f"Resource '{address}' has type '{resource_type}' which is on the denied list")
            elif mode == "allow":
                if resource_type not in type_set:
                    violations.append(
                        f"Resource '{address}' has type '{resource_type}' which is not on the allowed list"
                    )

        return PolicyResult(
            passed=len(violations) == 0,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=violations,
            details={"mode": mode, "types": types},
        )
