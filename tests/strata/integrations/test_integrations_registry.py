#!/usr/bin/env python3
"""Tests for the lean lazily-loaded registry (ADR-0021 D2, D3, D10)."""

from dataclasses import dataclass
from typing import Any

import pytest

from strata.integrations import registry
from strata.integrations.azure_appconfig_resolver import AzureAppConfigResolver
from strata.integrations.azure_keyvault_resolver import AzureKeyVaultResolver
from strata.integrations.base import Integration
from strata.integrations.infisical_resolver import InfisicalResolver
from strata.integrations.registry import IntegrationNotFoundError, get


@dataclass
class _FakeEntryPoint:
    """Duck-types the bits of `importlib.metadata.EntryPoint` the registry actually uses."""

    name: str
    value: str
    _target: Any

    def load(self) -> Any:
        return self._target


def test_get_returns_an_instance_of_the_registered_class():
    instance = get("infisical")
    assert isinstance(instance, InfisicalResolver)


def test_get_returns_the_right_class_per_built_in_type():
    assert isinstance(get("azure-keyvault"), AzureKeyVaultResolver)
    assert isinstance(get("azure-appconfig"), AzureAppConfigResolver)


def test_get_constructs_a_new_instance_every_call():
    """No cache lives in the registry itself (ADR-0021 D3) — that's the caller's job."""
    assert get("infisical") is not get("infisical")


def test_unknown_type_raises_with_no_v1_hint():
    with pytest.raises(IntegrationNotFoundError, match="no integration registered for type 'totally-made-up'"):
        get("totally-made-up")


def test_unknown_type_known_from_v1_gets_a_hint():
    with pytest.raises(IntegrationNotFoundError, match="not yet ported to v2"):
        get("vault")


def test_entry_point_plugin_is_discovered(monkeypatch):
    class _FakePlugin(Integration):
        TYPE = "servicenow"
        CAPABILITIES = frozenset({"x-ticketing"})
        TRANSPORTS = frozenset({"http"})

    fake_entry_point = _FakeEntryPoint(name="servicenow", value="does.not.matter:Ignored", _target=_FakePlugin)
    monkeypatch.setattr(registry, "entry_points", lambda group: [fake_entry_point])

    instance = get("servicenow")
    assert isinstance(instance, _FakePlugin)


def test_entry_point_colliding_with_a_built_in_name_is_an_error(monkeypatch):
    fake_entry_point = _FakeEntryPoint(name="infisical", value="does.not.matter:Ignored", _target=InfisicalResolver)
    monkeypatch.setattr(registry, "entry_points", lambda group: [fake_entry_point])

    with pytest.raises(IntegrationNotFoundError, match="built-in integration type"):
        get("infisical")


def test_multiple_entry_points_for_the_same_unknown_type_is_an_error(monkeypatch):
    class _PluginA(Integration):
        TYPE = "servicenow"
        CAPABILITIES = frozenset({"x-ticketing"})
        TRANSPORTS = frozenset({"http"})

    class _PluginB(Integration):
        TYPE = "servicenow"
        CAPABILITIES = frozenset({"x-ticketing"})
        TRANSPORTS = frozenset({"http"})

    first = _FakeEntryPoint(name="servicenow", value="pkg_a:PluginA", _target=_PluginA)
    second = _FakeEntryPoint(name="servicenow", value="pkg_b:PluginB", _target=_PluginB)
    monkeypatch.setattr(registry, "entry_points", lambda group: [first, second])

    with pytest.raises(IntegrationNotFoundError, match="multiple installed plugins"):
        get("servicenow")
