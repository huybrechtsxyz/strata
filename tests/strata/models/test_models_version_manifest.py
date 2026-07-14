"""Tests for VersionManifestModel spec.hash field (ADR-0011 Layer 2)."""

from __future__ import annotations

from strata.models.version_manifest_model import VersionManifestModel, VersionManifestSpecModel


class TestVersionManifestSpecHash:
    """spec.hash field is optional and round-trips correctly."""

    def test_hash_defaults_to_none(self):
        spec = VersionManifestSpecModel(ring="prd")
        assert spec.hash is None

    def test_hash_can_be_set(self):
        spec = VersionManifestSpecModel(ring="prd", hash="abc123")
        assert spec.hash == "abc123"

    def test_hash_round_trips_in_model_validate(self):
        raw = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "version",
            "meta": {"name": "prd"},
            "spec": {
                "ring": "prd",
                "hash": "deadbeef" * 8,  # 64 hex chars like a sha256
                "pins": {"images": {"app": "v2.1.0"}},
            },
        }
        m = VersionManifestModel.model_validate(raw)
        assert m.spec.hash == "deadbeef" * 8

    def test_hash_is_none_when_absent(self):
        raw = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "version",
            "meta": {"name": "dev"},
            "spec": {"ring": "dev", "pins": {}},
        }
        m = VersionManifestModel.model_validate(raw)
        assert m.spec.hash is None
