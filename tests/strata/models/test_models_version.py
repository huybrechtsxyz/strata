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
    model = VersionModel.model_validate(
        _version({"charts": {"a": "1.0.0", "b": {"version": "1.0.0"}}})
    )
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
    """images/charts/remotes each map to something strata itself materialises."""
    model = VersionModel.model_validate(
        _version(
            {
                "images": {"server": "ghcr.io/goauthentik/server:2026.5.6"},
                "charts": {"cert-manager": "v1.16.2"},
                "remotes": {"infra": "abc64feae2da19a61b76460269941399b04acb7b"},
            }
        )
    )
    assert {c for c, _, _ in model.spec.pins.iter_pins()} == {"images", "charts", "remotes"}


def test_version_rejects_tools_pins():
    """CI installs tool binaries, so a tools pin could never take effect.

    The expected version lives on an Integration document instead
    (ProvisionerModel.integration -> IntegrationModel.version, ADR-0021 D4).
    """
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        VersionModel.model_validate(_version({"tools": {"terraform-main": "1.7.0"}}))


def test_iter_pins_reports_every_pin_once():
    """Resolution and logging share one traversal so they cannot drift."""
    model = VersionModel.model_validate(
        _version({"images": {"a": "1", "b": "2"}, "charts": {"c": "3"}})
    )
    assert sorted((cat, name) for cat, name, _ in model.spec.pins.iter_pins()) == [
        ("charts", "c"),
        ("images", "a"),
        ("images", "b"),
    ]


def test_version_rejects_unknown_pin_category():
    """A typo'd category would silently pin nothing."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        VersionModel.model_validate(_version({"modules": {"a": "1"}}))


def test_version_accepts_tooling_hash():
    """hash is written by tooling for tamper detection and round-trips."""
    data = _version({"images": {"a": "1"}})
    data["spec"]["hash"] = "c3f7bbe0e93803073e8d080ace0e69be92b54185ecf94a4200d0c16d81a74c95"
    model = VersionModel.model_validate(data)
    assert model.spec.hash.startswith("c3f7bbe0")
