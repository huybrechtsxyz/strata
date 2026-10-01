#!/usr/bin/env python3
"""Tests for `ComposeCollector` (docs/design/sbom-generation.md Phase 1)."""

from pathlib import Path

from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.integrations.sbom_collectors.compose_collector import ComposeCollector
from strata.models.common_models import SourceModel
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


def test_collect_returns_empty_when_build_path_does_not_exist(tmp_path: Path):
    result = ComposeCollector().collect(_graph(), [], tmp_path / "missing")
    assert result.components == []


def test_collect_extracts_images_from_a_rendered_compose_file(tmp_path: Path):
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  redis:\n    image: redis:7.2\n  web:\n    build: .\n", encoding="utf-8"
    )
    result = ComposeCollector().collect(_graph(), [], tmp_path)

    assert len(result.components) == 1
    comp = result.components[0]
    assert comp.name == "redis"
    assert comp.version == "7.2"
    assert comp.source_collector == "compose"


def test_collect_flags_a_floating_tag_as_a_warning(tmp_path: Path):
    (tmp_path / "docker-compose.yml").write_text("services:\n  app:\n    image: acme/app:latest\n", encoding="utf-8")
    result = ComposeCollector().collect(_graph(), [], tmp_path)

    assert result.components[0].properties == {"strata:tag-stability": "floating"}
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].severity is Severity.WARNING


def test_collect_warns_on_an_unparseable_compose_file(tmp_path: Path):
    (tmp_path / "docker-compose.yml").write_text("{ not: valid: yaml: [", encoding="utf-8")
    result = ComposeCollector().collect(_graph(), [], tmp_path)

    assert result.components == []
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].severity is Severity.WARNING
    assert "failed to parse" in result.diagnostics[0].message


def test_collect_scans_recursively_and_deduplicates_by_purl(tmp_path: Path):
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "docker-compose.yml").write_text(
        "services:\n  redis:\n    image: redis:7.2\n", encoding="utf-8"
    )
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "docker-compose.yaml").write_text(
        "services:\n  redis:\n    image: redis:7.2\n", encoding="utf-8"
    )
    result = ComposeCollector().collect(_graph(), [], tmp_path)
    assert len(result.components) == 1
