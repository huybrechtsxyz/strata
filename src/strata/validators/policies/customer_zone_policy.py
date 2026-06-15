#!/usr/bin/env python3
"""Built-in policy: customer zone enforcement.

Evaluates at the ``plan`` phase.  Reads the Terraform plan JSON and verifies
that every resource being created or updated resides in a region that belongs
to one of the customer's declared zones.

Graceful degradation
--------------------
- No plan data → pass (skip)
- No zone configuration in ConfigurationService → pass (no constraint)
- No customer context in plan data → pass (no constraint)
- No zone constraints on customer → pass (no constraint)
- No resource_changes in plan → pass (nothing to check)
"""

from typing import Any, Dict, List, Optional, Set

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult


class CustomerZonePolicy(BasePolicy):
    """Deny resources being provisioned in regions outside the customer's allowed zones."""

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

        # --- Build zone-name → regions lookup from ConfigurationService ---
        zone_regions: Dict[str, List[str]] = {}
        if context.configuration_service is not None:
            config_model = getattr(context.configuration_service, "model", None)
            if config_model is not None:
                spec = getattr(config_model, "spec", None)
                zones: List[Any] = getattr(spec, "zones", None) or []
                for zone in zones:
                    zone_regions[str(zone.name)] = [r.lower().replace(" ", "") for r in zone.regions]

        if not zone_regions:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no zone configuration found"},
            )

        # --- Extract customer data from plan variables ---
        variables = context.plan_data.get("variables") or {}
        customer_entry = variables.get("strata_customer")
        customer_value: Optional[Any] = None
        if isinstance(customer_entry, dict):
            customer_value = customer_entry.get("value")

        if customer_value is None:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no customer context in plan data"},
            )

        # --- Resolve customer's allowed zone names ---
        customer_zones: List[str] = []
        if isinstance(customer_value, dict):
            customer_zones = customer_value.get("zones") or []
        elif isinstance(customer_value, list):
            customer_zones = customer_value

        if not customer_zones:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "customer has no zone constraints"},
            )

        # --- Build allowed regions set ---
        allowed_regions: Set[str] = set()
        for zone_name in customer_zones:
            allowed_regions.update(zone_regions.get(str(zone_name), []))

        # --- Evaluate resource changes ---
        resource_changes: List[Any] = context.plan_data.get("resource_changes") or []
        if not resource_changes:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"allowed_regions": sorted(allowed_regions), "customer_zones": customer_zones},
            )

        violations: List[str] = []
        for change in resource_changes:
            actions: List[str] = change.get("change", {}).get("actions") or []
            if not any(a in ("create", "update") for a in actions):
                continue

            after: Dict[str, Any] = change.get("change", {}).get("after") or {}
            location: Optional[str] = after.get("location") or after.get("region")
            if location is None:
                continue

            normalized = location.lower().replace(" ", "")
            if normalized not in allowed_regions:
                resource_type = change.get("type", "unknown")
                resource_name = change.get("name", "unknown")
                violations.append(
                    f"Resource '{resource_type}.{resource_name}' is in region '{location}' "
                    f"which is not in any of the customer's allowed zones: {customer_zones}"
                )

        return PolicyResult(
            passed=len(violations) == 0,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=violations,
            details={
                "allowed_regions": sorted(allowed_regions),
                "customer_zones": customer_zones,
            },
        )
