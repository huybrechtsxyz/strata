"""Tests for the validate graph command."""

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from strata.commands.cli_validate import validate_command
from strata.controllers.graph_controller import GraphController
from strata.utils.graph import (
    GraphEdge,
    GraphNode,
    GraphResult,
    GraphTopology,
    compute_deployment_order,
    render_mermaid,
    render_mermaid_resources,
    render_tree,
    slugify_path,
)

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


class TestRenderMermaid:
    def test_renders_basic_graph(self):
        result = GraphResult(
            mode="files",
            nodes=[
                GraphNode(identifier="a.yaml", path="a.yaml", name="alpha", kind="deployment", status="valid"),
                GraphNode(identifier="b.yaml", path="b.yaml", name="beta", kind="workspace", status="valid"),
            ],
            edges=[GraphEdge(source="a.yaml", target="b.yaml", label="workspace")],
        )
        mermaid = render_mermaid(result)
        assert "graph LR" in mermaid
        assert "classDef valid" in mermaid
        assert 'a["alpha (a.yaml)"]:::valid' in mermaid
        assert 'b["beta (b.yaml)"]:::valid' in mermaid
        assert "a -->|workspace| b" in mermaid

    def test_missing_node_uses_identifier(self):
        result = GraphResult(
            mode="files",
            nodes=[GraphNode(identifier="missing/file.yaml", status="missing")],
            edges=[],
        )
        mermaid = render_mermaid(result)
        assert "missing_file" in mermaid
        assert ":::missing" in mermaid

    def test_direction_override(self):
        result = GraphResult(mode="files", nodes=[], edges=[])
        assert "graph TD" in render_mermaid(result, direction="TD")


class TestRenderMermaidResources:
    def test_renders_topology_subgraphs(self):
        result = GraphResult(
            mode="resources",
            nodes=[
                GraphNode(identifier="aks", name="aks", kind="resource", status="active"),
                GraphNode(identifier="pg", name="pg", kind="resource", status="active"),
            ],
            edges=[GraphEdge(source="aks", target="pg", label="depends_on")],
            topologies=[
                GraphTopology(name="infra", provisioner="terraform", type="azure", components=["aks", "pg"]),
            ],
        )
        mermaid = render_mermaid_resources(result)
        assert "graph TD" in mermaid
        assert "subgraph" in mermaid
        assert "infra" in mermaid
        assert "-->|depends_on|" in mermaid

    def test_subnet_edges_are_dotted(self):
        result = GraphResult(
            mode="resources",
            nodes=[
                GraphNode(identifier="aks", name="aks", kind="resource", status="active"),
                GraphNode(identifier="net", name="net", kind="network", status="active"),
            ],
            edges=[GraphEdge(source="aks", target="net", label="subnet: net/snet_aks")],
            topologies=[],
        )
        mermaid = render_mermaid_resources(result)
        assert "-.->|subnet:" in mermaid


class TestRenderTree:
    def test_file_tree_shows_summary(self):
        result = GraphResult(
            mode="files",
            nodes=[
                GraphNode(identifier="a.yaml", path="a.yaml", name="alpha", kind="deployment", status="valid"),
                GraphNode(identifier="b.yaml", path="b.yaml", name="beta", kind="workspace", status="missing"),
            ],
            edges=[GraphEdge(source="a.yaml", target="b.yaml", label="workspace")],
            entry_points=["a.yaml"],
        )
        tree = render_tree(result)
        assert "alpha" in tree
        assert "Summary:" in tree
        assert "1 valid" in tree
        assert "1 missing" in tree

    def test_resource_tree_shows_topology(self):
        result = GraphResult(
            mode="resources",
            nodes=[
                GraphNode(identifier="aks", name="aks", kind="resource", status="active"),
            ],
            edges=[],
            topologies=[
                GraphTopology(name="infra", provisioner="terraform", type="azure", components=["aks"]),
            ],
        )
        tree = render_tree(result)
        assert "infra" in tree
        assert "terraform" in tree
        assert "Resources: 1" in tree


