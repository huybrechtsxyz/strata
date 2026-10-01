#!/usr/bin/env python3
"""Tests for `sbom_controller.write_sbom()` (docs/design/sbom-generation.md
Phase 1)."""

import json
from pathlib import Path

import pytest

from strata.controllers.sbom_controller import write_sbom
from strata.controllers.solution_controller import DocumentIndex, DocumentRef, IndexEntry
from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.models.common_models import ModuleReferenceModel, PlatformKind, SourceModel
from strata.models.module_model import ModuleMetaModel, ModuleModel, ModuleServiceModel, ModuleSpecModel
from strata.models.namespace_model import NamespaceMetaModel, NamespaceModel, NamespaceSpecModel
from strata.models.provisioning_model import ProvisionerModel
from strata.models.workspace_model import WorkspaceMetaModel, WorkspaceModel, WorkspaceSpecModel


def _module(name: str, *, services: list[ModuleServiceModel] | None = None) -> ModuleModel:
    return ModuleModel(
        meta=ModuleMetaModel(name=name),
        spec=ModuleSpecModel(
            source=SourceModel(source_path=f"services/{name}"),
            type="compose",
            services=services if services is not None else [ModuleServiceModel(name=name, image=f"{name}:1.0")],
            default_labels={},
        ),
    )


def _index(*modules: ModuleModel) -> DocumentIndex:
    index = DocumentIndex()
    for model in modules:
        ref = DocumentRef(kind=PlatformKind.MODULE, name=model.meta.name)
        index.add(IndexEntry(ref=ref, model=model, source=Path(f"{model.meta.name}.yaml")))
    return index


def _namespace(*refs: ModuleReferenceModel) -> NamespaceModel:
    return NamespaceModel(
        meta=NamespaceMetaModel(name="apps"),
        spec=NamespaceSpecModel(type="dedicated", default_labels={}, modules=list(refs)),
    )


def _graph(*namespaces: NamespaceModel) -> ResolvedWorkspaceGraph:
    workspace = WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(
            providers=["p"],
            provisioners=[ProvisionerModel(name="prov", tool="terraform", source=SourceModel(source_path="infra"))],
        ),
    )
    return ResolvedWorkspaceGraph(
        workspace=workspace, namespaces={namespace.meta.name: namespace for namespace in namespaces}
    )


def test_write_sbom_writes_a_valid_cyclonedx_file(tmp_path: Path):
    module = _module("redis")
    index = _index(module)
    namespace = _namespace(ModuleReferenceModel(name="redis", module="redis"))
    graph = _graph(namespace)

    diagnostics = write_sbom(tmp_path, graph, index)

    assert diagnostics.ok
    sbom_path = tmp_path / "sbom.json"
    assert sbom_path.is_file()

    data = json.loads(sbom_path.read_text(encoding="utf-8"))
    assert data["bomFormat"] == "CycloneDX"
    assert data["specVersion"] == "1.6"
    names = {c["name"] for c in data["components"]}
    assert "redis" in names


def test_write_sbom_skips_a_disabled_module_reference(tmp_path: Path):
    module = _module("redis")
    index = _index(module)
    namespace = _namespace(ModuleReferenceModel(name="redis", module="redis", enabled=False))
    graph = _graph(namespace)

    write_sbom(tmp_path, graph, index)

    data = json.loads((tmp_path / "sbom.json").read_text(encoding="utf-8"))
    assert data.get("components", []) == []


def test_write_sbom_merges_warnings_from_every_collector(tmp_path: Path):
    module = _module("app", services=[ModuleServiceModel(name="app", image="acme/app:latest")])
    index = _index(module)
    namespace = _namespace(ModuleReferenceModel(name="app", module="app"))
    graph = _graph(namespace)

    diagnostics = write_sbom(tmp_path, graph, index)

    assert diagnostics.ok  # a floating tag is a warning, never an error
    assert len(diagnostics.warnings) >= 1


def test_write_sbom_with_no_namespaces_writes_an_empty_bom(tmp_path: Path):
    graph = _graph()
    diagnostics = write_sbom(tmp_path, graph, _index())

    assert diagnostics.ok
    data = json.loads((tmp_path / "sbom.json").read_text(encoding="utf-8"))
    assert data.get("components", []) == []


def test_write_sbom_raises_when_the_bom_fails_schema_validation(tmp_path: Path, monkeypatch):
    import strata.controllers.sbom_controller as sbom_controller_module

    monkeypatch.setattr(sbom_controller_module, "_build_cyclonedx_json", lambda components: "{}")

    with pytest.raises(RuntimeError, match="failed CycloneDX schema validation"):
        write_sbom(tmp_path, _graph(), _index())
