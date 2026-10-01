#!/usr/bin/env python3
"""Tests for the cross-document reference graph."""

from pathlib import Path

from strata.controllers.graph_controller import build_reference_graph
from strata.controllers.solution_context import open_solution

MANIFEST = """apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec:
  remotes:
    - name: infra
      type: git
      url: https://example.com/infra.git
      reference: v1.0.0
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
  providers:
    - {provider}
  provisioners:
    - name: tf
      tool: terraform
      source:
        remote: {remote}
        source_path: terraform/main
"""


def _solution(tmp_path: Path, *, provider: str = "azure-main", remote: str = "infra") -> Path:
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "provider.yaml").write_text(PROVIDER, encoding="utf-8")
    (root / "workspace.yaml").write_text(WORKSPACE.format(provider=provider, remote=remote), encoding="utf-8")
    return root


def test_resolved_reference_is_an_edge(tmp_path):
    """A provider the workspace names and that exists produces a resolved edge."""
    context = open_solution(_solution(tmp_path))
    assert context.diagnostics.ok, context.diagnostics.messages()

    edges = build_reference_graph(context.controller.index)

    provider_edges = [e for e in edges if e.target_kind == "provider"]
    assert len(provider_edges) == 1
    edge = provider_edges[0]
    assert edge.source_kind == "workspace"
    assert edge.source_name == "main"
    assert edge.target_name == "azure-main"
    assert edge.location == "spec.providers.0"
    assert edge.resolved is True


def test_unresolved_reference_is_still_an_edge(tmp_path):
    """A dangling reference is shown, not dropped — that is the point of a graph."""
    context = open_solution(_solution(tmp_path, provider="does-not-exist"))
    assert context.diagnostics.ok, context.diagnostics.messages()  # Phase 1 only; names aren't checked yet

    edges = build_reference_graph(context.controller.index)

    provider_edges = [e for e in edges if e.target_kind == "provider"]
    assert len(provider_edges) == 1
    assert provider_edges[0].target_name == "does-not-exist"
    assert provider_edges[0].resolved is False


def test_remote_references_are_excluded(tmp_path):
    """`RemoteReference` fields name a manifest remote, not a document — not part of this graph."""
    context = open_solution(_solution(tmp_path))
    assert context.diagnostics.ok, context.diagnostics.messages()

    edges = build_reference_graph(context.controller.index)

    assert not any(e.location.endswith("remote") for e in edges)


def test_edges_carry_the_source_file_path(tmp_path):
    """A consumer needs to jump to the declaring file, not just its identity."""
    root = _solution(tmp_path)
    context = open_solution(root)

    edges = build_reference_graph(context.controller.index)

    workspace_edges = [e for e in edges if e.source_kind == "workspace"]
    assert workspace_edges
    assert all(Path(e.source_path) == root / "workspace.yaml" for e in workspace_edges)


def test_no_references_is_an_empty_list(tmp_path):
    """A solution with nothing but a provider has no edges at all."""
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "provider.yaml").write_text(PROVIDER, encoding="utf-8")
    context = open_solution(root)

    assert build_reference_graph(context.controller.index) == []
