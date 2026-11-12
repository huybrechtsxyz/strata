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


@pytest.mark.parametrize(
    ("tag", "source", "version"),
    [(None, "pin", None), ("latest", "pin", "latest"), ("latest", "declared", "latest")],
)
def test_an_artifact_with_a_digest_records_it_as_a_hash_and_is_not_floating(tag, source, version):
    """`image_tag` is the tag part and `image_digest` the digest, as `effective_images()` splits `TAG@sha256:...`.
    A digest never moves, so not even `latest@sha256:...` is floating."""
    from strata.integrations.resolved_context import EffectiveImage

    graph = _graph()
    graph.images.append(
        EffectiveImage(
            origin="artifact",
            name="api",
            image_name="ghcr.io/acme/api",
            image_tag=tag,
            image_digest=f"sha256:{_HEX}",
            image_ref=f"ghcr.io/acme/api{f':{tag}' if tag else ''}@sha256:{_HEX}",
            tag_source=source,
            artifact="api",
            via=("variable:API_IMAGE",),
            pin_version="prd" if source == "pin" else None,
        )
    )

    result = ImageCollector().collect(graph, [], Path("/does/not/matter"))

    [component] = result.components
    assert (component.version, component.hashes) == (version, {"SHA-256": _HEX})
    assert component.purl == f"pkg:docker/acme/api@sha256:{_HEX}?repository_url=ghcr.io", "the digest, not the tag"
    assert "strata:tag-stability" not in component.properties
    assert result.diagnostics == []


def test_a_service_image_with_a_floating_tag_and_a_digest_is_not_floating():
    module = _module("app", ModuleServiceModel(name="app", image=f"acme/app:latest@sha256:{_HEX}"))
    result = ImageCollector().collect(_graph(), [module], Path("/does/not/matter"))
    assert "strata:tag-stability" not in result.components[0].properties
    assert result.diagnostics == []


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


# The declared-only marker (docs/work/helm-image-delivery.md, the SBOM side, part 1).


def _helm_row(image_ref: str, *, delivered: bool, module: str = "authentik"):
    from strata.integrations.resolved_context import EffectiveImage

    image_name, _, tag = image_ref.rpartition(":")
    return EffectiveImage(
        origin="module-service",
        name="server",
        image_name=image_name,
        image_tag=tag,
        image_ref=image_ref,
        tag_source="declared",
        module=module,
        via=(f"service:{module}/server",),
        delivered=delivered,
        delivery=None if delivered else "helm module: the chart decides",
    )


def test_an_image_only_a_helm_module_declares_is_marked_not_delivered():
    graph = _graph()
    graph.images.append(_helm_row("ghcr.io/goauthentik/server:2026.5.2", delivered=False))
    module = _module("authentik", ModuleServiceModel(name="server", image="ghcr.io/goauthentik/server:2026.5.2"))

    [component] = ImageCollector().collect(graph, [module], Path("/does/not/matter")).components

    assert component.properties["strata:image-delivered"] == "false"
    assert component.properties["strata:image-delivery"] == "helm module: the chart decides"


def test_an_image_a_compose_module_also_runs_is_not_marked():
    graph = _graph()
    graph.images.append(_helm_row("redis:7.2", delivered=False))
    graph.images.append(_helm_row("redis:7.2", delivered=True, module="cache"))
    module = _module("cache", ModuleServiceModel(name="server", image="redis:7.2"))

    [component] = ImageCollector().collect(graph, [module], Path("/does/not/matter")).components

    assert "strata:image-delivered" not in component.properties


def test_a_delivered_image_or_one_without_a_row_is_not_marked():
    graph = _graph()
    graph.images.append(_helm_row("redis:7.2", delivered=True))
    modules = [
        _module("cache", ModuleServiceModel(name="redis", image="redis:7.2")),
        _module("web", ModuleServiceModel(name="nginx", image="nginx:1.27")),
    ]

    components = ImageCollector().collect(graph, modules, Path("/does/not/matter")).components

    assert all("strata:image-delivered" not in c.properties for c in components)
