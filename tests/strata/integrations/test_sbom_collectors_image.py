#!/usr/bin/env python3
"""Tests for `ImageCollector` (docs/design/sbom-generation.md Phase 1)."""

from pathlib import Path

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
    assert comp.properties == {}
    assert result.diagnostics == []


def test_collect_flags_a_floating_tag_as_a_warning():
    module = _module("app", ModuleServiceModel(name="app", image="acme/app:latest"))
    result = ImageCollector().collect(_graph(), [module], Path("/does/not/matter"))

    assert result.components[0].properties == {"strata:tag-stability": "floating"}
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].severity is Severity.WARNING


def test_collect_skips_a_service_using_artifact_instead_of_image():
    module = _module("app", ModuleServiceModel(name="app", artifact="dspapi_container"))
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
