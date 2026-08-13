#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_controllers_graph.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : GraphController and graph dataclass tests for strata CLI.
===============================================================================
"""

from pathlib import Path

from strata.controllers.graph_controller import GraphController
from strata.utils.graph import slugify_path

AZURE_AKS_PATH = Path(__file__).resolve().parents[3] / "config" / "azure-aks"


class TestSlugifyPath:
    def test_simple_path(self):
        assert slugify_path("deploy/my-file.yaml") == "deploy_my_file"

    def test_nested_path(self):
        assert slugify_path("stack/azure-res-aks.yaml") == "stack_azure_res_aks"

    def test_no_extension(self):
        assert slugify_path("deploy/my-file") == "deploy_my_file"

    def test_dots_in_path(self):
        assert slugify_path("a.b/c-d.yaml") == "a_b_c_d"

    def test_backslash(self):
        assert slugify_path("deploy\\my-file.yaml") == "deploy_my_file"

    def test_cross_repo_marker_becomes_a_prefix(self):
        """Mermaid rejects '@' in a node ID, and '_' would leave a leading underscore."""
        assert slugify_path("@haven/stack/ws-platform.yaml") == "at_haven_stack_ws_platform"

    def test_domain_name_is_not_treated_as_a_file_extension(self):
        """'example.com' has no file behind it — '.com' must not be stripped like '.yaml'."""
        assert slugify_path("example.com") == "example_com"

    def test_domain_names_with_different_tlds_do_not_collide(self):
        assert slugify_path("example.com") != slugify_path("example.org")

    def test_at_only_rewritten_when_leading(self):
        assert slugify_path("deploy/a@b.yaml") == "deploy_a@b"


class TestGraphController:
    def test_file_graph_discovers_deployment(self):
        controller = GraphController(work_path=AZURE_AKS_PATH, no_validate=True)
        result = controller.build_file_graph()
        assert result.mode == "files"
        assert len(result.entry_points) >= 1
        assert any("deploy" in ep for ep in result.entry_points)
        assert len(result.nodes) > 0

    def test_file_graph_with_entry(self):
        controller = GraphController(
            work_path=AZURE_AKS_PATH,
            entry="deploy/azure-aks-deploy-prd.yaml",
            no_validate=True,
        )
        result = controller.build_file_graph()
        assert any("azure-aks-deploy-prd" in ep for ep in result.entry_points)
        assert len(result.nodes) > 0
        deploy_node = next(n for n in result.nodes if n.kind == "deployment")
        assert deploy_node.name == "azure_aks_deploy_prd"

    def test_file_graph_marks_external_refs(self):
        controller = GraphController(work_path=AZURE_AKS_PATH, no_validate=True)
        result = controller.build_file_graph()
        external_nodes = [n for n in result.nodes if n.status == "external"]
        assert len(external_nodes) > 0
        assert all(n.identifier.startswith("@") for n in external_nodes)

    def test_resource_graph_builds_from_workspace(self):
        controller = GraphController(
            work_path=AZURE_AKS_PATH,
            entry="stack/azure-ws-platform.yaml",
            no_validate=True,
        )
        result = controller.build_resource_graph()
        assert result.mode == "resources"
        assert len(result.nodes) > 0
        assert len(result.topologies) > 0
        resource_nodes = [n for n in result.nodes if n.kind == "resource"]
        assert len(resource_nodes) >= 1

    def test_resource_graph_exposes_workspace_name(self):
        """Needed to form strata://workspace/<name>/... URIs."""
        controller = GraphController(
            work_path=AZURE_AKS_PATH,
            entry="stack/azure-ws-platform.yaml",
            no_validate=True,
        )
        result = controller.build_resource_graph()
        assert result.workspace_name == "azure_aks_platform"

    def test_resource_graph_missing_entry_returns_error(self):
        controller = GraphController(
            work_path=AZURE_AKS_PATH,
            entry="nonexistent.yaml",
            no_validate=True,
        )
        controller.build_resource_graph()
        assert controller.has_errors()

    def test_file_graph_missing_entry_returns_error(self):
        controller = GraphController(
            work_path=AZURE_AKS_PATH,
            entry="nonexistent.yaml",
            no_validate=True,
        )
        controller.build_file_graph()
        assert controller.has_errors()

    def test_file_graph_ignores_non_dict_yaml(self, tmp_path):
        """A top-level YAML list (e.g. an Ansible playbook) must be skipped,
        not crash — yaml.safe_load() can return any YAML type, not just a dict,
        and _parse_yaml() call sites all assume dict semantics via .get()."""
        (tmp_path / "playbook.yml").write_text(
            "- name: a play\n  hosts: all\n  tasks:\n    - name: a task\n      debug:\n        msg: hi\n",
            encoding="utf-8",
        )
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
            "apiVersion: strata.huybrechts.xyz/v1\nkind: workspace\nmeta:\n  name: sample_ws\nspec: {}\n",
            encoding="utf-8",
        )

        controller = GraphController(work_path=tmp_path, no_validate=True)
        result = controller.build_file_graph()

        assert not controller.has_errors()
        assert any(n.kind == "deployment" for n in result.nodes)
        # The playbook is not a strata document — it must not appear as a node
        assert all("playbook" not in n.identifier for n in result.nodes)
