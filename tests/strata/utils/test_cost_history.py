#!/usr/bin/env python3
"""Unit tests for CostHistoryStore."""

from pathlib import Path

import pytest

from strata.utils.cost_history import CostHistoryStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _store(tmp_path: Path, name: str = "production", max_snapshots: int = 50) -> CostHistoryStore:
    return CostHistoryStore(work_path=tmp_path, deployment_name=name, max_snapshots=max_snapshots)


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

_COST_DATA_6000 = {"provisioners": {"terraform": {"breakdown": {"totalMonthlyCost": "6000.00"}}}}

_COST_DATA_MULTI = {
    "provisioners": {
        "infra": {"breakdown": {"totalMonthlyCost": "3000.00"}},
        "platform": {"breakdown": {"totalMonthlyCost": "2000.00"}},
    }
}


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------


class TestCostHistoryLoadSave:
    def test_load_creates_empty_when_no_file(self, tmp_path):
        store = _store(tmp_path)
        store.load()
        assert store.list_snapshots() == []

    def test_load_reads_existing_file(self, tmp_path):
        store = _store(tmp_path)
        store.load()
        store.record_snapshot(_COST_DATA_5000)
        store.save()

        store2 = _store(tmp_path)
        store2.load()
        assert len(store2.list_snapshots()) == 1

    def test_save_creates_directory(self, tmp_path):
        store = _store(tmp_path, "myapp")
        store.load()
        store.record_snapshot(_COST_DATA_5000)
        store.save()

        history_file = tmp_path / ".strata" / "cost" / "myapp.cost-history.json"
        assert history_file.exists()

    def test_load_tolerates_corrupt_file(self, tmp_path):
        cost_dir = tmp_path / ".strata" / "cost"
        cost_dir.mkdir(parents=True)
        (cost_dir / "production.cost-history.json").write_text("not json", encoding="utf-8")

        store = _store(tmp_path)
        store.load()
        assert store.list_snapshots() == []

    def test_save_nonfatal_on_write_error(self, tmp_path):
        from unittest.mock import patch

        store = _store(tmp_path)
        store.load()
        store.record_snapshot(_COST_DATA_5000)
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            store.save()  # should not raise


# ---------------------------------------------------------------------------
# record_snapshot
# ---------------------------------------------------------------------------


class TestCostHistoryRecordSnapshot:
    def test_appends_snapshot(self, tmp_path):
        store = _store(tmp_path)
        store.load()
        store.record_snapshot(_COST_DATA_5000)
        assert len(store.list_snapshots()) == 1

    def test_snapshot_has_required_fields(self, tmp_path):
        store = _store(tmp_path)
        store.load()
        store.record_snapshot(_COST_DATA_5000, version="1.0.0", currency="EUR")
        snap = store.latest()
        assert snap is not None
        assert "recorded_at" in snap
        assert snap["total_monthly"] == 5000.0
        assert snap["currency"] == "EUR"
        assert snap["version"] == "1.0.0"
        assert "provisioners" in snap

    def test_first_snapshot_has_null_delta(self, tmp_path):
        store = _store(tmp_path)
        store.load()
        store.record_snapshot(_COST_DATA_5000)
        snap = store.latest()
        assert snap is not None
        assert snap["delta_from_previous"] is None

    def test_delta_computed_from_previous(self, tmp_path):
        store = _store(tmp_path)
        store.load()
        store.record_snapshot(_COST_DATA_5000)
        store.record_snapshot(_COST_DATA_6000)
        snaps = store.list_snapshots()
        assert snaps[-1]["delta_from_previous"] == pytest.approx(1000.0)

    def test_negative_delta_when_cost_decreases(self, tmp_path):
        store = _store(tmp_path)
        store.load()
        store.record_snapshot(_COST_DATA_6000)
        store.record_snapshot(_COST_DATA_5000)
        snaps = store.list_snapshots()
        assert snaps[-1]["delta_from_previous"] == pytest.approx(-1000.0)

    def test_sums_multi_provisioner_total(self, tmp_path):
        store = _store(tmp_path)
        store.load()
        store.record_snapshot(_COST_DATA_MULTI)
        snap = store.latest()
        assert snap is not None
        assert snap["total_monthly"] == pytest.approx(5000.0)
        assert "infra" in snap["provisioners"]
        assert "platform" in snap["provisioners"]

    def test_multiple_snapshots_accumulate(self, tmp_path):
        store = _store(tmp_path)
        store.load()
        for _ in range(5):
            store.record_snapshot(_COST_DATA_5000)
        assert len(store.list_snapshots()) == 5

    def test_version_omitted_when_none(self, tmp_path):
        store = _store(tmp_path)
        store.load()
        store.record_snapshot(_COST_DATA_5000, version=None)
        snap = store.latest()
        assert "version" not in (snap or {})


