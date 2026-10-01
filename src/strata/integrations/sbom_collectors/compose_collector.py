#!/usr/bin/env python3
"""Collect container image components from rendered `docker-compose.yml`
files (docs/design/sbom-generation.md Phase 1) — v2 equivalent of v1's
`ComposeImageCollector`.
"""

from pathlib import Path

import yaml

from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.integrations.sbom_collectors.base import CollectorResult, SbomCollector
from strata.models.module_model import ModuleModel
from strata.models.sbom_model import SbomComponentModel
from strata.utils.diagnostics import Diagnostic, Severity
from strata.utils.sbom_utils import image_to_purl, is_floating_tag, parse_image_ref

_COMPOSE_FILENAMES = ("docker-compose.yml", "docker-compose.yaml")
_FLOATING_PROPERTY = "strata:tag-stability"
_FLOATING_VALUE = "floating"


class ComposeCollector(SbomCollector):
    """Collect container image components from `docker-compose.yml`/`.yaml`
    service definitions rendered under `build_path`.

    Scans `build_path` recursively — these files are staged there by
    `ComposeIntegration.prepare_namespace()` during `strata build run`, same
    as v1's `ComposeDeployer`. Extracts `services.<name>.image`; a service
    with only a `build:` directive (no `image:`) is silently skipped.
    Deduplicates by PURL.
    """

    def collect(self, graph: ResolvedWorkspaceGraph, modules: list[ModuleModel], build_path: Path) -> CollectorResult:
        components: list[SbomComponentModel] = []
        diagnostics: list[Diagnostic] = []

        if not build_path.exists():
            return CollectorResult()

        seen_purls: set[str] = set()
        compose_files = [f for name in _COMPOSE_FILENAMES for f in sorted(build_path.rglob(name))]

        for compose_file in compose_files:
            try:
                with compose_file.open("r", encoding="utf-8") as fh:
                    data = yaml.safe_load(fh)
            except Exception as exc:
                diagnostics.append(
                    Diagnostic(
                        severity=Severity.WARNING,
                        message=f"failed to parse {compose_file.name}: {exc}",
                        source="sbom:compose",
                    )
                )
                continue

            self._extract_images(data, components, diagnostics, seen_purls)

        return CollectorResult(components=components, diagnostics=diagnostics)

    @staticmethod
    def _extract_images(
        data: object,
        components: list[SbomComponentModel],
        diagnostics: list[Diagnostic],
        seen_purls: set[str],
    ) -> None:
        if not isinstance(data, dict):
            return
        services = data.get("services")
        if not isinstance(services, dict):
            return

        for service_name, service_cfg in services.items():
            if not isinstance(service_cfg, dict):
                continue
            image = service_cfg.get("image")
            if not image:
                continue

            purl = image_to_purl(image)
            if purl in seen_purls:
                continue
            seen_purls.add(purl)

            _, tag, _ = parse_image_ref(image)
            properties: dict[str, str] = {}
            if is_floating_tag(tag):
                properties[_FLOATING_PROPERTY] = _FLOATING_VALUE
                diagnostics.append(
                    Diagnostic(
                        severity=Severity.WARNING,
                        message=f"floating image tag detected for service '{service_name}': {image}",
                        source="sbom:compose",
                    )
                )

            components.append(
                SbomComponentModel(
                    component_type="container",
                    name=str(service_name),
                    version=tag,
                    purl=purl,
                    properties=properties,
                    source_collector="compose",
                )
            )
