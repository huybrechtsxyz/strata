"""Tests for PromotionStrategyModel.versions_path and overlap validation (ADR-0011)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from strata.models.promotion_model import (
    ConfigurationPromotionsModel,
    PromotionStrategyModel,
)


class TestPromotionStrategyVersionsPath:
    """versions_path field on PromotionStrategyModel."""

    def test_versions_path_defaults_to_none(self):
        s = PromotionStrategyModel(name="app", type="image", progression="standard")
        assert s.versions_path is None

    def test_versions_path_can_be_set(self):
        s = PromotionStrategyModel(
            name="app",
            type="image",
            progression="standard",
            versions_path="versions/app/",
        )
        assert s.versions_path == "versions/app/"

    def test_versions_path_accepts_repo_syntax(self):
        s = PromotionStrategyModel(
            name="app",
            type="image",
            progression="standard",
            versions_path="@config/versions/customer/",
        )
        assert s.versions_path == "@config/versions/customer/"


def _make_promotions(strategies: list[dict]) -> dict:
    """Build a ConfigurationPromotionsModel raw dict with the given strategies."""
    return {
        "progressions": [{"name": "standard", "rings": [{"name": "dev", "environments": ["dev1"]}]}],
        "strategies": strategies,
    }


class TestConfigurationPromotionsOverlapValidation:
    """Overlapping versions_path raises a ValidationError."""

    def test_no_overlap_passes(self):
        raw = _make_promotions(
            [
                {"name": "app", "type": "image", "progression": "standard", "versions_path": "versions/app/"},
                {"name": "infra", "type": "remote", "progression": "standard", "versions_path": "versions/infra/"},
            ]
        )
        m = ConfigurationPromotionsModel.model_validate(raw)
        assert len(m.strategies) == 2

    def test_overlap_raises(self):
        raw = _make_promotions(
            [
                {"name": "app", "type": "image", "progression": "standard", "versions_path": "versions/shared/"},
                {"name": "infra", "type": "remote", "progression": "standard", "versions_path": "versions/shared/"},
            ]
        )
        with pytest.raises(ValidationError, match="share versions_path"):
            ConfigurationPromotionsModel.model_validate(raw)

    def test_overlap_trailing_slash_normalised(self):
        # "versions/app/" and "versions/app" should be considered the same
        raw = _make_promotions(
            [
                {"name": "app", "type": "image", "progression": "standard", "versions_path": "versions/app/"},
                {"name": "app2", "type": "image", "progression": "standard", "versions_path": "versions/app"},
            ]
        )
        with pytest.raises(ValidationError, match="share versions_path"):
            ConfigurationPromotionsModel.model_validate(raw)

    def test_none_versions_path_ignored(self):
        # Strategies without versions_path should not trigger overlap detection
        raw = _make_promotions(
            [
                {"name": "app", "type": "image", "progression": "standard"},
                {"name": "infra", "type": "remote", "progression": "standard"},
            ]
        )
        m = ConfigurationPromotionsModel.model_validate(raw)
        assert len(m.strategies) == 2
