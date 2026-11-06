#!/usr/bin/env python3
"""Tests for `ImageCollector` (docs/design/sbom-generation.md Phase 1)."""

from pathlib import Path

import pytest

from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.integrations.sbom_collectors.image_collector import ImageCollector
from strata.models.common_models import SourceModel
from strata.models.module_model import ModuleMetaModel, ModuleModel, ModuleServiceModel, ModuleSpecModel
from strata.models.provisioning_model import ProvisionerModel
from strata.models.workspace_model import WorkspaceMetaModel, WorkspaceModel, WorkspaceSpecModel
from strata.utils.diagnostics import Severity


def _graph() -> ResolvedWorkspaceGraph:
    workspace = WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(
            providers=["p"],
            provisioners=[ProvisionerModel(name="prov", tool="terraform", source=SourceModel(source_path="infra"))],
        ),
    )
    return ResolvedWorkspaceGraph(workspace=workspace)


def _module(name: str, *services: ModuleServiceModel) -> ModuleModel:
    return ModuleModel(
        meta=ModuleMetaModel(name=name),
        spec=ModuleSpecModel(
            source=SourceModel(source_path=f"services/{name}"),
            type="compose",
            services=list(services),
            default_labels={},
        ),
    )


def test_collect_returns_empty_for_no_modules():
    result = ImageCollector().collect(_graph(), [], Path("/does/not/matter"))
    assert result.components == []
    assert result.diagnostics == []


def test_collect_extracts_a_pinned_image():
    module = _module("redis", ModuleServiceModel(name="redis", image="redis:7.2"))
    result = ImageCollector().collect(_graph(), [module], Path("/does/not/matter"))

    assert len(result.components) == 1
    comp = result.components[0]
    assert comp.name == "redis"
    assert comp.version == "7.2"
    assert comp.purl == "pkg:docker/redis@7.2"
    assert comp.source_collector == "image"
    assert comp.properties == {"strata:used-by": "service:redis/redis"}
    assert result.diagnostics == []


def test_collect_flags_a_floating_tag_as_a_warning():
    module = _module("app", ModuleServiceModel(name="app", image="acme/app:latest"))
    result = ImageCollector().collect(_graph(), [module], Path("/does/not/matter"))

    assert result.components[0].properties == {
        "strata:tag-stability": "floating",
        "strata:used-by": "service:app/app",
    }
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].severity is Severity.WARNING


_HEX = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"


@pytest.mark.parametrize(
    ("image", "version"), [(f"ghcr.io/acme/api:1.4.2@sha256:{_HEX}", "1.4.2"), (f"postgres@sha256:{_HEX}", None)]
)
def test_an_image_with_a_digest_records_it_as_a_hash_and_is_not_floating(image, version):
    module = _module("app", ModuleServiceModel(name="app", image=image))

    result = ImageCollector().collect(_graph(), [module], Path("/does/not/matter"))

    [component] = result.components
    assert (component.version, component.hashes) == (version, {"SHA-256": _HEX})
    assert "strata:tag-stability" not in component.properties
    assert result.diagnostics == []


def test_an_image_without_a_digest_has_no_hash():
    module = _module("redis", ModuleServiceModel(name="redis", image="redis:7.2"))
    [component] = ImageCollector().collect(_graph(), [module], Path("/does/not/matter")).components
    assert component.hashes == {}


def test_an_artifact_pinned_to_a_digest_records_it_as_a_hash():
    from strata.integrations.resolved_context import EffectiveImage

    graph = _graph()
    graph.images.append(
        EffectiveImage(
            origin="artifact",
            name="api",
            image_name="ghcr.io/acme/api",
            image_tag=f"sha256:{_HEX}",
            image_ref=f"ghcr.io/acme/api@sha256:{_HEX}",
            tag_source="pin",
            artifact="api",
            via=("variable:API_IMAGE",),
            pin_version="prd",
        )
    )

    [component] = ImageCollector().collect(graph, [], Path("/does/not/matter")).components

    assert component.hashes == {"SHA-256": _HEX}


def test_collect_skips_a_service_using_artifact_instead_of_image():
    module = _module("app", ModuleServiceModel(name="app", artifact="api_container"))
    result = ImageCollector().collect(_graph(), [module], Path("/does/not/matter"))
    assert result.components == []


def test_collect_deduplicates_by_purl():
    module = _module(
        "app",
        ModuleServiceModel(name="a", image="redis:7.2"),
        ModuleServiceModel(name="b", image="redis:7.2"),
    )
    result = ImageCollector().collect(_graph(), [module], Path("/does/not/matter"))
    assert len(result.components) == 1


def test_an_image_lists_every_service_that_uses_it():
    cache = _module("cache", ModuleServiceModel(name="redis", image="redis:7.2"))
    app = _module("app", ModuleServiceModel(name="queue", image="redis:7.2"))

    [component] = ImageCollector().collect(_graph(), [cache, app], Path("/does/not/matter")).components

    assert component.properties == {"strata:used-by": "service:cache/redis, service:app/queue"}


def test_a_module_used_in_two_namespaces_is_listed_once():
    cache = _module("cache", ModuleServiceModel(name="redis", image="redis:7.2"))

    [component] = ImageCollector().collect(_graph(), [cache, cache], Path("/does/not/matter")).components

    assert component.properties == {"strata:used-by": "service:cache/redis"}


def test_an_artifact_lists_its_uses_from_the_effective_images():
    from strata.integrations.resolved_context import EffectiveImage

    graph = _graph()
    graph.images.append(
        EffectiveImage(
            origin="artifact",
            name="api",
            image_name="ghcr.io/acme/api",
            image_tag="1.4.2",
            image_ref="ghcr.io/acme/api:1.4.2",
            tag_source="declared",
            artifact="api",
            via=("variable:API_IMAGE", "service:web/api"),
        )
    )

    [component] = ImageCollector().collect(graph, [], Path("/does/not/matter")).components

    assert component.properties == {"strata:used-by": "variable:API_IMAGE, service:web/api"}


def _row(origin: str, name: str, image_ref: str, *, pin: str | None, via: tuple[str, ...]):
    from strata.integrations.resolved_context import EffectiveImage

    image_name, _, tag = image_ref.rpartition(":")
    return EffectiveImage(
        origin=origin,
        name=name,
        image_name=image_name,
        image_tag=tag,
        image_ref=image_ref,
        tag_source="pin" if pin else "declared",
        artifact=name if origin == "artifact" else None,
        via=via,
        pin_version=pin,
    )


def test_an_image_a_version_document_pinned_names_that_document():
    graph = _graph()
    graph.images.extend(
        [
            _row("artifact", "api", "ghcr.io/acme/api:1.4.2", pin="prd", via=("variable:API_IMAGE",)),
            _row("module-service", "redis", "redis:7.4", pin="prd", via=("service:cache/redis",)),
            _row("module-service", "db", "postgres:16", pin=None, via=("service:cache/db",)),
        ]
    )
    cache = _module(
        "cache",
        ModuleServiceModel(name="redis", image="redis:7.4"),
        ModuleServiceModel(name="db", image="postgres:16"),
    )

    result = ImageCollector().collect(graph, [cache], Path("/does/not/matter"))

    pinned = {c.name: c.properties.get("strata:pinned-by") for c in result.components}
    assert pinned == {"redis": "prd", "db": None, "api": "prd"}
