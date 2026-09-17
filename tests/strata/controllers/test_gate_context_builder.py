#!/usr/bin/env python3
"""Unit tests for GateContextBuilder's cost-delta reading (ADR-0031 section 3a)."""

import json
from unittest.mock import MagicMock

from strata.controllers.gate_context_builder import GateContextBuilder


def _make_deployment_service(build_path):
    ds = MagicMock()
    ds.get_build_path.return_value = build_path
    return ds


class TestReadCostDelta:
    def test_returns_none_when_no_build_path(self):
        builder = GateContextBuilder(build_path=None, deployment_service=MagicMock())
        assert builder._read_cost_delta() is None

    def test_returns_none_when_cost_json_missing(self, tmp_path):
        ds = _make_deployment_service(tmp_path)
        builder = GateContextBuilder(build_path=tmp_path, deployment_service=ds)
        assert builder._read_cost_delta() is None

    def test_reads_unified_provisioners_shape(self, tmp_path):
        """The shape written by CostController.diff() (ADR-0031 section 3a)."""
        cost_json = {
            "provisioners": {
                "terraform": {"totalMonthlyCost": "220.00", "pastTotalMonthlyCost": "200.00"},
            }
        }
        (tmp_path / "cost.json").write_text(json.dumps(cost_json), encoding="utf-8")
        ds = _make_deployment_service(tmp_path)
        builder = GateContextBuilder(build_path=tmp_path, deployment_service=ds)
        assert builder._read_cost_delta() == 20.00

    def test_falls_back_to_legacy_diff_nested_shape(self, tmp_path):
        """Older cost.json files (pre-3a) nested the raw Infracost diff under "diff"."""
        cost_json = {"diff": {"totalMonthlyCost": "220.00", "pastTotalMonthlyCost": "200.00"}}
        (tmp_path / "cost.json").write_text(json.dumps(cost_json), encoding="utf-8")
        ds = _make_deployment_service(tmp_path)
        builder = GateContextBuilder(build_path=tmp_path, deployment_service=ds)
        assert builder._read_cost_delta() == 20.00

    def test_returns_none_for_breakdown_only_snapshot(self, tmp_path):
        """A `strata cost show` breakdown snapshot has no past total — no delta."""
        cost_json = {"provisioners": {"terraform": {"breakdown": {"totalMonthlyCost": "200.00"}}}}
        (tmp_path / "cost.json").write_text(json.dumps(cost_json), encoding="utf-8")
        ds = _make_deployment_service(tmp_path)
        builder = GateContextBuilder(build_path=tmp_path, deployment_service=ds)
        assert builder._read_cost_delta() is None

    def test_returns_none_on_malformed_json(self, tmp_path):
        (tmp_path / "cost.json").write_text("not json", encoding="utf-8")
        ds = _make_deployment_service(tmp_path)
        builder = GateContextBuilder(build_path=tmp_path, deployment_service=ds)
        assert builder._read_cost_delta() is None
