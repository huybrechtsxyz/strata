#!/usr/bin/env python3
"""Tests for VersionModel YAML validation."""

from datetime import date

import pytest
from pydantic import ValidationError

from strata.models.version_model import VersionModel, VersionPinStatus


def _version(pins: dict | None = None) -> dict:
    return {"meta": {"name": "prd"}, "spec": {"pins": pins or {}}}


def test_version_minimal_is_valid():
    """A version document with no pins yet is valid."""
    model = VersionModel.model_validate({"meta": {"name": "prd"}, "spec": {}})
    assert model.meta.name == "prd"
    assert model.kind.value == "version"
    assert list(model.spec.pins.iter_pins()) == []


def test_version_rejects_mismatched_kind():
    """A document declaring another kind is rejected (ADR-0016)."""
    data = _version()
    data["kind"] = "deployment"
    with pytest.raises(ValidationError, match="Expected kind 'version'"):
        VersionModel.model_validate(data)


def test_version_rejects_v1_ring_field():
    """spec.ring duplicated meta.name and is not ported."""
    data = _version()
    data["spec"]["ring"] = "prd"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        VersionModel.model_validate(data)


# ---------------------------------------------------------------------------
# Shorthand and structured pins
# ---------------------------------------------------------------------------


def test_scalar_shorthand_becomes_a_pin():
    """A bare string is the same model as the structured form."""
    model = VersionModel.model_validate(_version({"images": {"caddy": "caddy:2-alpine"}}))
    pin = model.spec.pins.images["caddy"]
    assert pin.version == "caddy:2-alpine"
    assert pin.status is VersionPinStatus.CURRENT
    assert pin.reason is None


def test_structured_pin_keeps_its_rationale():
    """status/available/reason/reviewed are data, so a rewrite cannot lose them."""
    model = VersionModel.model_validate(
        _version(
            {
                "images": {
                    "db": {
                        "version": "docker.io/library/postgres:16-alpine",
                        "status": "held",
                        "available": "18.6-alpine",
                        "reason": "postgres majors need pg_upgrade/dump-restore",
                        "reviewed": "2026-09-08",
                    }
                }
            }
        )
    )
    pin = model.spec.pins.images["db"]
    assert pin.status is VersionPinStatus.HELD
    assert pin.available == "18.6-alpine"
    assert pin.reviewed == date(2026, 9, 8)
    assert "pg_upgrade" in pin.reason


def test_both_forms_produce_the_same_shape():
    """Consumers never have to branch on how a pin was written."""
    model = VersionModel.model_validate(_version({"charts": {"a": "1.0.0", "b": {"version": "1.0.0"}}}))
    assert model.spec.pins.charts["a"] == model.spec.pins.charts["b"]


def test_pin_requires_a_version():
    """An empty pin is meaningless."""
    with pytest.raises(ValidationError):
        VersionModel.model_validate(_version({"images": {"db": {"status": "held"}}}))


# ---------------------------------------------------------------------------
# The rationale rule — the reason this model exists
# ---------------------------------------------------------------------------


def test_held_pin_requires_a_reason():
    """An unexplained hold is exactly the knowledge v1 lost on every rewrite."""
    with pytest.raises(ValidationError, match="requires a 'reason'"):
        VersionModel.model_validate(_version({"images": {"redis": {"version": "redis:7-alpine", "status": "held"}}}))


def test_unverified_pin_requires_a_reason():
    """An unverified pin must say what could not be confirmed."""
    with pytest.raises(ValidationError, match="requires a 'reason'"):
        VersionModel.model_validate(_version({"charts": {"gatus": {"version": "1.0.0", "status": "unverified"}}}))


def test_current_pin_needs_no_reason():
    """The common case stays frictionless."""
    model = VersionModel.model_validate(_version({"charts": {"immich": "0.13.1"}}))
    assert model.spec.pins.charts["immich"].status is VersionPinStatus.CURRENT


def test_pin_rejects_unknown_status():
    """The status vocabulary is closed."""
    with pytest.raises(ValidationError):
        VersionModel.model_validate(_version({"images": {"db": {"version": "x", "status": "maybe"}}}))


# ---------------------------------------------------------------------------
# Categories and traversal
# ---------------------------------------------------------------------------


def test_all_categories_are_supported():
    """images/charts/remotes map to something strata itself materialises;
    artifacts is the one deliberate exception (docs/design/
    artifact-references.md) — a pinnable reference to something strata
    never fetches or deploys itself."""
    model = VersionModel.model_validate(
        _version(
            {
                "images": {"server": "ghcr.io/goauthentik/server:2026.5.6"},
                "charts": {"cert-manager": "v1.16.2"},
                "remotes": {"infra": "abc64feae2da19a61b76460269941399b04acb7b"},
                "artifacts": {"dspapi_container": "env_sbx11006400201_20260907.11"},
            }
        )
    )
    assert {c for c, _, _ in model.spec.pins.iter_pins()} == {"images", "charts", "remotes", "artifacts"}


