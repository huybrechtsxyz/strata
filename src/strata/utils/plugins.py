#!/usr/bin/env python3
"""Installed plugins: the entry points third-party packages register for strata (ADR-0021 D10).

`importlib.metadata.entry_points()` scans the metadata of every installed package on each call. The registries
(`strata.integrations.registry`, `strata.integrations.sbom_collectors.registry`) look a name up for every
integration and collector a build or deploy touches, so an uncached read repeated that scan dozens of times per
command. The set of installed packages does not change while strata runs, so it is read once per process.
"""

import importlib.metadata
from functools import cache


@cache
def installed_entry_points(group: str) -> tuple[importlib.metadata.EntryPoint, ...]:
    """Every installed entry point of `group`, read on the first call and reused after.

    A package installed while this process runs is seen by the next strata command.
    """
    return tuple(importlib.metadata.entry_points(group=group))
