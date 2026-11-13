#!/usr/bin/env python3
"""Installed entry points are read once per process (strata.utils.plugins)."""

import importlib.metadata

import pytest

from strata.integrations import registry as integration_registry
from strata.integrations.sbom_collectors import registry as collector_registry
from strata.utils.plugins import installed_entry_points


@pytest.fixture
def counted_scans(monkeypatch):
    """Counts the scans of installed packages, on an empty cache that is emptied again afterwards."""
    scans: list[str] = []
    real = importlib.metadata.entry_points

    def _entry_points(*, group: str):
        scans.append(group)
        return real(group=group)

    installed_entry_points.cache_clear()
    monkeypatch.setattr(importlib.metadata, "entry_points", _entry_points)
    yield scans
    installed_entry_points.cache_clear()


def test_a_group_is_scanned_once_however_often_it_is_read(counted_scans):
    first = installed_entry_points("strata.integrations")
    second = installed_entry_points("strata.integrations")

    assert first is second
    assert counted_scans == ["strata.integrations"]


def test_each_group_is_scanned_on_its_own(counted_scans):
    installed_entry_points("strata.integrations")
    installed_entry_points("strata.sbom_collectors")

    assert counted_scans == ["strata.integrations", "strata.sbom_collectors"]


def test_the_result_cannot_be_changed_by_a_caller():
    assert isinstance(installed_entry_points("strata.integrations"), tuple)


def test_repeated_lookups_in_both_registries_scan_each_group_once(counted_scans):
    for _ in range(3):
        integration_registry.get_class("terraform")
        integration_registry.known_types()
        collector_registry.list_collectors()
        collector_registry.get("terraform")

    assert sorted(counted_scans) == ["strata.integrations", "strata.sbom_collectors"]