def test_version_rejects_tools_pins():
    """CI installs tool binaries, so a tools pin could never take effect.

    The expected version lives on an Integration document instead
    (ProvisionerModel.integration -> IntegrationModel.version, ADR-0021 D4).
    """
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        VersionModel.model_validate(_version({"tools": {"terraform-main": "1.7.0"}}))


def test_iter_pins_reports_every_pin_once():
    """Resolution and logging share one traversal so they cannot drift."""
    model = VersionModel.model_validate(_version({"images": {"a": "1", "b": "2"}, "charts": {"c": "3"}}))
    assert sorted((cat, name) for cat, name, _ in model.spec.pins.iter_pins()) == [
        ("charts", "c"),
        ("images", "a"),
        ("images", "b"),
    ]


def test_version_rejects_unknown_pin_category():
    """A typo'd category would silently pin nothing."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        VersionModel.model_validate(_version({"modules": {"a": "1"}}))


def test_version_rejects_hash_field():
    """spec.hash was a v2-only field with zero real consumers, removed
    2026-10-06 (docs/work/version-lifecycle.md Phase 0) — its
    tamper-detection premise didn't hold up (the audit manifest's own git
    history is already the tamper-evidence)."""
    data = _version({"images": {"a": "1"}})
    data["spec"]["hash"] = "c3f7bbe0e93803073e8d080ace0e69be92b54185ecf94a4200d0c16d81a74c95"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        VersionModel.model_validate(data)


def test_artifacts_pin_is_a_separate_category_from_images():
    """pins.artifacts is not an overload of pins.images — the same name in
    both categories is two independent pins (no cross-category collision)."""
    model = VersionModel.model_validate(
        _version(
            {
                "images": {"dspapi_container": "ghcr.io/other:1.0"},
                "artifacts": {"dspapi_container": "env_sbx11006400201_20260907.11"},
            }
        )
    )
    assert model.spec.pins.images["dspapi_container"].version == "ghcr.io/other:1.0"
    assert model.spec.pins.artifacts["dspapi_container"].version == "env_sbx11006400201_20260907.11"


def test_artifacts_pin_shorthand_and_held_status():
    """Same shorthand/structured/rationale rules as every other category."""
    model = VersionModel.model_validate(
        _version(
            {
                "artifacts": {
                    "dspapi_container": {
                        "version": "env_sbx11006400201_20260907.10",
                        "status": "held",
                        "reason": "pending product team confirmation",
                    }
                }
            }
        )
    )
    pin = model.spec.pins.artifacts["dspapi_container"]
    assert pin.status is VersionPinStatus.HELD
    assert pin.reason == "pending product team confirmation"


# ---------------------------------------------------------------------------
# spec.promotion — VersionPromotionModel (docs/work/promotion.md Phase 1)
# ---------------------------------------------------------------------------


def _version_with_promotion(promotion: dict) -> dict:
    data = _version()
    data["spec"]["promotion"] = promotion
    return data


def test_promotion_is_omitted_by_default():
    """A single-app product with no rollout pipeline omits spec.promotion
    entirely — never a null/empty placeholder."""
    model = VersionModel.model_validate(_version())
    assert model.spec.promotion is None


def test_promotion_accepts_all_three_fields():
    model = VersionModel.model_validate(_version_with_promotion({"ring": "prd", "order": 3, "wave": "canary"}))
    assert model.spec.promotion.ring == "prd"
    assert model.spec.promotion.order == 3
    assert model.spec.promotion.wave == "canary"


def test_promotion_accepts_ring_and_order_with_no_wave():
    """wave is optional even when ring/order are set — most rings have only one wave."""
    model = VersionModel.model_validate(_version_with_promotion({"ring": "dev", "order": 1}))
    assert model.spec.promotion.ring == "dev"
    assert model.spec.promotion.order == 1
    assert model.spec.promotion.wave is None


def test_promotion_wave_without_ring_is_rejected():
    """A wave of *which* ring? No inference."""
    with pytest.raises(ValidationError, match="'wave' requires 'ring'"):
        VersionModel.model_validate(_version_with_promotion({"wave": "canary"}))


def test_promotion_ring_without_order_is_rejected():
    """order is the entire reason sortability works — no inferring it."""
    with pytest.raises(ValidationError, match="'ring' requires 'order'"):
        VersionModel.model_validate(_version_with_promotion({"ring": "prd"}))


def test_promotion_wave_without_ring_or_order_is_still_rejected_on_the_ring_rule():
    """wave alone (no ring, no order) is rejected on the wave->ring rule
    specifically — confirms the two validators don't mask each other."""
    with pytest.raises(ValidationError, match="'wave' requires 'ring'"):
        VersionModel.model_validate(_version_with_promotion({"wave": "canary", "order": 3}))


def test_promotion_rejects_unknown_fields():
    """extra='forbid' applies here too — same closed-schema discipline as
    every other model in this file."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        VersionModel.model_validate(_version_with_promotion({"ring": "prd", "order": 3, "scope": "tenant"}))


def test_promotion_ring_and_wave_must_be_non_empty():
    with pytest.raises(ValidationError):
        VersionModel.model_validate(_version_with_promotion({"ring": "", "order": 1}))