# ---------------------------------------------------------------------------
# max_snapshots cap
# ---------------------------------------------------------------------------


class TestCostHistoryMaxSnapshots:
    def test_trims_to_max_snapshots(self, tmp_path):
        store = _store(tmp_path, max_snapshots=3)
        store.load()
        for i in range(5):
            store.record_snapshot(_COST_DATA_5000)
        assert len(store.list_snapshots()) == 3

    def test_retains_most_recent(self, tmp_path):
        store = _store(tmp_path, max_snapshots=2)
        store.load()
        store.record_snapshot(_COST_DATA_5000, version="1.0")
        store.record_snapshot(_COST_DATA_6000, version="2.0")
        store.record_snapshot(_COST_DATA_5000, version="3.0")
        snaps = store.list_snapshots()
        assert len(snaps) == 2
        assert snaps[0]["version"] == "2.0"
        assert snaps[1]["version"] == "3.0"


# ---------------------------------------------------------------------------
# list_snapshots / latest
# ---------------------------------------------------------------------------


class TestCostHistoryQuery:
    def test_list_snapshots_returns_all_by_default(self, tmp_path):
        store = _store(tmp_path)
        store.load()
        for _ in range(7):
            store.record_snapshot(_COST_DATA_5000)
        assert len(store.list_snapshots()) == 7

    def test_list_snapshots_last_n(self, tmp_path):
        store = _store(tmp_path)
        store.load()
        for _ in range(7):
            store.record_snapshot(_COST_DATA_5000)
        assert len(store.list_snapshots(last=3)) == 3

    def test_latest_returns_none_when_empty(self, tmp_path):
        store = _store(tmp_path)
        store.load()
        assert store.latest() is None

    def test_latest_returns_most_recent(self, tmp_path):
        store = _store(tmp_path)
        store.load()
        store.record_snapshot(_COST_DATA_5000, version="1.0")
        store.record_snapshot(_COST_DATA_6000, version="2.0")
        snap = store.latest()
        assert snap is not None
        assert snap["version"] == "2.0"


# ---------------------------------------------------------------------------
# _extract_total formats
# ---------------------------------------------------------------------------


class TestExtractTotal:
    def test_breakdown_format(self):
        result = CostHistoryStore._extract_total({"breakdown": {"totalMonthlyCost": "1234.56"}})
        assert result == pytest.approx(1234.56)

    def test_projects_format(self):
        result = CostHistoryStore._extract_total(
            {
                "projects": [
                    {"breakdown": {"totalMonthlyCost": "1000.00"}},
                    {"breakdown": {"totalMonthlyCost": "500.00"}},
                ]
            }
        )
        assert result == pytest.approx(1500.0)

    def test_top_level_format(self):
        result = CostHistoryStore._extract_total({"totalMonthlyCost": "999.99"})
        assert result == pytest.approx(999.99)

    def test_returns_none_for_unknown_format(self):
        assert CostHistoryStore._extract_total({"some_other_key": "value"}) is None

    def test_returns_none_for_non_dict(self):
        assert CostHistoryStore._extract_total("not a dict") is None


# ---------------------------------------------------------------------------
# get_cost_dir integration
# ---------------------------------------------------------------------------


class TestCostDir:
    def test_history_file_path(self, tmp_path):
        store = _store(tmp_path, "my-deployment")
        expected = tmp_path / ".strata" / "cost" / "my-deployment.cost-history.json"
        assert store._history_file == expected
