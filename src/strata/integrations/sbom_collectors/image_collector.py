#!/usr/bin/env python3
"""Collect container image components from resolved module services
(docs/design/sbom-generation.md Phase 1) — v2 equivalent of v1's
`ContainerImageCollector`, reading `ModuleModel.spec.services[].image`
(v1 read the same field off its `platform.spec.modules[].services[]`
snapshot — same data, different host object).
"""

from pathlib import Path

from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.integrations.sbom_collectors.base import CollectorResult, SbomCollector
from strata.models.module_model import ModuleModel
from strata.models.sbom_model import SbomComponentModel
from strata.utils.diagnostics import Diagnostic, Severity
from strata.utils.sbom_utils import image_to_purl, is_floating_tag, parse_image_ref

_FLOATING_PROPERTY = "strata:tag-stability"
_FLOATING_VALUE = "floating"


class ImageCollector(SbomCollector):
    """Collects container image components from every resolved module's
    `spec.services[].image`.

    Deduplicates by PURL. A floating tag (`latest`, `main`, `dev`, …) gets a
    `strata:tag-stability=floating` property plus a `Severity.WARNING`
    diagnostic — informational, never fails the build.
    """

    def collect(self, graph: ResolvedWorkspaceGraph, modules: list[ModuleModel], build_path: Path) -> CollectorResult:
        components: list[SbomComponentModel] = []
        diagnostics: list[Diagnostic] = []
        seen_purls: set[str] = set()

        for module in modules:
            for service in module.spec.services or []:
                if not service.image:
                    continue

                purl = image_to_purl(service.image)
                if purl in seen_purls:
                    continue
                seen_purls.add(purl)

                _, tag, _ = parse_image_ref(service.image)
                properties: dict[str, str] = {}
                if is_floating_tag(tag):
                    properties[_FLOATING_PROPERTY] = _FLOATING_VALUE
                    diagnostics.append(
                        Diagnostic(
                            severity=Severity.WARNING,
                            message=f"floating image tag detected for service '{service.name}': {service.image}",
                            source="sbom:image",
                        )
                    )

                components.append(
                    SbomComponentModel(
                        component_type="container",
                        name=str(service.name),
                        version=tag,
                        purl=purl,
                        properties=properties,
                        source_collector="image",
                    )
                )

        return CollectorResult(components=components, diagnostics=diagnostics)
