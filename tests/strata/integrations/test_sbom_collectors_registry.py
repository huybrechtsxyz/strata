#!/usr/bin/env python3
"""Tests for the SBOM collector registry (docs/design/sbom-generation.md
Extensibility section) — structurally mirrors
`test_integrations_registry.py` (ADR-0021 D10's precedent this registry
copies)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.integrations.sbom_collectors import registry
from strata.integrations.sbom_collectors.base import CollectorResult, SbomCollector
from strata.integrations.sbom_collectors.compose_collector import ComposeCollector
from strata.integrations.sbom_collectors.helm_collector import HelmCollector
from strata.integrations.sbom_collectors.image_collector import ImageCollector
from strata.integrations.sbom_collectors.registry import CollectorNotFoundError, get
from strata.integrations.sbom_collectors.terraform_collector import TerraformCollector
from strata.models.module_model import ModuleModel


@dataclass
class _FakeEntryPoint:
    """Duck-types the bits of `importlib.metadata.EntryPoint` the registry actually uses."""

    name: str
    value: str
    _target: Any

    def load(self) -> Any:
        return self._target


def test_get_returns_an_instance_of_the_registered_class():
    assert isinstance(get("image"), ImageCollector)
    assert isinstance(get("compose"), ComposeCollector)
    assert isinstance(get("helm"), HelmCollector)
    assert isinstance(get("terraform"), TerraformCollector)


def test_get_constructs_a_new_instance_every_call():
    assert get("image") is not get("image")


def test_list_collectors_returns_every_built_in():
    assert set(registry.list_collectors()) == {"image", "compose", "helm", "terraform"}


def test_unknown_name_raises():
    with pytest.raises(CollectorNotFoundError, match="no SBOM collector registered for name 'totally-made-up'"):
        get("totally-made-up")


def test_entry_point_plugin_is_discovered(monkeypatch):
    class _FakeCollector(SbomCollector):
        def collect(
            self, graph: ResolvedWorkspaceGraph, modules: list[ModuleModel], build_path: Path
        ) -> CollectorResult:
            return CollectorResult()

    fake_entry_point = _FakeEntryPoint(name="npm", value="does.not.matter:Ignored", _target=_FakeCollector)
    monkeypatch.setattr(registry, "entry_points", lambda group: [fake_entry_point])

    instance = get("npm")
    assert isinstance(instance, _FakeCollector)


def test_entry_point_plugin_is_included_in_list_collectors(monkeypatch):
    class _FakeCollector(SbomCollector):
        def collect(
            self, graph: ResolvedWorkspaceGraph, modules: list[ModuleModel], build_path: Path
        ) -> CollectorResult:
            return CollectorResult()

    fake_entry_point = _FakeEntryPoint(name="npm", value="does.not.matter:Ignored", _target=_FakeCollector)
    monkeypatch.setattr(registry, "entry_points", lambda group: [fake_entry_point])

    assert "npm" in registry.list_collectors()


def test_entry_point_colliding_with_a_built_in_name_is_an_error(monkeypatch):
    fake_entry_point = _FakeEntryPoint(name="image", value="does.not.matter:Ignored", _target=ImageCollector)
    monkeypatch.setattr(registry, "entry_points", lambda group: [fake_entry_point])

    with pytest.raises(CollectorNotFoundError, match="built-in SBOM collector"):
        get("image")


def test_multiple_entry_points_for_the_same_unknown_name_is_an_error(monkeypatch):
    class _PluginA(SbomCollector):
        def collect(
            self, graph: ResolvedWorkspaceGraph, modules: list[ModuleModel], build_path: Path
        ) -> CollectorResult:
            return CollectorResult()

    class _PluginB(SbomCollector):
        def collect(
            self, graph: ResolvedWorkspaceGraph, modules: list[ModuleModel], build_path: Path
        ) -> CollectorResult:
            return CollectorResult()

    entry_a = _FakeEntryPoint(name="npm", value="a:A", _target=_PluginA)
    entry_b = _FakeEntryPoint(name="npm", value="b:B", _target=_PluginB)
    monkeypatch.setattr(registry, "entry_points", lambda group: [entry_a, entry_b])

    with pytest.raises(CollectorNotFoundError, match="multiple installed plugins"):
        get("npm")
