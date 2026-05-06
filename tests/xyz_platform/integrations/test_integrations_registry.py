#!/usr/bin/env python3
"""Unit tests for IntegrationRegistry."""

import pytest

from xyz_platform.integrations.registry import IntegrationRegistry


class FakeIntegration:
    """Minimal stand-in — just needs is_available()."""

    def __init__(self, available=True):
        self._available = available

    def is_available(self):
        return self._available


class TestIntegrationRegistrySingleton:
    def setup_method(self):
        IntegrationRegistry.reset()

    def test_singleton(self):
        a = IntegrationRegistry()
        b = IntegrationRegistry()
        assert a is b

    def test_get_instance(self):
        r = IntegrationRegistry.get_instance()
        assert isinstance(r, IntegrationRegistry)

    def test_reset_gives_new_instance(self):
        a = IntegrationRegistry()
        IntegrationRegistry.reset()
        b = IntegrationRegistry()
        assert a is not b


class TestIntegrationRegistryRegister:
    def setup_method(self):
        IntegrationRegistry.reset()

    def test_register_integration(self):
        r = IntegrationRegistry()
        r.register_integration("git", FakeIntegration())
        assert r.is_integration_registered("git")

    def test_register_without_is_available_raises(self):
        r = IntegrationRegistry()
        with pytest.raises(ValueError, match="is_available"):
            r.register_integration("bad", object())

    def test_get_integration_returns_instance(self):
        r = IntegrationRegistry()
        fake = FakeIntegration()
        r.register_integration("git", fake)
        assert r.get_integration("git") is fake

    def test_get_integration_returns_none_for_unknown(self):
        r = IntegrationRegistry()
        assert r.get_integration("nonexistent") is None

    def test_get_all_integrations(self):
        r = IntegrationRegistry()
        r.register_integration("git", FakeIntegration())
        r.register_integration("terraform", FakeIntegration())
        all_i = r.get_all_integrations()
        assert "git" in all_i
        assert "terraform" in all_i


class TestIntegrationRegistryAvailability:
    def setup_method(self):
        IntegrationRegistry.reset()

    def test_available_integration(self):
        r = IntegrationRegistry()
        r.register_integration("git", FakeIntegration(available=True))
        assert r.is_integration_available("git") is True

    def test_unavailable_integration(self):
        r = IntegrationRegistry()
        r.register_integration("git", FakeIntegration(available=False))
        assert r.is_integration_available("git") is False

    def test_unregistered_is_not_available(self):
        r = IntegrationRegistry()
        assert r.is_integration_available("nonexistent") is False


class TestIntegrationRegistryRequirements:
    def setup_method(self):
        IntegrationRegistry.reset()

    def test_register_and_validate_operation_all_available(self):
        r = IntegrationRegistry()
        r.register_integration("git", FakeIntegration())
        r.register_integration("terraform", FakeIntegration())
        r.register_requirement("deploy", ["git", "terraform"])
        ok, errors = r.validate_operation("deploy")
        assert ok
        assert errors == []

    def test_validate_operation_missing_integration(self):
        r = IntegrationRegistry()
        r.register_requirement("deploy", ["git"])
        ok, errors = r.validate_operation("deploy")
        assert not ok
        assert any("git" in e for e in errors)

    def test_validate_operation_unknown_operation(self):
        r = IntegrationRegistry()
        # Unregistered operation is treated as a configuration error
        ok, errors = r.validate_operation("nonexistent_op")
        assert not ok
        assert any("not registered" in e for e in errors)
