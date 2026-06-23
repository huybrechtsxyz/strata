"""Tests for TenantZonePolicy — zone enforcement against Terraform plan data.

The implementation reads tenant zones from
``plan_data["variables"]["strata_tenant"]["value"]["zones"]`` and compares
them against the zone-to-region mapping on ``ConfigurationService.model.spec.zones``.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

try:
    from strata.models.policy_model import PolicyModel
    from strata.validators.policies.base_policy import PolicyContext, PolicyResult
    from strata.validators.policies.tenant_zone_policy import TenantZonePolicy

    IMPL_MISSING = False
except ImportError:
    TenantZonePolicy = None  # type: ignore[assignment,misc]
    PolicyContext = None  # type: ignore[assignment,misc]
    PolicyResult = None  # type: ignore[assignment,misc]
    PolicyModel = None  # type: ignore[assignment,misc]
    IMPL_MISSING = True

pytestmark = pytest.mark.skipif(IMPL_MISSING, reason="TenantZonePolicy not yet implemented")


# ---------------------------------------------------------------------------
# Sample plan data fixtures
# ---------------------------------------------------------------------------

#: Tenant zone context injected via Terraform plan variables
_TENANT_EU_WEST = {"variables": {"strata_tenant": {"value": {"zones": ["eu_west"]}}}}

#: Azure resource in eu_west (location field — Azure convention)
PLAN_DATA_ALLOWED = {
    **_TENANT_EU_WEST,
    "resource_changes": [
        {
            "type": "azurerm_virtual_machine",
            "name": "worker",
            "address": "azurerm_virtual_machine.worker",
            "change": {"actions": ["create"], "after": {"location": "fr-par"}},
        }
    ],
}

#: AWS resource in a disallowed zone (region = "us-east-1" not in eu_west)
PLAN_DATA_DENIED = {
    **_TENANT_EU_WEST,
    "resource_changes": [
        {
            "type": "aws_instance",
            "name": "worker",
            "address": "aws_instance.worker",
            "change": {"actions": ["create"], "after": {"region": "us-east-1"}},
        }
    ],
}

#: Resource with a read-only action — should be ignored by the policy
PLAN_DATA_READ_ONLY = {
    **_TENANT_EU_WEST,
    "resource_changes": [
        {
            "type": "data.azurerm_resource_group",
            "name": "existing",
            "address": "data.azurerm_resource_group.existing",
            "change": {"actions": ["read"], "after": {"location": "us-east-1"}},
        }
    ],
}

#: Two resources both targeting disallowed regions — two violations expected
PLAN_DATA_TWO_VIOLATIONS = {
    **_TENANT_EU_WEST,
    "resource_changes": [
        {
            "type": "aws_instance",
            "name": "worker_a",
            "address": "aws_instance.worker_a",
            "change": {"actions": ["create"], "after": {"region": "us-east-1"}},
        },
        {
            "type": "aws_instance",
            "name": "worker_b",
            "address": "aws_instance.worker_b",
            "change": {"actions": ["update"], "after": {"region": "ap-southeast-1"}},
        },
    ],
}


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------

#: Standard zone map used by most tests — eu_west covers fr-par and be-bru
EU_WEST_ZONE_MAP = {"eu_west": ["fr-par", "be-bru"]}


def _make_policy(**kwargs) -> "PolicyModel":
    """Build a minimal valid PolicyModel for TenantZonePolicy."""
    defaults = {
        "name": "zone_check",
        "type": "tenant_zone",
        "phase": "plan",
    }
    defaults.update(kwargs)
    if IMPL_MISSING:
        return MagicMock()
    return PolicyModel(**defaults)


def _make_context(plan_data=None, zone_map=None) -> "PolicyContext":
    """Build a PolicyContext with the given plan data and zone mapping.

    Args:
        plan_data:  Full Terraform plan dict, or None to test graceful skip.
                    Tenant zones are embedded in plan_data under
                    ``variables.strata_tenant.value.zones``.
                    Use the ``PLAN_DATA_*`` module constants or build a custom
                    dict.  Pass ``{}`` (empty zones list) to test the
                    no-constraint path.
        zone_map:   Dict mapping zone name → list of region codes, e.g.
                    ``{"eu_west": ["fr-par", "be-bru"]}``.  Pass ``None`` or
                    ``{}`` to simulate no zone configuration in the platform.
    """
    zone_models = []
    if zone_map:
        for zone_name, regions in zone_map.items():
            z = MagicMock()
            z.name = zone_name
            z.regions = regions
            zone_models.append(z)

    config_spec = MagicMock()
    config_spec.zones = zone_models

    config_model = MagicMock()
    config_model.spec = config_spec

    config_service = MagicMock()
    config_service.model = config_model

    if IMPL_MISSING:
        return MagicMock()
    return PolicyContext(
        phase="plan",
        work_path=Path("/tmp"),
        configuration_service=config_service,
        plan_data=plan_data,
    )


# ---------------------------------------------------------------------------
# Tests — graceful skips when context is incomplete
# ---------------------------------------------------------------------------


class TestTenantZonePolicyNoContext:
    def test_no_plan_data_passes(self):
        """Policy passes gracefully when plan_data is None — nothing to check."""
        policy = TenantZonePolicy(_make_policy())
        ctx = _make_context(plan_data=None, zone_map=EU_WEST_ZONE_MAP)

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.violations == []

    def test_no_resource_changes_passes(self):
        """Empty resource_changes list produces no violations."""
        plan = {
            **_TENANT_EU_WEST,
            "resource_changes": [],
        }
        policy = TenantZonePolicy(_make_policy())
        ctx = _make_context(plan_data=plan, zone_map=EU_WEST_ZONE_MAP)

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.violations == []

    def test_no_tenant_zones_passes(self):
        """No zone configuration in ConfigurationService → no constraint → pass."""
        # zone_map={} means the platform has no zones defined — nothing to restrict against
        policy = TenantZonePolicy(_make_policy())
        ctx = _make_context(plan_data=PLAN_DATA_DENIED, zone_map={})

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.violations == []


# ---------------------------------------------------------------------------
# Tests — zone enforcement logic
# ---------------------------------------------------------------------------


class TestTenantZonePolicyViolations:
    def test_resource_in_allowed_region_passes(self):
        """Resource whose region falls inside the tenant zone produces no violation."""
        policy = TenantZonePolicy(_make_policy())
        ctx = _make_context(plan_data=PLAN_DATA_ALLOWED, zone_map=EU_WEST_ZONE_MAP)

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.violations == []

    def test_resource_in_disallowed_region_fails(self):
        """Resource whose region is outside all tenant zones triggers a violation."""
        policy = TenantZonePolicy(_make_policy())
        ctx = _make_context(plan_data=PLAN_DATA_DENIED, zone_map=EU_WEST_ZONE_MAP)

        result = policy.evaluate(ctx)

        assert result.passed is False
        assert len(result.violations) == 1
        assert "us-east-1" in result.violations[0]
        assert "aws_instance.worker" in result.violations[0]

    def test_resource_with_location_field(self):
        """Azure resources expose the region via ``change.after.location``."""
        plan = {
            **_TENANT_EU_WEST,
            "resource_changes": [
                {
                    "type": "azurerm_virtual_machine",
                    "name": "app",
                    "change": {"actions": ["create"], "after": {"location": "fr-par"}},
                }
            ],
        }
        policy = TenantZonePolicy(_make_policy())
        ctx = _make_context(plan_data=plan, zone_map={"eu_west": ["fr-par"]})

        result = policy.evaluate(ctx)

        assert result.passed is True

    def test_resource_with_region_field(self):
        """AWS resources expose the region via ``change.after.region``."""
        plan = {
            **_TENANT_EU_WEST,
            "resource_changes": [
                {
                    "type": "aws_instance",
                    "name": "app",
                    "change": {"actions": ["create"], "after": {"region": "fr-par"}},
                }
            ],
        }
        policy = TenantZonePolicy(_make_policy())
        ctx = _make_context(plan_data=plan, zone_map={"eu_west": ["fr-par"]})

        result = policy.evaluate(ctx)

        assert result.passed is True

    def test_no_create_or_update_action_skipped(self):
        """Resources with read/no-op actions are ignored — no violation."""
        policy = TenantZonePolicy(_make_policy())
        ctx = _make_context(plan_data=PLAN_DATA_READ_ONLY, zone_map=EU_WEST_ZONE_MAP)

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.violations == []

    def test_multiple_violations_all_reported(self):
        """Every disallowed resource generates a distinct violation message."""
        policy = TenantZonePolicy(_make_policy())
        ctx = _make_context(plan_data=PLAN_DATA_TWO_VIOLATIONS, zone_map=EU_WEST_ZONE_MAP)

        result = policy.evaluate(ctx)

        assert result.passed is False
        assert len(result.violations) == 2
        combined = " ".join(result.violations)
        assert "aws_instance.worker_a" in combined
        assert "aws_instance.worker_b" in combined


# ---------------------------------------------------------------------------
# Tests — enforcement level is reflected in result
# ---------------------------------------------------------------------------


class TestTenantZonePolicyEnforcement:
    def test_deny_enforcement_result(self):
        """deny enforcement: failed result carries enforcement='deny'."""
        policy = TenantZonePolicy(_make_policy(enforcement="deny"))
        ctx = _make_context(plan_data=PLAN_DATA_DENIED, zone_map=EU_WEST_ZONE_MAP)

        result = policy.evaluate(ctx)

        assert result.passed is False
        assert result.enforcement == "deny"

    def test_warn_enforcement_result(self):
        """warn enforcement: failed result carries enforcement='warn'."""
        policy = TenantZonePolicy(_make_policy(enforcement="warn"))
        ctx = _make_context(plan_data=PLAN_DATA_DENIED, zone_map=EU_WEST_ZONE_MAP)

        result = policy.evaluate(ctx)

        assert result.passed is False
        assert result.enforcement == "warn"
