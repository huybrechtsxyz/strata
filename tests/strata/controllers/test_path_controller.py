#!/usr/bin/env python3
"""Tests for `path_controller.py` (`strata path get`/`strata path list`)."""

from pathlib import Path

from strata.controllers.path_controller import get_path, list_paths
from strata.controllers.solution_context import open_solution
from strata.models.common_models import PlatformKind

MANIFEST = """apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec: {}
"""

PROVIDER = """apiVersion: strata.huybrechts.xyz/v2
kind: provider
meta:
  name: azure-main
spec:
  properties:
    type: azure
    region: westeurope
"""

WORKSPACE = """apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: main
spec:
  providers: [azure-main]
  provisioners:
    - name: tf
      tool: terraform
      source: {source_path: terraform/main}
"""

VERSION = """apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: {name}
spec:
{workspace_line}  pins: {{}}
"""


def _solution(tmp_path: Path) -> Path:
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "provider.yaml").write_text(PROVIDER, encoding="utf-8")
    (root / "workspace.yaml").write_text(WORKSPACE, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# get_path
# ---------------------------------------------------------------------------


def test_get_path_resolves_a_real_document(tmp_path):
    root = _solution(tmp_path)
    context = open_solution(root)

    result = get_path(context.controller.index, PlatformKind.WORKSPACE, "main")

    assert result.exists is True
    assert Path(result.path) == (root / "workspace.yaml").resolve()
    assert result.kind == "workspace"
    assert result.name == "main"


def test_get_path_reports_absence_rather_than_raising(tmp_path):
    root = _solution(tmp_path)
    context = open_solution(root)

    result = get_path(context.controller.index, PlatformKind.WORKSPACE, "ghost")

    assert result.exists is False
    assert result.path is None
    assert result.kind == "workspace"
    assert result.name == "ghost"


# ---------------------------------------------------------------------------
# list_paths
# ---------------------------------------------------------------------------


def test_list_paths_returns_every_document_of_one_kind(tmp_path):
    root = _solution(tmp_path)
    context = open_solution(root)

    results = list_paths(context.controller.index, PlatformKind.PROVIDER)

    assert len(results) == 1
    assert results[0].name == "azure-main"
    assert Path(results[0].path) == (root / "provider.yaml").resolve()
    assert results[0].exists is True


def test_list_paths_on_a_kind_with_no_documents_is_empty(tmp_path):
    root = _solution(tmp_path)
    context = open_solution(root)

    assert list_paths(context.controller.index, PlatformKind.DNS) == []


def test_list_paths_workspace_filter_matches_declared_field(tmp_path):
    root = _solution(tmp_path)
    (root / "version-prd.yaml").write_text(
        VERSION.format(name="prd", workspace_line="  workspace: main\n"), encoding="utf-8"
    )
    (root / "version-other.yaml").write_text(VERSION.format(name="other", workspace_line=""), encoding="utf-8")
    context = open_solution(root)

    results = list_paths(context.controller.index, PlatformKind.VERSION, workspace="main")

    assert [r.name for r in results] == ["prd"]


def test_list_paths_workspace_filter_on_unsupported_kind_is_empty_not_an_error(tmp_path):
    """`provider` has no `spec.workspace` field at all — filtering by it
    returns nothing rather than raising."""
    root = _solution(tmp_path)
    context = open_solution(root)

    results = list_paths(context.controller.index, PlatformKind.PROVIDER, workspace="main")

    assert results == []
