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
from strata.utils.sbom_utils import (
    IMAGE_DELIVERED_PROPERTY,
    IMAGE_DELIVERY_PROPERTY,
    USED_BY_PROPERTY,
    image_digest_hash,
    image_to_purl,
    is_floating_tag,
    is_mutable_alias_tag,
    parse_image_ref,
)

_FLOATING_PROPERTY = "strata:tag-stability"
_FLOATING_VALUE = "floating"
_USED_BY_PROPERTY = USED_BY_PROPERTY
#: The `kind: version` document whose pin chose the tag, from the deployment's effective images.
_PINNED_BY_PROPERTY = "strata:pinned-by"


class ImageCollector(SbomCollector):
    """Collects container image components from every resolved module's
    `spec.services[].image` and from the deployment's artifact images
    (`graph.images`, `origin == "artifact"`): `store: artifact` variables and module
    services that name an `artifact`, which have no `image` of their own.

    Deduplicates by PURL, and lists every use of an image in a `strata:used-by`
    property (`service:MODULE/SERVICE`, `variable:KEY`, `token:KIND/NAME`, as `strata deploy images`
    names them). A floating tag (`latest`, `main`, `dev`, ...) gets a
    `strata:tag-stability=floating` property plus a `Severity.WARNING`
    diagnostic - informational, never fails the build. An artifact whose tag a
    `kind: version` pin chose is only floating when it is a known mutable alias:
    a pin is a deliberate choice, even when the tag is a build number rather
    than a semantic version.
    """

    def collect(self, graph: ResolvedWorkspaceGraph, modules: list[ModuleModel], build_path: Path) -> CollectorResult:
        components: list[SbomComponentModel] = []
        diagnostics: list[Diagnostic] = []
        seen_purls: set[str] = set()
        uses: dict[str, list[str]] = {}

        def _add(name: str, image: str, floating: bool, used_by: tuple[str, ...]) -> None:
            purl = image_to_purl(image)
            known = uses.setdefault(purl, [])
            for use in used_by:
                if use not in known:
                    known.append(use)
            if purl in seen_purls:
                return
            seen_purls.add(purl)
            _, tag, _ = parse_image_ref(image)
            digest_hash = image_digest_hash(image)
            properties: dict[str, str] = {}
            if floating:
                properties[_FLOATING_PROPERTY] = _FLOATING_VALUE
                diagnostics.append(
                    Diagnostic(
                        severity=Severity.WARNING,
                        message=f"floating image tag detected for service '{name}': {image}",
                        source="sbom:image",
                    )
                )
            components.append(
                SbomComponentModel(
                    component_type="container",
                    name=name,
                    version=tag,
                    purl=purl,
                    properties=properties,
                    hashes=dict([digest_hash]) if digest_hash else {},
                    source_collector="image",
                )
            )

        for module in modules:
            for service in module.spec.services or []:
                if not service.image:
                    continue
                _, tag, digest = parse_image_ref(service.image)
                _add(
                    str(service.name),
                    service.image,
                    digest is None and is_floating_tag(tag),
                    (f"service:{module.meta.name}/{service.name}",),
                )

        for image in graph.images:
            if image.origin != "artifact":
                continue
            # A digest never moves, whatever tag travels with it.
            floating = image.image_digest is None and (
                is_mutable_alias_tag(image.image_tag) if image.tag_source == "pin" else is_floating_tag(image.image_tag)
            )
            _add(image.name, image.image_ref, floating, image.via)

        components = [
            c.model_copy(update={"properties": {**c.properties, _USED_BY_PROPERTY: ", ".join(uses[c.purl])}})
            if uses.get(c.purl)
            else c
            for c in components
        ]
        pinned_by = {
            image_to_purl(image.image_ref): image.pin_version
            for image in reversed(graph.images)
            if image.tag_source == "pin" and image.pin_version
        }
        components = [
            c.model_copy(update={"properties": {**c.properties, _PINNED_BY_PROPERTY: pinned_by[c.purl]}})
            if c.purl in pinned_by
            else c
            for c in components
        ]
        not_delivered = _not_delivered(graph)
        components = [
            c.model_copy(
                update={
                    "properties": {
                        **c.properties,
                        IMAGE_DELIVERED_PROPERTY: "false",
                        IMAGE_DELIVERY_PROPERTY: not_delivered[c.purl],
                    }
                }
            )
            if c.purl in not_delivered
            else c
            for c in components
        ]
        return CollectorResult(components=components, diagnostics=diagnostics)


def _not_delivered(graph: ResolvedWorkspaceGraph) -> dict[str, str]:
    """Why each image strata does not deliver is not delivered, by purl (docs/work/helm-image-delivery.md, the
    SBOM side). An image is delivered when any of its uses delivers it, so one a Helm module declares and a
    Compose module runs is not listed. An image with no `graph.images` row is taken as delivered."""
    delivered = {image_to_purl(image.image_ref) for image in graph.images if image.delivered}
    reasons: dict[str, str] = {}
    for image in graph.images:
        purl = image_to_purl(image.image_ref)
        if not image.delivered and purl not in delivered:
            reasons.setdefault(purl, image.delivery or "strata does not deliver it")
    return reasons
