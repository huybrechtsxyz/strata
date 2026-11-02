#!/usr/bin/env python3
"""`SbomCollector` ABC and its result type (docs/design/sbom-generation.md
Phase 1) — the contract every collector implements, kept deliberately
narrower than v1's `BaseSbomCollector`.

v1's `work_path`/`deployment_build_path` collapse to v2's single
`build_path` (one deployment's entire rendered build output, same shape
either way — v2 never has a `platform.json` or a separate
per-deployment subdirectory to distinguish them). There is no separate
`get_warnings()` drain method either: `Diagnostics` is already v2's one
shared findings channel (`docs/design/sbom-generation.md`'s own
"Collector warnings" section), so a collector returns its warnings as part
of the same `CollectorResult` it returns its components in.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.models.module_model import ModuleModel
from strata.models.sbom_model import SbomComponentModel
from strata.utils.diagnostics import Diagnostic


@dataclass
class CollectorResult:
    """What one collector's `collect()` call found."""

    components: list[SbomComponentModel] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)


class SbomCollector(ABC):
    """One pluggable SBOM component-type collector.

    Registered by name in `strata.integrations.sbom_collectors.registry`
    (built-in) or discovered via the `strata.sbom_collectors` entry-point
    group (third-party) — see `registry.py` and docs/design/
    sbom-generation.md's Extensibility section for how a new technology
    source is added without touching this class or any existing collector.
    """

    @abstractmethod
    def collect(self, graph: ResolvedWorkspaceGraph, modules: list[ModuleModel], build_path: Path) -> CollectorResult:
        """Extract components reachable from this build.

        Args:
            graph: The deployment's already-resolved workspace graph
                (`build_resolved_workspace_graph()`) — `graph.namespaces`
                is every namespace the workspace references, already keyed
                by name.
            modules: Every module every namespace references, already
                resolved to a real `ModuleModel` by the caller
                (`sbom_controller.write_sbom()`, via
                `workload_controller.resolve_module()`) — a collector never
                does its own `DocumentIndex` lookup (layering, ADR-0003:
                `integrations` must not import `controllers`).
            build_path: The build output root for this `build run`
                invocation — already-rendered artifacts (compose files,
                materialised Helm charts, synced Terraform roots) live
                under here, scannable directly via `rglob()`.

        Returns:
            A `CollectorResult` — empty when nothing applicable is found,
            never `None`. A parse/floating-tag warning is a `Diagnostic`
            (`Severity.WARNING`), never a raised exception — one bad file
            or image reference should not abort the whole SBOM.
        """
        raise NotImplementedError
