"""Tests for promotion models (ADR-0011)."""

from __future__ import annotations

from strata.models.promotion_model import ProgressionRingModel


class TestProgressionRingModelRequireLock:
    """ProgressionRingModel.require_lock field."""

    def test_require_lock_defaults_to_none(self):
        ring = ProgressionRingModel(name="prd", environments=["prod-be"])
        assert ring.require_lock is None

    def test_require_lock_true(self):
        ring = ProgressionRingModel(name="prd", environments=["prod-be"], require_lock=True)
        assert ring.require_lock is True

    def test_require_lock_false(self):
        ring = ProgressionRingModel(name="prd", environments=["prod-be"], require_lock=False)
        assert ring.require_lock is False

    def test_require_lock_in_dict_round_trip(self):
        data = {"name": "prd", "environments": ["prod-be"], "require_lock": True}
        ring = ProgressionRingModel.model_validate(data)
        assert ring.require_lock is True

    def test_other_fields_not_affected(self):
        ring = ProgressionRingModel(name="dev", environments=["dev1"], require="any_one", require_lock=True)
        assert ring.name == "dev"
        assert ring.require == "any_one"
        assert ring.require_lock is True
