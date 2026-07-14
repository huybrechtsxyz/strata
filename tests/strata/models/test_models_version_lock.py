"""Tests for VersionLockModel pointer/pins redesign (ADR-0011 Phase B)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from strata.models.version_lock_model import VersionLockModel, VersionLockPreviousModel, VersionLockSpecModel


class TestVersionLockSpecModelValidation:
    """Exactly one of source or pins must be set."""

    def test_pointer_lock_valid(self):
        spec = VersionLockSpecModel(ring="prd", source="v2.1.0.yaml")
        assert spec.source == "v2.1.0.yaml"
        assert spec.pins is None
        assert spec.ring == "prd"

    def test_pins_lock_valid(self):
        spec = VersionLockSpecModel(ring="prd", pins=[])
        assert spec.pins == []
        assert spec.source is None

    def test_neither_source_nor_pins_raises(self):
        with pytest.raises(ValidationError, match="must have either spec.source"):
            VersionLockSpecModel(ring="prd")

    def test_both_source_and_pins_raises(self):
        with pytest.raises(ValidationError, match="mutually exclusive"):
            VersionLockSpecModel(ring="prd", source="v2.yaml", pins=[])

    def test_scope_allowed_with_source(self):
        spec = VersionLockSpecModel(ring="prd", source="v2.1.0.yaml", scope="tenant")
        assert spec.scope == "tenant"

    def test_scope_selector_allowed_with_source(self):
        spec = VersionLockSpecModel(ring="prd", source="v2.1.0.yaml", scope_selector="acme")
        assert spec.scope_selector == "acme"


class TestVersionLockModelIsPointer:
    """VersionLockModel.is_pointer property."""

    def test_is_pointer_true_when_source_set(self):
        model = VersionLockModel.model_validate({
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "version-lock",
            "meta": {"name": "prd"},
            "spec": {"ring": "prd", "source": "v2.1.0.yaml"},
        })
        assert model.is_pointer is True

    def test_is_pointer_false_when_pins_set(self):
        model = VersionLockModel.model_validate({
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "version-lock",
            "meta": {"name": "prd"},
            "spec": {"ring": "prd", "pins": []},
        })
        assert model.is_pointer is False

    def test_round_trip_pointer_yaml(self):
        raw = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "version-lock",
            "meta": {"name": "dev"},
            "spec": {"ring": "dev", "source": "../v1.0.0.yaml"},
        }
        m = VersionLockModel.model_validate(raw)
        assert m.spec.source == "../v1.0.0.yaml"
        assert m.spec.ring == "dev"


class TestVersionLockSpecModelNewFields:
    """spec.hash / spec.version / spec.wave / spec.previous on pointer locks."""

    def test_hash_accepted(self):
        spec = VersionLockSpecModel(
            ring="prd",
            source="v2.1.0.yaml",
            hash="sha256:abcdef1234abcdef1234abcdef1234abcdef1234abcdef1234abcdef1234abcd",
        )
        assert spec.hash.startswith("sha256:")

    def test_version_accepted(self):
        spec = VersionLockSpecModel(ring="prd", source="v2.1.0.yaml", version="v2.1.0")
        assert spec.version == "v2.1.0"

    def test_wave_accepted(self):
        spec = VersionLockSpecModel(ring="prd", source="v2.1.0.yaml", wave=3)
        assert spec.wave == 3

    def test_previous_accepted(self):
        prev = VersionLockPreviousModel(
            source="v2.0.0.yaml",
            version="v2.0.0",
            hash="sha256:0000000000000000000000000000000000000000000000000000000000000000",
        )
        spec = VersionLockSpecModel(ring="prd", source="v2.1.0.yaml", previous=prev)
        assert spec.previous.source == "v2.0.0.yaml"
        assert spec.previous.version == "v2.0.0"

    def test_previous_source_only(self):
        """previous.version and previous.hash are optional."""
        prev = VersionLockPreviousModel(source="v2.0.0.yaml")
        spec = VersionLockSpecModel(ring="prd", source="v2.1.0.yaml", previous=prev)
        assert spec.previous.source == "v2.0.0.yaml"
        assert spec.previous.version is None
        assert spec.previous.hash is None

    def test_new_fields_round_trip_yaml(self):
        raw = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "version-lock",
            "meta": {"name": "prd"},
            "spec": {
                "ring": "prd",
                "source": "v2.1.0.yaml",
                "hash": "sha256:abcdef1234abcdef1234abcdef1234abcdef1234abcdef1234abcdef1234abcd",
                "version": "v2.1.0",
                "wave": None,
                "previous": {
                    "source": "v2.0.0.yaml",
                    "version": "v2.0.0",
                    "hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
                },
            },
        }
        m = VersionLockModel.model_validate(raw)
        assert m.spec.hash == raw["spec"]["hash"]
        assert m.spec.version == "v2.1.0"
        assert m.spec.wave is None
        assert m.spec.previous.source == "v2.0.0.yaml"
        assert m.spec.previous.version == "v2.0.0"

    def test_new_fields_absent_on_inline_pins(self):
        """Inline-pins lock can also optionally carry the new fields (no constraint)."""
        spec = VersionLockSpecModel(ring="prd", pins=[], hash=None, version=None, wave=None, previous=None)
        assert spec.hash is None
        assert spec.wave is None
