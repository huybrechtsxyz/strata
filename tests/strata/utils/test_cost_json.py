#!/usr/bin/env python3
"""Unit tests for strata.utils.cost_json — the shared cost.json parser (ADR-0031 section 3a)."""

from strata.utils.cost_json import (
    extract_cost_delta,
    extract_provisioner_value,
    extract_total_monthly,
)

# ---------------------------------------------------------------------------
# extract_provisioner_value
# ---------------------------------------------------------------------------


class TestExtractProvisionerValue:
    def test_breakdown_shape(self):
        prov = {"breakdown": {"totalMonthlyCost": "1202.40"}}
        assert extract_provisioner_value(prov, "totalMonthlyCost") == 1202.40

    def test_projects_shape_sums_all(self):
        prov = {
            "projects": [
                {"breakdown": {"totalMonthlyCost": "100.00"}},
                {"breakdown": {"totalMonthlyCost": "50.00"}},
            ]
        }
        assert extract_provisioner_value(prov, "totalMonthlyCost") == 150.00

    def test_top_level_shape(self):
        prov = {"totalMonthlyCost": "200.00", "pastTotalMonthlyCost": "0.00"}
        assert extract_provisioner_value(prov, "totalMonthlyCost") == 200.00
        assert extract_provisioner_value(prov, "pastTotalMonthlyCost") == 0.00

    def test_not_a_dict_returns_none(self):
        assert extract_provisioner_value("not-a-dict", "totalMonthlyCost") is None

    def test_unparseable_value_returns_none(self):
        prov = {"totalMonthlyCost": "not-a-number"}
        assert extract_provisioner_value(prov, "totalMonthlyCost") is None

    def test_missing_key_returns_none(self):
        assert extract_provisioner_value({}, "totalMonthlyCost") is None


# ---------------------------------------------------------------------------
# extract_total_monthly
# ---------------------------------------------------------------------------


class TestExtractTotalMonthly:
    def test_sums_across_provisioners(self):
        cost_data = {
            "provisioners": {
                "infra": {"breakdown": {"totalMonthlyCost": "3000.00"}},
                "platform": {"breakdown": {"totalMonthlyCost": "2000.00"}},
            }
        }
        assert extract_total_monthly(cost_data) == 5000.00

    def test_falls_back_to_top_level(self):
        cost_data = {"totalMonthlyCost": "42.00"}
        assert extract_total_monthly(cost_data) == 42.00

    def test_no_data_returns_none(self):
        assert extract_total_monthly({"provisioners": {}}) is None

    def test_custom_key(self):
        cost_data = {"provisioners": {"terraform": {"totalMonthlyCost": "200.00", "pastTotalMonthlyCost": "50.00"}}}
        assert extract_total_monthly(cost_data, key="pastTotalMonthlyCost") == 50.00


# ---------------------------------------------------------------------------
# extract_cost_delta
# ---------------------------------------------------------------------------


class TestExtractCostDelta:
    def test_computes_delta_from_wrapped_diff(self):
        cost_data = {
            "provisioners": {
                "terraform": {"totalMonthlyCost": "200.00", "pastTotalMonthlyCost": "150.00"},
            }
        }
        assert extract_cost_delta(cost_data) == 50.00

    def test_computes_delta_from_top_level(self):
        cost_data = {"totalMonthlyCost": "200.00", "pastTotalMonthlyCost": "150.00"}
        assert extract_cost_delta(cost_data) == 50.00

    def test_none_when_past_total_missing(self):
        """A plain `breakdown` snapshot (from `strata cost show`) has no past total
        to compare against — there is no delta to report, not a delta of zero."""
        cost_data = {"provisioners": {"terraform": {"breakdown": {"totalMonthlyCost": "200.00"}}}}
        assert extract_cost_delta(cost_data) is None

    def test_negative_delta(self):
        cost_data = {"totalMonthlyCost": "100.00", "pastTotalMonthlyCost": "150.00"}
        assert extract_cost_delta(cost_data) == -50.00
