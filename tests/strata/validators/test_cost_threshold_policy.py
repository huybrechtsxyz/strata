#!/usr/bin/env python3
"""Unit tests for CostThresholdPolicy."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import PolicyContext
from strata.validators.policies.cost_threshold_policy import CostThresholdPolicy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _policy(
    max_monthly=None,
    currency="EUR",
    env_pattern=None,
    enforcement="deny",
) -> CostThresholdPolicy:
    config = {}
    if max_monthly is not None:
        config["max_monthly"] = max_monthly
    if currency:
        config["currency"] = currency
    if env_pattern:
        config["environment_pattern"] = env_pattern
    model = PolicyModel(
        name="test_cost_policy",
        type="cost_threshold",
        phase="plan",
        enforcement=enforcement,
        configuration=config or None,
    )
    return CostThresholdPolicy(model)


def _context(
    cost_data=None,
    env_name=None,
    build_path=None,
) -> PolicyContext:
    ds = None
    if env_name is not None:
        env_service = MagicMock()
        env_service.get_name.return_value = env_name
        ds = MagicMock()
        ds.get_environment_service.return_value = env_service

    return PolicyContext(
        phase="plan",
        work_path=Path("/tmp"),
        deployment_service=ds,
        build_path=build_path,
        cost_data=cost_data,
    )


_COST_DATA_5000 = {
    "provisioners": {
        "terraform": {
            "breakdown": {
                "totalMonthlyCost": "5000.00",
                "resources": [],
            }
        }
    }
}

_COST_DATA_500 = {
    "provisioners": {
        "terraform": {
            "breakdown": {
                "totalMonthlyCost": "500.00",
                "resources": [],
            }
        }
    }
}

_COST_DATA_MULTI = {
    "provisioners": {
        "infra": {"breakdown": {"totalMonthlyCost": "3000.00"}},
        "platform": {"breakdown": {"totalMonthlyCost": "2000.00"}},
    }
}


# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------


class TestCostThresholdSkip:
    def test_skips_when_no_max_monthly_configured(self):
        policy = _policy(max_monthly=None)
        result = policy.evaluate(_context(cost_data=_COST_DATA_5000))
        assert result.passed is True
        assert "skipped" in (result.details or {})

    def test_skips_when_no_cost_data(self):
        policy = _policy(max_monthly=10000)
        result = policy.evaluate(_context(cost_data=None))
        assert result.passed is True
        assert "cost.json" in (result.details or {}).get("skipped", "")

    def test_skips_when_cost_is_zero(self):
        cost_data = {"provisioners": {"terraform": {"breakdown": {"totalMonthlyCost": "0.00"}}}}
        policy = _policy(max_monthly=10000)
        result = policy.evaluate(_context(cost_data=cost_data))
        assert result.passed is True
        assert "zero" in (result.details or {}).get("skipped", "")

    def test_skips_when_cost_data_has_no_parseable_costs(self):
        policy = _policy(max_monthly=10000)
        result = policy.evaluate(_context(cost_data={"provisioners": {}}))
        assert result.passed is True

    def test_skips_when_max_monthly_not_a_number(self):
        policy = _policy(max_monthly="not_a_number")
        result = policy.evaluate(_context(cost_data=_COST_DATA_5000))
        assert result.passed is True


# ---------------------------------------------------------------------------
# Threshold evaluation
# ---------------------------------------------------------------------------


class TestCostThresholdEvaluation:
    def test_passes_when_cost_below_threshold(self):
        policy = _policy(max_monthly=10000)
        result = policy.evaluate(_context(cost_data=_COST_DATA_5000))
        assert result.passed is True
        assert result.violations == []

    def test_passes_when_cost_equals_threshold(self):
        policy = _policy(max_monthly=5000)
        result = policy.evaluate(_context(cost_data=_COST_DATA_5000))
        assert result.passed is True

    def test_fails_when_cost_exceeds_threshold(self):
        policy = _policy(max_monthly=4000)
        result = policy.evaluate(_context(cost_data=_COST_DATA_5000))
        assert result.passed is False
        assert len(result.violations) == 1
        assert "5000" in result.violations[0]
        assert "4000" in result.violations[0]

    def test_violation_includes_currency(self):
        policy = _policy(max_monthly=4000, currency="EUR")
        result = policy.evaluate(_context(cost_data=_COST_DATA_5000))
        assert result.passed is False
        assert "EUR" in result.violations[0]

    def test_enforcement_is_warn(self):
        policy = _policy(max_monthly=4000, enforcement="warn")
        result = policy.evaluate(_context(cost_data=_COST_DATA_5000))
        assert result.passed is False
        assert result.enforcement == "warn"

    def test_details_contain_costs(self):
        policy = _policy(max_monthly=10000)
        result = policy.evaluate(_context(cost_data=_COST_DATA_5000))
        assert result.details is not None
        assert result.details["total_monthly"] == 5000.0
        assert result.details["max_monthly"] == 10000.0


# ---------------------------------------------------------------------------
# Multi-provisioner
# ---------------------------------------------------------------------------


class TestCostThresholdMultiProvisioner:
    def test_sums_costs_across_provisioners(self):
        # _COST_DATA_MULTI = infra:3000 + platform:2000 = 5000
        policy = _policy(max_monthly=4000)
        result = policy.evaluate(_context(cost_data=_COST_DATA_MULTI))
        assert result.passed is False
        assert result.details["total_monthly"] == 5000.0

    def test_passes_when_multi_provisioner_total_within_threshold(self):
        policy = _policy(max_monthly=6000)
        result = policy.evaluate(_context(cost_data=_COST_DATA_MULTI))
        assert result.passed is True


# ---------------------------------------------------------------------------
# Environment pattern
# ---------------------------------------------------------------------------


class TestCostThresholdEnvPattern:
    def test_skips_when_env_does_not_match_pattern(self):
        policy = _policy(max_monthly=500, env_pattern="dev*")
        result = policy.evaluate(_context(cost_data=_COST_DATA_5000, env_name="prd"))
        assert result.passed is True
        assert "does not match" in (result.details or {}).get("skipped", "")

    def test_evaluates_when_env_matches_pattern(self):
        policy = _policy(max_monthly=400, env_pattern="dev*")
        result = policy.evaluate(_context(cost_data=_COST_DATA_500, env_name="dev"))
        assert result.passed is False

    def test_evaluates_when_env_matches_exact(self):
        policy = _policy(max_monthly=400, env_pattern="staging")
        result = policy.evaluate(_context(cost_data=_COST_DATA_500, env_name="staging"))
        assert result.passed is False

    def test_evaluates_when_env_pattern_set_but_no_env_available(self):
        """When env_pattern is set but environment cannot be resolved, evaluate normally."""
        policy = _policy(max_monthly=400, env_pattern="dev*")
        ctx = PolicyContext(
            phase="plan",
            work_path=Path("/tmp"),
            cost_data=_COST_DATA_500,
        )
        # No deployment_service → env_name is None → pattern check skipped → evaluate
        result = policy.evaluate(ctx)
        assert result.passed is False  # 500 > 400


# ---------------------------------------------------------------------------
# Cost data format variants
# ---------------------------------------------------------------------------


class TestCostDataFormats:
    def test_top_level_total_monthly_cost(self):
        """Handles flat cost.json with top-level totalMonthlyCost."""
        cost_data = {"totalMonthlyCost": "8000.00"}
        policy = _policy(max_monthly=5000)
        result = policy.evaluate(_context(cost_data=cost_data))
        assert result.passed is False
        assert result.details["total_monthly"] == 8000.0

    def test_string_cost_value_parsed(self):
        """totalMonthlyCost as string (Infracost default) is parsed correctly."""
        cost_data = {"provisioners": {"terraform": {"breakdown": {"totalMonthlyCost": "1234.56"}}}}
        policy = _policy(max_monthly=2000)
        result = policy.evaluate(_context(cost_data=cost_data))
        assert result.passed is True
        assert result.details["total_monthly"] == pytest.approx(1234.56)

    def test_projects_format(self):
        """Handles Infracost multi-project format."""
        cost_data = {
            "provisioners": {
                "terraform": {
                    "projects": [
                        {"breakdown": {"totalMonthlyCost": "1000.00"}},
                        {"breakdown": {"totalMonthlyCost": "2000.00"}},
                    ]
                }
            }
        }
        policy = _policy(max_monthly=2500)
        result = policy.evaluate(_context(cost_data=cost_data))
        assert result.passed is False
        assert result.details["total_monthly"] == pytest.approx(3000.0)


# ---------------------------------------------------------------------------
# Policy engine registration
# ---------------------------------------------------------------------------


class TestCostThresholdRegistration:
    def test_registered_in_policy_engine(self):
        """cost_threshold resolves without ValueError from policy engine."""
        from strata.validators.policies.policy_engine import PolicyEngine

        model = PolicyModel(
            name="test",
            type="cost_threshold",
            phase="plan",
            enforcement="deny",
            configuration={"max_monthly": 1000},
        )
        engine = PolicyEngine([model])
        assert len(engine._policies) == 1
        assert isinstance(engine._policies[0], CostThresholdPolicy)

    def test_cost_data_in_policy_context(self):
        """cost_data field is accepted on PolicyContext."""
        ctx = PolicyContext(
            phase="plan",
            work_path=None,
            cost_data={"provisioners": {}},
        )
        assert ctx.cost_data is not None