class TestComputeDeploymentOrder:
    def test_no_dependencies_single_layer(self):
        result = GraphResult(
            mode="resources",
            nodes=[
                GraphNode(identifier="a", kind="resource"),
                GraphNode(identifier="b", kind="resource"),
            ],
            edges=[],
        )
        layers = compute_deployment_order(result)
        assert len(layers) == 1
        assert sorted(layers[0]) == ["a", "b"]

    def test_linear_dependency(self):
        result = GraphResult(
            mode="resources",
            nodes=[
                GraphNode(identifier="a", kind="resource"),
                GraphNode(identifier="b", kind="resource"),
                GraphNode(identifier="c", kind="resource"),
            ],
            edges=[
                GraphEdge(source="b", target="a", label="depends_on"),
                GraphEdge(source="c", target="b", label="depends_on"),
            ],
        )
        layers = compute_deployment_order(result)
        assert layers[0] == ["a"]
        assert layers[1] == ["b"]
        assert layers[2] == ["c"]

    def test_cycle_detection(self):
        result = GraphResult(
            mode="resources",
            nodes=[
                GraphNode(identifier="a", kind="resource"),
                GraphNode(identifier="b", kind="resource"),
            ],
            edges=[
                GraphEdge(source="a", target="b", label="depends_on"),
                GraphEdge(source="b", target="a", label="depends_on"),
            ],
        )
        layers = compute_deployment_order(result)
        # Should still complete without infinite loop
        assert len(layers) >= 1


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
        # Deployment node should exist
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
        # Should have resource nodes
        resource_nodes = [n for n in result.nodes if n.kind == "resource"]
        assert len(resource_nodes) >= 1

    def test_resource_graph_missing_entry_returns_error(self):
        controller = GraphController(
            work_path=AZURE_AKS_PATH,
            entry="nonexistent.yaml",
            no_validate=True,
        )
        result = controller.build_resource_graph()
        assert controller.has_errors()

    def test_file_graph_missing_entry_returns_error(self):
        controller = GraphController(
            work_path=AZURE_AKS_PATH,
            entry="nonexistent.yaml",
            no_validate=True,
        )
        result = controller.build_file_graph()
        assert controller.has_errors()


class TestGraphCommand:
    def test_graph_help(self):
        runner = CliRunner()
        result = runner.invoke(validate_command, ["graph", "--help"])
        assert result.exit_code == 0
        assert "dependency graph" in result.output.lower()

    def test_graph_files_mode(self):
        runner = CliRunner()
        result = runner.invoke(
            validate_command,
            ["graph", "--no-validate", "--work-path", str(AZURE_AKS_PATH)],
        )
        assert result.exit_code == 0

    def test_graph_json_output(self):
        runner = CliRunner()
        result = runner.invoke(
            validate_command,
            ["graph", "--no-validate", "--output", "json", "--work-path", str(AZURE_AKS_PATH)],
        )
        # JSON output is sandwiched between banner lines — extract by brace matching
        json_start = result.output.find('{\n  "success"')
        if json_start == -1:
            json_start = result.output.find('{"success"')
        assert json_start != -1, f"No JSON found in output: {result.output[:200]}"
        # Find matching closing brace by counting
        depth = 0
        json_end = json_start
        for i, ch in enumerate(result.output[json_start:], start=json_start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    json_end = i + 1
                    break
        json_text = result.output[json_start:json_end]
        data = json.loads(json_text)
        assert data["success"] is True
        assert data["data"]["mode"] == "files"
        assert len(data["data"]["nodes"]) > 0

    def test_graph_resources_mode(self):
        runner = CliRunner()
        result = runner.invoke(
            validate_command,
            [
                "graph",
                "--mode",
                "resources",
                "--entry",
                "stack/azure-ws-platform.yaml",
                "--no-validate",
                "--work-path",
                str(AZURE_AKS_PATH),
            ],
        )
        assert result.exit_code == 0

    def test_graph_save_writes_file(self, tmp_path):
        runner = CliRunner()
        save_path = tmp_path / "graph.md"
        result = runner.invoke(
            validate_command,
            [
                "graph",
                "--no-validate",
                "--save",
                str(save_path),
                "--work-path",
                str(AZURE_AKS_PATH),
            ],
        )
        assert result.exit_code == 0
        assert save_path.exists()
        content = save_path.read_text()
        assert "# Workspace Dependency Graph" in content
        assert "graph LR" in content

    def test_validate_backward_compat_still_works(self):
        """Bare `strata validate -f` still delegates to the run subcommand."""
        runner = CliRunner()
        with patch("strata.commands.validate.run_validate_command.ValidateCommand.execute", return_value=True):
            result = runner.invoke(
                validate_command,
                ["-f", "fake.yaml", "--work-path", str(AZURE_AKS_PATH)],
            )
        assert result.exit_code == 0

    def test_validate_group_help_shows_subcommands(self):
        runner = CliRunner()
        result = runner.invoke(validate_command, ["--help"])
        assert result.exit_code == 0
        assert "run" in result.output
        assert "graph" in result.output
