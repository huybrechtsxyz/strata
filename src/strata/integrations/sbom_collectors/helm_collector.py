#!/usr/bin/env python3
"""Collect Helm chart components — both from resolved module sources and
from rendered `Chart.yaml` files (docs/design/sbom-generation.md Phase 1) —
v2 equivalent of v1's `HelmChartCollector` + `HelmChartFileCollector`
(merged into one collector here; v1 kept them as two classes sharing one
`get_collector_name()` purely for output grouping — this design's registry
keys collectors 1:1, so the same "model + file" pairing lives in one file
instead, components still share `source_collector="helm"`).
"""

from pathlib import Path

import yaml

from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.integrations.sbom_collectors.base import CollectorResult, SbomCollector
from strata.models.module_model import ModuleModel
from strata.models.sbom_model import SbomComponentModel
from strata.utils.diagnostics import Diagnostic, Severity
from strata.utils.sbom_utils import helm_chart_to_purl

_SKIP_DIRS = frozenset({"node_modules", ".venv", "venv", "dist", "build", ".git", "__pycache__"})


class HelmCollector(SbomCollector):
    """Collects Helm chart components two ways:

    1. **Declarative** — every resolved module whose `spec.source.chart_name`
       is set (`SourceModel`'s chart-based form). Mirrors v1's
       `HelmChartCollector`, reading `ModuleModel.spec.source` instead of a
       provisioner's source (v2 models Helm as a workload `kind: module`,
       not a provisioner).
    2. **File-based** — every `Chart.yaml` rendered under `build_path`
       (materialised there by `sync_module_source()`), plus each chart's
       own `dependencies[]`. Mirrors v1's `HelmChartFileCollector`.

    Both are deduplicated together by PURL.
    """

    def collect(self, graph: ResolvedWorkspaceGraph, modules: list[ModuleModel], build_path: Path) -> CollectorResult:
        components: list[SbomComponentModel] = []
        diagnostics: list[Diagnostic] = []
        seen_purls: set[str] = set()

        for module in modules:
            source = module.spec.source
            if source.chart_name is None:
                continue
            purl = helm_chart_to_purl(source.chart_name, source.chart_version, source.chart_repository)
            if purl in seen_purls:
                continue
            seen_purls.add(purl)
            components.append(
                SbomComponentModel(
                    component_type="library",
                    name=source.chart_name,
                    version=source.chart_version,
                    purl=purl,
                    properties={},
                    source_collector="helm",
                )
            )

        if build_path.exists():
            for chart_file in self._find_chart_files(build_path):
                self._collect_chart_file(chart_file, components, diagnostics, seen_purls)

        return CollectorResult(components=components, diagnostics=diagnostics)

    @staticmethod
    def _find_chart_files(root: Path) -> list[Path]:
        """`root.rglob("Chart.yaml")`, pruning anything under a `_SKIP_DIRS`
        directory *encountered during the walk* (vendored/dependency trees a
        chart's own `charts/` subfolder might contain).

        Checks only the path components *relative to `root`*, never the
        absolute path's own parts — `root` itself is `build_path`, which is
        routinely named literally `build` (`layout.build_dir()`'s own
        convention); matching against the full absolute path would skip
        every chart under it, since `"build"` is itself one of the
        directories this is meant to prune.
        """
        found: list[Path] = []
        for path in sorted(root.rglob("Chart.yaml")):
            relative_dirs = path.relative_to(root).parts[:-1]
            if _SKIP_DIRS.isdisjoint(relative_dirs):
                found.append(path)
        return found

    @staticmethod
    def _collect_chart_file(
        chart_file: Path,
        components: list[SbomComponentModel],
        diagnostics: list[Diagnostic],
        seen_purls: set[str],
    ) -> None:
        try:
            with chart_file.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except Exception as exc:
            diagnostics.append(
                Diagnostic(
                    severity=Severity.WARNING,
                    message=f"failed to parse {chart_file}: {exc}",
                    source="sbom:helm",
                )
            )
            return

        if not isinstance(data, dict):
            return

        name = data.get("name")
        version = data.get("version")
        if name:
            purl = helm_chart_to_purl(name, version)
            if purl not in seen_purls:
                seen_purls.add(purl)
                components.append(
                    SbomComponentModel(
                        component_type="library",
                        name=str(name),
                        version=str(version) if version else None,
                        purl=purl,
                        properties={},
                        source_collector="helm",
                    )
                )

        for dependency in data.get("dependencies") or []:
            if not isinstance(dependency, dict):
                continue
            dep_name = dependency.get("name")
            if not dep_name:
                continue
            dep_version = dependency.get("version")
            dep_repository = dependency.get("repository")
            purl = helm_chart_to_purl(dep_name, dep_version, dep_repository)
            if purl in seen_purls:
                continue
            seen_purls.add(purl)
            components.append(
                SbomComponentModel(
                    component_type="library",
                    name=str(dep_name),
                    version=str(dep_version) if dep_version else None,
                    purl=purl,
                    properties={},
                    source_collector="helm",
                )
            )
