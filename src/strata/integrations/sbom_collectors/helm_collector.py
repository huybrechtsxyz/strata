#!/usr/bin/env python3
"""Collect Helm chart components — both from resolved module sources and
from rendered `Chart.yaml` files (docs/design/sbom-generation.md Phase 1) —
v2 equivalent of v1's `HelmChartCollector` + `HelmChartFileCollector`
(merged into one collector here; v1 kept them as two classes sharing one
`get_collector_name()` purely for output grouping — this design's registry
keys collectors 1:1, so the same "model + file" pairing lives in one file
instead, components still share `source_collector="helm"`).
"""

from collections.abc import Collection
from pathlib import Path
from typing import Any

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

    Both are deduplicated together by chart name and version, first one
    kept: a declared chart knows its repository, the `Chart.yaml` it was
    materialised to does not. A declared chart lists the modules that use it
    in a `strata:used-by` property (`module:NAME`).

    A registry chart `build run` pinned (docs/work/helm-registry-chart-checks.md) is recorded from its module's
    `meta.yaml`: the exact version, the archive's SHA-256, its `appVersion`, and every chart packaged in its
    `charts/`. One the build could not pin keeps its declared form and gets `strata:pinned: false`.

    A local chart's dependencies the build placed in its `charts/` (docs/work/helm-chart-dependencies.md) come from
    its `meta.yaml`'s `chartDependencies` at their exact versions, instead of its `Chart.yaml`'s declared ones; those
    in `chartDependenciesUnpinned` keep the declared form with `strata:pinned: false`.
    """

    def collect(self, graph: ResolvedWorkspaceGraph, modules: list[ModuleModel], build_path: Path) -> CollectorResult:
        components: list[SbomComponentModel] = []
        diagnostics: list[Diagnostic] = []
        seen: set[tuple[str, str | None]] = set()
        uses: dict[tuple[str, str | None], list[str]] = {}
        chart_pins = (graph.version.spec.pins.charts or {}) if graph.version is not None else {}
        built = self._built_registry_charts(build_path)

        for module in modules:
            source = module.spec.source
            if source.chart_name is None:
                continue
            meta = built.get((source.chart_name, source.chart_version))
            pinned = meta is not None and meta.get("chartDigest") is not None
            version = str(meta["chartVersion"]) if meta is not None and pinned else source.chart_version
            key = (source.chart_name, version)
            used = uses.setdefault(key, [])
            if f"module:{module.meta.name}" not in used:
                used.append(f"module:{module.meta.name}")
            if key in seen:
                continue
            seen.add(key)
            properties: dict[str, str] = {}
            if graph.version is not None and module.meta.name in chart_pins:
                properties["strata:pinned-by"] = graph.version.meta.name
            hashes: dict[str, str] = {}
            if meta is not None and pinned:
                if source.chart_version is not None and source.chart_version != version:
                    properties["strata:version-constraint"] = source.chart_version
                if meta.get("chartAppVersion") is not None:
                    properties["strata:app-version"] = str(meta["chartAppVersion"])
                hashes["SHA-256"] = str(meta["chartDigest"]).removeprefix("sha256:")
            elif meta is not None:
                properties["strata:pinned"] = "false"
            components.append(
                SbomComponentModel(
                    component_type="library",
                    name=source.chart_name,
                    version=version,
                    purl=helm_chart_to_purl(source.chart_name, version, source.chart_repository),
                    properties=properties,
                    hashes=hashes,
                    source_collector="helm",
                )
            )
            if meta is not None and pinned:
                self._collect_subcharts(meta, f"{source.chart_name}@{version}", components, seen)

        if build_path.exists():
            local = self._built_local_charts(build_path)
            for module_dir, meta in local.items():
                self._collect_dependencies(module_dir, meta, components, seen)
            for chart_file in self._find_chart_files(build_path):
                meta = local.get(chart_file.parent, {})
                self._collect_chart_file(
                    chart_file,
                    components,
                    diagnostics,
                    seen,
                    recorded={str(entry.get("name")) for entry in meta.get("chartDependencies") or []},
                    unpinned={str(name) for name in meta.get("chartDependenciesUnpinned") or []},
                )

        components = [
            c.model_copy(update={"properties": {**c.properties, "strata:used-by": ", ".join(uses[key])}})
            if (key := (c.name, c.version)) in uses
            else c
            for c in components
        ]
        return CollectorResult(components=components, diagnostics=diagnostics)

    @staticmethod
    def _built_registry_charts(build_path: Path) -> dict[tuple[str, str | None], dict[str, Any]]:
        """Every registry chart's `meta.yaml` in the build, keyed by chart name and the version its module declared."""
        found: dict[tuple[str, str | None], dict[str, Any]] = {}
        if not build_path.is_dir():
            return found
        for meta_file in sorted(build_path.glob("*/*/meta.yaml")):
            try:
                meta = yaml.safe_load(meta_file.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if not isinstance(meta, dict) or meta.get("chartName") is None:
                continue
            # A pinned range keeps the declaration in chartVersionConstraint; otherwise chartVersion is what was declared.
            declared = meta["chartVersionConstraint"] if "chartVersionConstraint" in meta else meta.get("chartVersion")
            found.setdefault((str(meta["chartName"]), str(declared) if declared is not None else None), meta)
        return found

    @staticmethod
    def _collect_subcharts(
        meta: dict[str, Any],
        parent: str,
        components: list[SbomComponentModel],
        seen: set[tuple[str, str | None]],
    ) -> None:
        """The charts packaged in a pinned chart's `charts/`, at their exact versions."""
        for subchart in meta.get("chartSubcharts") or []:
            if not isinstance(subchart, dict) or not subchart.get("name") or not subchart.get("version"):
                continue
            key = (str(subchart["name"]), str(subchart["version"]))
            if key in seen:
                continue
            seen.add(key)
            components.append(
                SbomComponentModel(
                    component_type="library",
                    name=key[0],
                    version=key[1],
                    purl=helm_chart_to_purl(key[0], key[1]),
                    properties={"strata:subchart-of": parent},
                    source_collector="helm",
                )
            )

    @staticmethod
    def _built_local_charts(build_path: Path) -> dict[Path, dict[str, Any]]:
        """Every local chart's `meta.yaml` in the build that records dependencies, keyed by its module directory."""
        found: dict[Path, dict[str, Any]] = {}
        for meta_file in sorted(build_path.glob("*/*/meta.yaml")):
            try:
                meta = yaml.safe_load(meta_file.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if not isinstance(meta, dict) or meta.get("chartName") is not None:
                continue
            if meta.get("chartDependencies") or meta.get("chartDependenciesUnpinned"):
                found[meta_file.parent] = meta
        return found

    @staticmethod
    def _collect_dependencies(
        module_dir: Path,
        meta: dict[str, Any],
        components: list[SbomComponentModel],
        seen: set[tuple[str, str | None]],
    ) -> None:
        """The dependencies `build run` placed in a local chart's `charts/`, at their exact versions
        (docs/work/helm-chart-dependencies.md)."""
        try:
            chart = yaml.safe_load((module_dir / "Chart.yaml").read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            chart = None
        parent = f"{chart.get('name')}@{chart.get('version')}" if isinstance(chart, dict) else module_dir.name
        for entry in meta.get("chartDependencies") or []:
            if not isinstance(entry, dict) or not entry.get("name") or not entry.get("version"):
                continue
            key = (str(entry["name"]), str(entry["version"]))
            if key in seen:
                continue
            seen.add(key)
            properties = {"strata:subchart-of": parent}
            if entry.get("versionConstraint") is not None:
                properties["strata:version-constraint"] = str(entry["versionConstraint"])
            repository = entry.get("repository")
            components.append(
                SbomComponentModel(
                    component_type="library",
                    name=key[0],
                    version=key[1],
                    purl=helm_chart_to_purl(
                        key[0], key[1], None if str(repository or "").startswith("file://") else repository
                    ),
                    properties=properties,
                    hashes=({"SHA-256": str(entry["digest"]).removeprefix("sha256:")} if entry.get("digest") else {}),
                    source_collector="helm",
                )
            )

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
        seen: set[tuple[str, str | None]],
        *,
        recorded: Collection[str] = (),
        unpinned: Collection[str] = (),
    ) -> None:
        """One `Chart.yaml` and its declared dependencies; those in `recorded` are already listed at their exact
        version from `meta.yaml`, and those in `unpinned` get `strata:pinned: false`."""
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
            key = (str(name), str(version) if version else None)
            if key not in seen:
                seen.add(key)
                components.append(
                    SbomComponentModel(
                        component_type="library",
                        name=key[0],
                        version=key[1],
                        purl=helm_chart_to_purl(key[0], key[1]),
                        properties={},
                        source_collector="helm",
                    )
                )

        for dependency in data.get("dependencies") or []:
            if not isinstance(dependency, dict):
                continue
            dep_name = dependency.get("name")
            if not dep_name or str(dep_name) in recorded:
                continue
            dep_version = dependency.get("version")
            key = (str(dep_name), str(dep_version) if dep_version else None)
            if key in seen:
                continue
            seen.add(key)
            components.append(
                SbomComponentModel(
                    component_type="library",
                    name=key[0],
                    version=key[1],
                    purl=helm_chart_to_purl(key[0], key[1], dependency.get("repository")),
                    properties={"strata:pinned": "false"} if key[0] in unpinned else {},
                    source_collector="helm",
                )
            )
