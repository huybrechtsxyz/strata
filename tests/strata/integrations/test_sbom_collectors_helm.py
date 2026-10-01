#!/usr/bin/env python3
"""Tests for `HelmCollector` (docs/design/sbom-generation.md Phase 1)."""

from pathlib import Path

from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.integrations.sbom_collectors.helm_collector import HelmCollector
from strata.models.common_models import SourceModel
from strata.models.module_model import ModuleMetaModel, ModuleModel, ModuleSpecModel
from strata.models.provisioning_model import ProvisionerModel
from strata.models.workspace_model import WorkspaceMetaModel, WorkspaceModel, WorkspaceSpecModel


def _graph() -> ResolvedWorkspaceGraph:
    workspace = WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(
            providers=["p"],
            provisioners=[ProvisionerModel(name="prov", tool="terraform", source=SourceModel(source_path="infra"))],
        ),
    )
    return ResolvedWorkspaceGraph(workspace=workspace)


def _chart_module(name: str, *, chart_name: str | None, chart_version: str | None = None) -> ModuleModel:
    return ModuleModel(
        meta=ModuleMetaModel(name=name),
        spec=ModuleSpecModel(
            source=SourceModel(remote="goauthentik", chart_name=chart_name, chart_version=chart_version),
            type="helm",
            default_labels={},
        ),
    )


def test_collect_extracts_a_declarative_chart_reference():
    module = _chart_module("authentik", chart_name="authentik", chart_version="2024.12.0")
    result = HelmCollector().collect(_graph(), [module], Path("/does/not/matter"))

    assert len(result.components) == 1
    comp = result.components[0]
    assert comp.name == "authentik"
    assert comp.version == "2024.12.0"
    assert comp.source_collector == "helm"


def test_collect_skips_a_non_chart_module():
    module = ModuleModel(
        meta=ModuleMetaModel(name="plain"),
        spec=ModuleSpecModel(source=SourceModel(source_path="charts/plain"), type="helm", default_labels={}),
    )
    result = HelmCollector().collect(_graph(), [module], Path("/does/not/matter"))
    assert result.components == []


def test_collect_extracts_chart_yaml_and_its_dependencies(tmp_path: Path):
    (tmp_path / "Chart.yaml").write_text(
        "name: authentik\nversion: 2024.12.0\ndependencies:\n"
        "  - name: postgresql\n    version: 12.1.0\n    repository: https://charts.bitnami.com/bitnami\n",
        encoding="utf-8",
    )
    result = HelmCollector().collect(_graph(), [], tmp_path)

    names = {c.name for c in result.components}
    assert names == {"authentik", "postgresql"}
    assert all(c.source_collector == "helm" for c in result.components)


def test_collect_warns_on_an_unparseable_chart_file(tmp_path: Path):
    (tmp_path / "Chart.yaml").write_text("{ not: valid: yaml: [", encoding="utf-8")
    result = HelmCollector().collect(_graph(), [], tmp_path)

    assert result.components == []
    assert len(result.diagnostics) == 1
    assert "failed to parse" in result.diagnostics[0].message


def test_collect_finds_chart_yaml_even_when_the_scan_root_is_named_build(tmp_path: Path):
    """Regression: `_find_chart_files()` must only check path components
    *relative to* the scan root against `_SKIP_DIRS`, never the absolute
    path's own parts — `build_path` (the real scan root `write_sbom()`
    passes) is routinely named literally `build`
    (`layout.build_dir()`'s own convention), which is itself one of the
    `_SKIP_DIRS` names. Checking the absolute path would silently skip
    every chart ever found."""
    build_root = tmp_path / "build"
    (build_root / "apps" / "auth").mkdir(parents=True)
    (build_root / "apps" / "auth" / "Chart.yaml").write_text("name: authentik\nversion: 2024.12.0\n", encoding="utf-8")

    result = HelmCollector().collect(_graph(), [], build_root)

    assert [c.name for c in result.components] == ["authentik"]


def test_collect_declarative_and_file_based_results_merge_and_dedup(tmp_path: Path):
    (tmp_path / "Chart.yaml").write_text("name: authentik\nversion: 2024.12.0\n", encoding="utf-8")
    module = _chart_module("authentik", chart_name="authentik", chart_version="2024.12.0")

    result = HelmCollector().collect(_graph(), [module], tmp_path)

    assert len(result.components) == 1
    assert result.components[0].source_collector == "helm"
