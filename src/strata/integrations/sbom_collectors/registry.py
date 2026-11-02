#!/usr/bin/env python3
"""One lean, lazily-loaded registry: collector name -> `SbomCollector` class
(docs/design/sbom-generation.md's Extensibility section).

Structurally copied from `strata.integrations.registry` (ADR-0021 D10) —
the same shaped problem (a built-in set plus third-party extensibility)
gets the same answer, rather than inventing a second plugin mechanism:
a lean `_KNOWN` dict of built-ins, lazily imported, plus discovery via
Python's standard `importlib.metadata.entry_points()` so a third-party
package registers a custom collector through its own `pyproject.toml`
(`[project.entry-points."strata.sbom_collectors"]`) with zero changes to
this repo.
"""

from importlib import import_module
from importlib.metadata import entry_points
from typing import cast

from strata.integrations.sbom_collectors.base import SbomCollector

#: Entry point group a third-party package registers a custom collector
#: class under — e.g. `[project.entry-points."strata.sbom_collectors"]`.
ENTRY_POINT_GROUP = "strata.sbom_collectors"

#: Built-in collectors: name -> (module path, class name). Imported lazily,
#: only when `get()`/`list_collectors()` actually needs the class.
_KNOWN: dict[str, tuple[str, str]] = {
    "image": ("strata.integrations.sbom_collectors.image_collector", "ImageCollector"),
    "compose": ("strata.integrations.sbom_collectors.compose_collector", "ComposeCollector"),
    "helm": ("strata.integrations.sbom_collectors.helm_collector", "HelmCollector"),
    "terraform": ("strata.integrations.sbom_collectors.terraform_collector", "TerraformCollector"),
    # ansible/deps(lockfiles) as each gets a real v2 consumer/trigger
    # (docs/design/sbom-generation.md Remaining Work).
}


class CollectorNotFoundError(Exception):
    """No class is registered for a requested collector name — neither built-in nor an installed entry point."""


def get(name: str) -> SbomCollector:
    """Construct the `SbomCollector` registered for `name`.

    Raises:
        CollectorNotFoundError: `name` is registered neither as a built-in
            nor as an installed entry point, or collides between the two.
    """
    collector_class = _resolve_class(name)
    return collector_class()


def list_collectors() -> list[str]:
    """Return every registered collector name — every built-in, plus every
    installed `strata.sbom_collectors` entry point.

    `write_sbom()` iterates this instead of a hardcoded list, so a new
    built-in collector is a pure addition (one `_KNOWN` entry), never an
    edit to the orchestration code.
    """
    names = set(_KNOWN)
    names.update(ep.name for ep in entry_points(group=ENTRY_POINT_GROUP))
    return sorted(names)


def _resolve_class(name: str) -> type[SbomCollector]:
    plugins = [ep for ep in entry_points(group=ENTRY_POINT_GROUP) if ep.name == name]

    if name in _KNOWN:
        if plugins:
            raise CollectorNotFoundError(
                f"'{name}' is a built-in SBOM collector; an installed plugin may not reuse a built-in name."
            )
        module_path, class_name = _KNOWN[name]
        module = import_module(module_path)
        return cast(type[SbomCollector], getattr(module, class_name))

    if plugins:
        if len(plugins) > 1:
            raise CollectorNotFoundError(
                f"multiple installed plugins register SBOM collector '{name}': {[p.value for p in plugins]}."
            )
        return cast(type[SbomCollector], plugins[0].load())

    raise CollectorNotFoundError(f"no SBOM collector registered for name '{name}'.")
