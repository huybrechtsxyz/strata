#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_controllers_diagram_source.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : DiagramSourceController tests for strata CLI (ADR-0034 step 4).
===============================================================================
"""

from pathlib import Path

import pytest

from strata.controllers.diagram_source_controller import DiagramSourceController
from strata.models.diagram_model import DiagramSourceModel, DiagramSourceType
from strata.utils.templater import TemplateProcessor

AZURE_AKS_PATH = Path(__file__).resolve().parents[3] / "config" / "azure-aks"


def _source(type_: str, bind: str | None = None, filter_: dict | None = None) -> DiagramSourceModel:
    payload: dict = {"type": type_}
    if bind is not None:
        payload["as"] = bind
    if filter_ is not None:
        payload["filter"] = filter_
    return DiagramSourceModel.model_validate(payload)


@pytest.fixture
def workspace_tree(tmp_path):
    """A minimal two-file workspace GraphController can walk in both modes."""
    (tmp_path / "deploy.yaml").write_text(
        "apiVersion: strata.huybrechts.xyz/v1\n"
        "kind: deployment\n"
        "meta:\n"
        "  name: sample_deploy\n"
        "spec:\n"
        "  workspace:\n"
        "    file: workspace.yaml\n",
        encoding="utf-8",
    )
    (tmp_path / "workspace.yaml").write_text(
        "apiVersion: strata.huybrechts.xyz/v1\n"
        "kind: workspace\n"
        "meta:\n"
        "  name: sample_ws\n"
        "spec:\n"
        "  resources:\n"
        "    - name: app_server\n"
        "      depends_on:\n"
        "        - db_server\n"
        "      modules:\n"
        "        - name: web_service\n"
        "    - name: db_server\n"
        "      enabled: false\n"
        "  namespaces:\n"
        "    - name: core\n",
        encoding="utf-8",
    )
    return tmp_path


class TestDiagramSourceControllerContract:
    def test_no_sources_returns_empty_context(self, tmp_path):
        controller = DiagramSourceController(tmp_path)
        assert controller.resolve(None) == {}
        assert controller.resolve([]) == {}
        assert not controller.has_errors()

    def test_source_binds_under_its_type_by_default(self, workspace_tree):
        controller = DiagramSourceController(workspace_tree, no_validate=True)
        context = controller.resolve([_source("files")])
        assert set(context) == {"files"}

    def test_as_alias_controls_the_context_key(self, workspace_tree):
        controller = DiagramSourceController(workspace_tree, no_validate=True)
        context = controller.resolve([_source("files", bind="refs")])
        assert set(context) == {"refs"}

    def test_multiple_sources_bind_independently(self, workspace_tree):
        controller = DiagramSourceController(workspace_tree, entry="workspace.yaml", no_validate=True)
        context = controller.resolve([_source("files"), _source("topology", bind="topo")])
        assert set(context) == {"files", "topo"}

    def test_unsupported_type_reports_error_and_binds_empty(self, tmp_path):
        """One unimplemented source must not hide the shape of the others.

        Every ``DiagramSourceType`` is implemented today, so this simulates a gap
        by removing one resolver rather than asserting on a real unsupported type.
        """
        controller = DiagramSourceController(tmp_path)
        del controller._resolvers[DiagramSourceType.DRIFT]
        context = controller.resolve([_source("drift")])
        assert controller.has_errors()
        assert "not implemented yet" in controller.get_errors()[0]
        assert context["drift"] == {"nodes": [], "edges": []}

    def test_supported_types(self, tmp_path):
        assert DiagramSourceController(tmp_path).supported_types == [
            "approvals",
            "dns",
            "drift",
            "environments",
            "features",
            "files",
            "firewalls",
            "history",
            "locks",
            "modules",
            "namespaces",
            "network",
            "outputs",
            "policies",
            "promotion",
            "repositories",
            "resources",
            "sbom",
            "secrets",
            "stages",
            "tenants",
            "topology",
            "values",
            "variables",
        ]

    def test_graph_errors_propagate(self, tmp_path):
        controller = DiagramSourceController(tmp_path, entry="nonexistent.yaml")
        controller.resolve([_source("files")])
        assert controller.has_errors()


class TestFilesSource:
    def test_node_shape(self, workspace_tree):
        controller = DiagramSourceController(workspace_tree, no_validate=True)
        nodes = controller.resolve([_source("files")])["files"]["nodes"]
        node = next(n for n in nodes if n["kind"] == "deployment")
        assert node["id"] == "deploy"
        assert node["label"] == "sample_deploy (deploy.yaml)"
        assert node["status"] == "valid"
        assert node["location"] == {"file": "deploy.yaml"}

    def test_uri_is_the_relative_path(self, workspace_tree):
        controller = DiagramSourceController(workspace_tree, no_validate=True)
        nodes = controller.resolve([_source("files")])["files"]["nodes"]
        assert {n["uri"] for n in nodes} >= {"strata://file/deploy.yaml", "strata://file/workspace.yaml"}

    def test_file_nodes_carry_no_line_number(self, workspace_tree):
        """A file node points at the document, not a position inside it."""
        controller = DiagramSourceController(workspace_tree, no_validate=True)
        nodes = controller.resolve([_source("files")])["files"]["nodes"]
        assert all("line" not in n["location"] for n in nodes)

    def test_edges_use_slugified_ids(self, workspace_tree):
        controller = DiagramSourceController(workspace_tree, no_validate=True)
        edges = controller.resolve([_source("files")])["files"]["edges"]
        assert {"source": "deploy", "target": "workspace", "label": "workspace"} in edges

    def test_entry_points_are_exposed(self, workspace_tree):
        controller = DiagramSourceController(workspace_tree, no_validate=True)
        result = controller.resolve([_source("files")])["files"]
        assert result["entry_points"] == ["deploy.yaml"]

    def test_real_workspace_resolves(self):
        controller = DiagramSourceController(AZURE_AKS_PATH, no_validate=True)
        result = controller.resolve([_source("files")])["files"]
        assert len(result["nodes"]) > 0
        assert all(n["uri"].startswith("strata://file/") for n in result["nodes"])


class TestTopologySource:
    @pytest.fixture
    def topology(self, workspace_tree):
        controller = DiagramSourceController(workspace_tree, entry="workspace.yaml", no_validate=True)
        return controller.resolve([_source("topology")])["topology"]

    def test_node_shape(self, topology):
        node = next(n for n in topology["nodes"] if n["id"] == "app_server")
        assert node["label"] == "app_server"
        assert node["kind"] == "resource"
        assert node["status"] == "active"

    def test_uri_is_structural(self, topology):
        node = next(n for n in topology["nodes"] if n["id"] == "app_server")
        assert node["uri"] == "strata://workspace/sample_ws/resource/app_server"

    def test_uri_carries_no_line_number(self, topology):
        """Structural, not positional — reordering the YAML must not break it."""
        node = next(n for n in topology["nodes"] if n["id"] == "app_server")
        assert node["location"] == {"file": "workspace.yaml"}

    def test_namespace_uri_uses_its_own_kind(self, topology):
        node = next(n for n in topology["nodes"] if n["id"] == "core")
        assert node["uri"] == "strata://workspace/sample_ws/namespace/core"

    def test_disabled_resource_keeps_its_status(self, topology):
        node = next(n for n in topology["nodes"] if n["id"] == "db_server")
        assert node["status"] == "disabled"

    def test_workspace_name_is_exposed(self, topology):
        assert topology["workspace"] == "sample_ws"

    def test_edges_are_slugified(self, topology):
        assert {"source": "app_server", "target": "db_server", "label": "depends_on"} in topology["edges"]

    def test_dangling_node_has_no_uri(self, tmp_path):
        """A depends_on target that does not exist has no workspace object to point at."""
        (tmp_path / "workspace.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\n"
            "kind: workspace\n"
            "meta:\n"
            "  name: sample_ws\n"
            "spec:\n"
            "  resources:\n"
            "    - name: app_server\n"
            "      depends_on:\n"
            "        - ghost\n",
            encoding="utf-8",
        )
        controller = DiagramSourceController(tmp_path, entry="workspace.yaml", no_validate=True)
        nodes = controller.resolve([_source("topology")])["topology"]["nodes"]
        ghost = next(n for n in nodes if n["id"] == "ghost")
        assert ghost["status"] == "dangling"
        assert "uri" not in ghost

    def test_real_workspace_resolves_topologies(self):
        controller = DiagramSourceController(AZURE_AKS_PATH, entry="stack/azure-ws-platform.yaml", no_validate=True)
        result = controller.resolve([_source("topology")])["topology"]
        assert len(result["nodes"]) > 0
        assert len(result["topologies"]) > 0
        assert all("id" in t and "components" in t for t in result["topologies"])


class TestSingleKindViews:
    """RESOURCES / MODULES / NAMESPACES — single-kind views over the same graph 'topology' uses."""

    def test_resources_view_excludes_other_kinds(self, workspace_tree):
        controller = DiagramSourceController(workspace_tree, entry="workspace.yaml", no_validate=True)
        result = controller.resolve([_source("resources")])["resources"]
        assert {n["id"] for n in result["nodes"]} == {"app_server", "db_server"}
        assert all(n["kind"] == "resource" for n in result["nodes"])

    def test_modules_view(self, workspace_tree):
        controller = DiagramSourceController(workspace_tree, entry="workspace.yaml", no_validate=True)
        result = controller.resolve([_source("modules")])["modules"]
        assert {n["id"] for n in result["nodes"]} == {"web_service"}
        assert result["nodes"][0]["kind"] == "module"
        assert result["nodes"][0]["metadata"]["parent_resource"] == "app_server"

    def test_namespaces_view(self, workspace_tree):
        controller = DiagramSourceController(workspace_tree, entry="workspace.yaml", no_validate=True)
        result = controller.resolve([_source("namespaces")])["namespaces"]
        assert {n["id"] for n in result["nodes"]} == {"core"}
        assert result["nodes"][0]["kind"] == "namespace"

    def test_dangling_resource_is_included_in_the_resources_view(self, tmp_path):
        """GraphController already gives a dangling depends_on target kind: 'resource'."""
        (tmp_path / "workspace.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\n"
            "kind: workspace\n"
            "meta:\n"
            "  name: sample_ws\n"
            "spec:\n"
            "  resources:\n"
            "    - name: app_server\n"
            "      depends_on:\n"
            "        - ghost\n",
            encoding="utf-8",
        )
        controller = DiagramSourceController(tmp_path, entry="workspace.yaml", no_validate=True)
        result = controller.resolve([_source("resources")])["resources"]
        assert {n["id"] for n in result["nodes"]} == {"app_server", "ghost"}

    def test_edges_are_not_pruned_to_the_viewed_kind(self, workspace_tree):
        """A resources-only view can still show the edge to its module; the template decides."""
        controller = DiagramSourceController(workspace_tree, entry="workspace.yaml", no_validate=True)
        result = controller.resolve([_source("resources")])["resources"]
        assert {"source": "app_server", "target": "web_service", "label": "runs"} in result["edges"]

    def test_uris_and_locations_match_the_topology_view(self, workspace_tree):
        controller = DiagramSourceController(workspace_tree, entry="workspace.yaml", no_validate=True)
        resources = controller.resolve([_source("resources")])["resources"]
        node = next(n for n in resources["nodes"] if n["id"] == "app_server")
        assert node["uri"] == "strata://workspace/sample_ws/resource/app_server"
        assert node["location"] == {"file": "workspace.yaml"}

    def test_user_filter_combines_with_the_kind_prefilter(self, workspace_tree):
        controller = DiagramSourceController(workspace_tree, entry="workspace.yaml", no_validate=True)
        result = controller.resolve([_source("resources", filter_={"status": "disabled"})])["resources"]
        assert {n["id"] for n in result["nodes"]} == {"db_server"}

    def test_topologies_are_still_exposed_on_a_single_kind_view(self, workspace_tree):
        """Layout metadata is not kind-specific — keep it available regardless of the view."""
        controller = DiagramSourceController(workspace_tree, entry="workspace.yaml", no_validate=True)
        result = controller.resolve([_source("resources")])["resources"]
        assert "topologies" in result
        assert "workspace" in result

    def test_graph_is_built_once_when_multiple_kind_views_are_combined(self, workspace_tree, monkeypatch):
        """An overview + a detail view in one diagram must not re-parse the workspace per source."""
        import strata.controllers.diagram_source_controller as module

        original = module.GraphController.build_resource_graph
        calls = []

        def counting_build(self):
            calls.append(1)
            return original(self)

        monkeypatch.setattr(module.GraphController, "build_resource_graph", counting_build)

        controller = DiagramSourceController(workspace_tree, entry="workspace.yaml", no_validate=True)
        controller.resolve(
            [
                _source("topology", bind="topo"),
                _source("resources", bind="res"),
                _source("modules", bind="mod"),
                _source("namespaces", bind="ns"),
            ]
        )
        assert len(calls) == 1

    def test_real_workspace_module_and_namespace_views_resolve(self):
        controller = DiagramSourceController(AZURE_AKS_PATH, entry="stack/azure-ws-platform.yaml", no_validate=True)
        context = controller.resolve([_source("resources"), _source("modules"), _source("namespaces")])
        assert all(n["kind"] == "resource" for n in context["resources"]["nodes"])
        assert all(n["kind"] == "module" for n in context["modules"]["nodes"])
        assert all(n["kind"] == "namespace" for n in context["namespaces"]["nodes"])


class TestSourceFilter:
    def test_scalar_filter_narrows_nodes(self, workspace_tree):
        controller = DiagramSourceController(workspace_tree, entry="workspace.yaml", no_validate=True)
        result = controller.resolve([_source("topology", filter_={"kind": "resource"})])["topology"]
        assert {n["id"] for n in result["nodes"]} == {"app_server", "db_server"}

    def test_list_filter_matches_any_entry(self, workspace_tree):
        controller = DiagramSourceController(workspace_tree, entry="workspace.yaml", no_validate=True)
        result = controller.resolve([_source("topology", filter_={"kind": ["resource", "namespace"]})])["topology"]
        assert {n["id"] for n in result["nodes"]} == {"app_server", "db_server", "core"}

    def test_multiple_keys_are_combined_with_and(self, workspace_tree):
        controller = DiagramSourceController(workspace_tree, entry="workspace.yaml", no_validate=True)
        result = controller.resolve([_source("topology", filter_={"kind": "resource", "status": "disabled"})])[
            "topology"
        ]
        assert {n["id"] for n in result["nodes"]} == {"db_server"}

    def test_unknown_key_matches_nothing(self, workspace_tree):
        """An empty diagram is visible; a silently ignored filter is not."""
        controller = DiagramSourceController(workspace_tree, entry="workspace.yaml", no_validate=True)
        result = controller.resolve([_source("topology", filter_={"nope": "x"})])["topology"]
        assert result["nodes"] == []

    def test_filter_does_not_prune_edges(self, workspace_tree):
        """Edges are left intact so a template can decide what to do with danglers."""
        controller = DiagramSourceController(workspace_tree, entry="workspace.yaml", no_validate=True)
        result = controller.resolve([_source("topology", filter_={"kind": "namespace"})])["topology"]
        assert result["nodes"] != []
        assert result["edges"] != []


class TestContextRendersMermaid:
    """The context exists to be rendered — check it against a real template."""

    def test_topology_context_renders_with_click_directives(self, workspace_tree):
        controller = DiagramSourceController(workspace_tree, entry="workspace.yaml", no_validate=True)
        context = controller.resolve([_source("topology", bind="topo")])

        template = (
            "flowchart TD\n"
            "{% for n in topo.nodes %}"
            '  {{ n.id }}["{{ n.label }}"]:::{{ n.status }}\n'
            "{% if n.uri %}"
            '  click {{ n.id }} "{{ n.uri }}"\n'
            "{% endif %}"
            "{% endfor %}"
            "{% for e in topo.edges %}"
            "  {{ e.source }} --> {{ e.target }}\n"
            "{% endfor %}"
        )
        mermaid = TemplateProcessor.render(template, context)

        assert "flowchart TD" in mermaid
        assert 'app_server["app_server"]:::active' in mermaid
        assert 'click app_server "strata://workspace/sample_ws/resource/app_server"' in mermaid
        assert "app_server --> db_server" in mermaid

    def test_template_variables_match_the_bound_context_keys(self, workspace_tree):
        """What DiagramService validates against is what the controller actually binds."""
        sources = [_source("files", bind="refs"), _source("topology", bind="topo")]
        controller = DiagramSourceController(workspace_tree, entry="workspace.yaml", no_validate=True)
        context = controller.resolve(sources)

        template = "{{ refs.nodes | length }}/{{ topo.nodes | length }}"
        assert TemplateProcessor.find_variables(template) <= set(context)
        # entry= scopes the file graph to workspace.yaml alone, while the
        # topology graph expands it into two resources, one module, and a namespace.
        assert TemplateProcessor.render(template, context) == "1/4"
