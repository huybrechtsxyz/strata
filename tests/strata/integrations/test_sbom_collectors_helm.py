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


def test_a_chart_lists_every_module_that_uses_it():
    first = _chart_module("auth-a", chart_name="authentik", chart_version="2024.12.0")
    second = _chart_module("auth-b", chart_name="authentik", chart_version="2024.12.0")

    [component] = HelmCollector().collect(_graph(), [first, second], Path("/does/not/matter")).components

    assert component.properties == {"strata:used-by": "module:auth-a, module:auth-b"}


def test_a_chart_a_version_document_pinned_names_that_document():
    from dataclasses import replace

    from strata.models.version_model import VersionModel

    version = VersionModel.model_validate(
        {"meta": {"name": "prd"}, "spec": {"pins": {"charts": {"auth-a": "2025.1.0"}}}}
    )
    pinned = _chart_module("auth-a", chart_name="authentik", chart_version="2025.1.0")
    declared = _chart_module("portainer", chart_name="portainer", chart_version="1.0.0")

    result = HelmCollector().collect(replace(_graph(), version=version), [pinned, declared], Path("/does/not/matter"))

    assert {c.name: c.properties.get("strata:pinned-by") for c in result.components} == {
        "authentik": "prd",
        "portainer": None,
    }


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


def test_a_materialised_chart_does_not_duplicate_its_declared_chart(tmp_path: Path):
    """The declared chart knows its repository and its rendered `Chart.yaml` does not: one component, with it."""
    module = ModuleModel(
        meta=ModuleMetaModel(name="authentik"),
        spec=ModuleSpecModel(
            source=SourceModel(
                chart_repository="https://charts.goauthentik.io", chart_name="authentik", chart_version="2024.12.0"
            ),
            type="helm",
            default_labels={},
        ),
    )
    (tmp_path / "Chart.yaml").write_text("name: authentik\nversion: 2024.12.0\n", encoding="utf-8")

    result = HelmCollector().collect(_graph(), [module], tmp_path)

    assert [c.purl for c in result.components] == [
        "pkg:helm/authentik@2024.12.0?repository_url=https://charts.goauthentik.io"
    ]


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


# ---------------------------------------------------------------------------
# Registry charts pinned by the build (docs/design/helm-chart-deployment.md, "Registry charts")
# ---------------------------------------------------------------------------


def _built_meta(build_path: Path, reference: str, meta: dict) -> None:
    import yaml

    module_dir = build_path / "apps" / reference
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "meta.yaml").write_text(yaml.safe_dump({"releaseName": reference, "namespace": "apps", **meta}))


_DIGEST = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"


def test_a_pinned_chart_is_recorded_at_its_exact_version_with_its_digest(tmp_path: Path):
    _built_meta(
        tmp_path,
        "auth",
        {
            "chartName": "authentik",
            "chartVersion": "2024.12.3",
            "chartVersionConstraint": "^2024.12.0",
            "chartDigest": f"sha256:{_DIGEST}",
            "chartAppVersion": "2024.12.3",
            "chartSubcharts": [{"name": "postgresql", "version": "15.5.38"}, {"name": "redis", "version": "19.0.1"}],
            "chartRemote": "goauthentik",
        },
    )
    module = _chart_module("authentik", chart_name="authentik", chart_version="^2024.12.0")

    chart, postgresql, redis = HelmCollector().collect(_graph(), [module], tmp_path).components

    assert (chart.name, chart.version) == ("authentik", "2024.12.3")
    assert chart.purl.startswith("pkg:helm/authentik@2024.12.3")
    assert chart.hashes == {"SHA-256": _DIGEST}
    assert chart.properties == {
        "strata:version-constraint": "^2024.12.0",
        "strata:app-version": "2024.12.3",
        "strata:used-by": "module:authentik",
    }
    assert (postgresql.name, postgresql.version, postgresql.purl) == (
        "postgresql",
        "15.5.38",
        "pkg:helm/postgresql@15.5.38",
    )
    assert postgresql.properties == {"strata:subchart-of": "authentik@2024.12.3"}
    assert (redis.name, redis.version) == ("redis", "19.0.1")


def test_an_exact_pinned_version_has_no_constraint_property(tmp_path: Path):
    _built_meta(
        tmp_path, "auth", {"chartName": "authentik", "chartVersion": "2024.12.3", "chartDigest": f"sha256:{_DIGEST}"}
    )
    module = _chart_module("authentik", chart_name="authentik", chart_version="2024.12.3")

    [chart] = HelmCollector().collect(_graph(), [module], tmp_path).components

    assert chart.version == "2024.12.3"
    assert "strata:version-constraint" not in chart.properties
    assert chart.hashes == {"SHA-256": _DIGEST}


def test_a_chart_the_build_could_not_pin_keeps_its_declaration_and_says_so(tmp_path: Path):
    _built_meta(tmp_path, "auth", {"chartName": "authentik", "chartVersion": "^2024.12.0"})
    module = _chart_module("authentik", chart_name="authentik", chart_version="^2024.12.0")

    [chart] = HelmCollector().collect(_graph(), [module], tmp_path).components

    assert chart.version == "^2024.12.0"
    assert chart.hashes == {}
    assert chart.properties == {"strata:pinned": "false", "strata:used-by": "module:authentik"}


def _local_chart_with_dependencies(build_path: Path, meta: dict) -> None:
    _built_meta(build_path, "app", meta)
    (build_path / "apps" / "app" / "Chart.yaml").write_text(
        "apiVersion: v2\nname: app\nversion: 1.0.0\ndependencies:\n"
        "  - name: redis\n    version: ~19.0\n    repository: https://charts.example.com\n"
        "  - name: common\n    version: 0.1.0\n    repository: file://../common\n"
    )


def test_a_local_charts_placed_dependencies_are_recorded_at_their_exact_versions(tmp_path: Path):
    _local_chart_with_dependencies(
        tmp_path,
        {
            "chartDependencies": [
                {
                    "name": "redis",
                    "version": "19.0.4",
                    "versionConstraint": "~19.0",
                    "repository": "https://charts.example.com",
                    "digest": f"sha256:{_DIGEST}",
                    "origin": "pulled",
                },
                {"name": "common", "version": "0.1.0", "repository": "file://../common", "origin": "file"},
            ]
        },
    )

    components = HelmCollector().collect(_graph(), [], tmp_path).components

    by_name = {(c.name, c.version): c for c in components}
    assert set(by_name) == {("redis", "19.0.4"), ("common", "0.1.0"), ("app", "1.0.0")}
    redis = by_name[("redis", "19.0.4")]
    assert redis.hashes == {"SHA-256": _DIGEST}
    assert redis.properties == {"strata:subchart-of": "app@1.0.0", "strata:version-constraint": "~19.0"}
    assert redis.purl.startswith("pkg:helm/redis@19.0.4")
    common = by_name[("common", "0.1.0")]
    assert common.purl == "pkg:helm/common@0.1.0"
    assert common.hashes == {}


def test_a_dependency_the_build_could_not_pin_keeps_its_declaration_and_says_so(tmp_path: Path):
    _local_chart_with_dependencies(
        tmp_path,
        {
            "chartDependencies": [
                {"name": "common", "version": "0.1.0", "repository": "file://../common", "origin": "file"}
            ],
            "chartDependenciesUnpinned": ["redis"],
        },
    )

    components = HelmCollector().collect(_graph(), [], tmp_path).components

    [redis] = [c for c in components if c.name == "redis"]
    assert redis.version == "~19.0"
    assert redis.properties == {"strata:pinned": "false"}
