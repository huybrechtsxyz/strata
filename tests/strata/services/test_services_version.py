#!/usr/bin/env python3
"""Tests for VersionService pin lookup."""

from strata.services.version_service import VersionService


def _service() -> VersionService:
    service = VersionService(
        data={
            "meta": {"name": "prd"},
            "spec": {
                "pins": {
                    "images": {"server": "ghcr.io/goauthentik/server:2026.5.6"},
                    "charts": {"immich": "0.13.1"},
                    "remotes": {"infra": "abc64feae2da19a61b76460269941399b04acb7b"},
                }
            },
        }
    )
    service.validate()
    return service


def test_service_validates_from_data():
    """A VersionService constructed from an in-memory dict validates."""
    assert _service().model is not None


def test_resolve_returns_the_pin_for_a_known_target():
    """A pinned target resolves to its pin, so the pin can win over the document."""
    pin = _service().resolve("images", "server")
    assert pin.version == "ghcr.io/goauthentik/server:2026.5.6"


def test_resolve_returns_none_for_an_unpinned_target():
    """An unpinned target leaves the declaring document's own value standing."""
    assert _service().resolve("images", "not-pinned") is None


def test_resolve_is_category_scoped():
    """A name pinned in one category does not leak into another."""
    service = _service()
    assert service.resolve("charts", "immich") is not None
    assert service.resolve("images", "immich") is None


def test_resolve_returns_none_for_unknown_category():
    """An unknown category resolves to nothing rather than raising."""
    assert _service().resolve("tools", "terraform-main") is None


def test_resolve_covers_every_category():
    """All categories are reachable through the same lookup."""
    service = _service()
    assert service.resolve("images", "server") is not None
    assert service.resolve("remotes", "infra").version.startswith("abc64fe")
