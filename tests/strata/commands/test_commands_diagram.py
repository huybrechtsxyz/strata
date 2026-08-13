#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_commands_diagram.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : `strata diagram` command group tests for strata CLI (ADR-0034).
===============================================================================
"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from strata.commands.cli_diagram import diagram_group
from strata.commands.cli_validate import validate_command
from strata.controllers.diagram_controller import BUILT_IN, WORKSPACE, DiagramController
from strata.utils.system import get_pkg_diagrams_path

AZURE_AKS_PATH = Path(__file__).resolve().parents[3] / "config" / "azure-aks"


def _extract_json(output: str) -> dict:
    """Pull the JSON envelope out of output that may carry banner lines."""
    start = output.find('{\n  "success"')
    if start == -1:
        start = output.find('{"success"')
    assert start != -1, f"No JSON found in output: {output[:300]}"
    depth = 0
    for i, ch in enumerate(output[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(output[start : i + 1])
    raise AssertionError("Unbalanced JSON in output")


@pytest.fixture
def user_diagram(tmp_path):
    """A workspace holding one user-authored diagram definition."""
    diagrams = tmp_path / ".strata" / "diagrams"
    diagrams.mkdir(parents=True)
    (diagrams / "mine.yaml").write_text(
        "apiVersion: strata.huybrechts.xyz/v1\n"
        "kind: diagram\n"
        "meta:\n"
        "  name: mine\n"
        "  annotations:\n"
        "    description: 'A hand-drawn context view'\n"
        "spec:\n"
        "  template: |\n"
        "    flowchart TD\n"
        "      a --> b\n",
        encoding="utf-8",
    )
    return tmp_path


class TestBuiltInDefinitions:
    def test_built_ins_ship_with_the_package(self):
        names = {p.stem for p in get_pkg_diagrams_path().glob("*.yaml")}
        assert {"refs", "topology"} <= names

    def test_built_ins_are_valid_diagram_documents(self, tmp_path):
        """Built-ins are ordinary definitions, so they must pass ordinary validation."""
        controller = DiagramController(tmp_path)
        for path in get_pkg_diagrams_path().glob("*.yaml"):
            assert controller.load(path) is not None, f"{path.name}: {controller.get_errors()}"


class TestDiagramControllerResolution:
    def test_built_in_resolves_by_name(self, tmp_path):
        controller = DiagramController(tmp_path)
        assert controller.resolve_definition("topology") == get_pkg_diagrams_path() / "topology.yaml"

    def test_workspace_definition_resolves_by_name(self, user_diagram):
        controller = DiagramController(user_diagram)
        resolved = controller.resolve_definition("mine")
        assert resolved == user_diagram / ".strata" / "diagrams" / "mine.yaml"

    def test_workspace_definition_shadows_a_built_in(self, tmp_path):
        """A user can override a built-in without having to rename it."""
        diagrams = tmp_path / ".strata" / "diagrams"
        diagrams.mkdir(parents=True)
        override = diagrams / "topology.yaml"
        override.write_text(
            "apiVersion: strata.huybrechts.xyz/v1\n"
            "kind: diagram\n"
            "meta:\n"
            "  name: topology\n"
            "spec:\n"
            "  template: 'flowchart TD'\n",
            encoding="utf-8",
        )
        controller = DiagramController(tmp_path)
        assert controller.resolve_definition("topology") == override

    def test_explicit_path_resolves(self, user_diagram):
        controller = DiagramController(user_diagram)
        assert controller.resolve_definition(".strata/diagrams/mine.yaml") is not None

    def test_unknown_name_reports_where_it_looked(self, tmp_path):
        controller = DiagramController(tmp_path)
        assert controller.resolve_definition("nope") is None
        assert "not found" in controller.get_errors()[0]
        assert "diagram list" in controller.get_errors()[0]


class TestDiagramControllerListing:
    def test_lists_built_ins(self, tmp_path):
        entries = DiagramController(tmp_path).list_definitions()
        assert {e["name"] for e in entries} >= {"refs", "topology"}
        assert all(e["source"] == BUILT_IN for e in entries)

    def test_lists_workspace_definitions(self, user_diagram):
        entries = DiagramController(user_diagram).list_definitions()
        mine = next(e for e in entries if e["name"] == "mine")
        assert mine["source"] == WORKSPACE
        assert mine["description"] == "A hand-drawn context view"

    def test_listing_skips_invalid_definitions(self, tmp_path):
        """One broken file must not take the whole listing down."""
        diagrams = tmp_path / ".strata" / "diagrams"
        diagrams.mkdir(parents=True)
        (diagrams / "broken.yaml").write_text("not: a diagram\n", encoding="utf-8")
        entries = DiagramController(tmp_path).list_definitions()
        assert all(e["name"] != "broken" for e in entries)
        assert {e["name"] for e in entries} >= {"refs", "topology"}


class TestDiagramControllerRendering:
    def test_layout_only_generates_a_template(self, tmp_path):
        """layout/style is sugar that generates a template, not a second format."""
        diagrams = tmp_path / ".strata" / "diagrams"
        diagrams.mkdir(parents=True)
        path = diagrams / "sugar.yaml"
        path.write_text(
            "apiVersion: strata.huybrechts.xyz/v1\n"
            "kind: diagram\n"
            "meta:\n"
            "  name: sugar\n"
            "spec:\n"
            "  sources:\n"
            "    - type: files\n"
            "  layout:\n"
            "    type: flowchart\n"
            "    direction: LR\n",
            encoding="utf-8",
        )
        controller = DiagramController(tmp_path)
        model = controller.load(path)
        assert model is not None
        template = controller.get_template(model)
        assert template is not None
        assert template.startswith("flowchart LR")
        assert "{%- for node in files.nodes %}" in template

    def test_layout_only_without_sources_is_rejected(self, tmp_path):
        """A generated template has nothing to draw without a source."""
        diagrams = tmp_path / ".strata" / "diagrams"
        diagrams.mkdir(parents=True)
        path = diagrams / "empty.yaml"
        path.write_text(
            "apiVersion: strata.huybrechts.xyz/v1\n"
            "kind: diagram\n"
            "meta:\n"
            "  name: empty\n"
            "spec:\n"
            "  layout:\n"
            "    type: flowchart\n",
            encoding="utf-8",
        )
        controller = DiagramController(tmp_path)
        model = controller.load(path)
        assert controller.get_template(model) is None
        assert "spec.sources" in controller.get_errors()[0]

    def test_non_node_edge_layout_points_at_spec_template(self, tmp_path):
        diagrams = tmp_path / ".strata" / "diagrams"
        diagrams.mkdir(parents=True)
        path = diagrams / "chart.yaml"
        path.write_text(
            "apiVersion: strata.huybrechts.xyz/v1\n"
            "kind: diagram\n"
            "meta:\n"
            "  name: chart\n"
            "spec:\n"
            "  sources:\n"
            "    - type: drift\n"
            "  layout:\n"
            "    type: pie\n",
            encoding="utf-8",
        )
        controller = DiagramController(tmp_path)
        model = controller.load(path)
        assert controller.get_template(model) is None
        assert "spec.template" in controller.get_errors()[0]

    def test_renders_topology_built_in(self):
        controller = DiagramController(AZURE_AKS_PATH, entry="stack/azure-ws-platform.yaml", no_validate=True)
        model = controller.load(controller.resolve_definition("topology"))
        mermaid = controller.render(model)
        assert mermaid is not None
        assert mermaid.startswith("graph TD")
        assert "classDef resource" in mermaid
        assert 'subgraph aks_cluster["aks_cluster (terraform)"]' in mermaid
        assert 'click postgres "strata://workspace/azure_aks_platform/resource/postgres"' in mermaid

    def test_renders_refs_built_in(self):
        controller = DiagramController(AZURE_AKS_PATH, no_validate=True)
        model = controller.load(controller.resolve_definition("refs"))
        mermaid = controller.render(model)
        assert mermaid is not None
        assert mermaid.startswith("graph LR")
        assert "classDef valid" in mermaid
        assert 'click deploy_azure_aks_deploy_prd "strata://file/deploy/azure-aks-deploy-prd.yaml"' in mermaid

    def test_rendered_node_ids_are_mermaid_safe(self):
        """Cross-repo '@refs' must not leak an '@' into a node ID."""
        controller = DiagramController(AZURE_AKS_PATH, no_validate=True)
        model = controller.load(controller.resolve_definition("refs"))
        mermaid = controller.render(model)
        for line in mermaid.splitlines():
            node_id = line.strip().split("[")[0]
            if line.strip().startswith("click") or "classDef" in line:
                continue
            assert "@" not in node_id, line


class TestDiagramShowCommand:
    def test_show_help(self):
        result = CliRunner().invoke(diagram_group, ["show", "--help"])
        assert result.exit_code == 0
        assert "Mermaid" in result.output

    def test_show_requires_a_definition(self):
        result = CliRunner().invoke(diagram_group, ["show"])
        assert result.exit_code == 2

    def test_show_built_in(self):
        result = CliRunner().invoke(
            diagram_group,
            ["show", "-f", "refs", "--no-validate", "--work-path", str(AZURE_AKS_PATH)],
        )
        assert result.exit_code == 0
        assert "graph LR" in result.output

    def test_show_unknown_definition_fails(self, tmp_path):
        result = CliRunner().invoke(diagram_group, ["show", "-f", "nope", "--work-path", str(tmp_path)])
        assert result.exit_code == 1

    def test_show_json_output(self):
        result = CliRunner().invoke(
            diagram_group,
            ["show", "-f", "refs", "--no-validate", "--output", "json", "--work-path", str(AZURE_AKS_PATH)],
        )
        data = _extract_json(result.output)
        assert data["success"] is True
        assert data["data"]["diagram"] == "refs"
        assert data["data"]["mermaid"].startswith("graph LR")

    def test_print_template_emits_jinja_not_mermaid(self):
        result = CliRunner().invoke(
            diagram_group,
            ["show", "-f", "refs", "--print-template", "--work-path", str(AZURE_AKS_PATH)],
        )
        assert result.exit_code == 0
        assert "{%- for n in files.nodes %}" in result.output

    def test_save_writes_mermaid_verbatim(self, tmp_path):
        save_path = tmp_path / "out.mmd"
        result = CliRunner().invoke(
            diagram_group,
            [
                "show",
                "-f",
                "refs",
                "--no-validate",
                "--save",
                str(save_path),
                "--work-path",
                str(AZURE_AKS_PATH),
            ],
        )
        assert result.exit_code == 0
        content = save_path.read_text(encoding="utf-8")
        assert content.startswith("graph LR")
        assert "# Workspace Dependency Graph" not in content

    def test_policies_source_without_a_workspace_reports_a_clear_error(self, tmp_path):
        """'policies' is the one source needing an active profile — every other
        source in this command works with no initialized workspace at all."""
        (tmp_path / ".strata" / "diagrams").mkdir(parents=True)
        (tmp_path / ".strata" / "diagrams" / "pols.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\n"
            "kind: diagram\n"
            "meta:\n"
            "  name: pols\n"
            "spec:\n"
            "  sources:\n"
            "    - type: policies\n"
            "  template: '{{ policies.nodes }}'\n",
            encoding="utf-8",
        )
        result = CliRunner().invoke(diagram_group, ["show", "-f", "pols", "--work-path", str(tmp_path)])
        assert result.exit_code == 1
        assert "initialized workspace" in result.output

    def test_print_template_never_needs_the_active_profile(self, tmp_path):
        """--print-template only needs the template, not resolved data — a
        'policies' source must not force the config-loading path here."""
        (tmp_path / ".strata" / "diagrams").mkdir(parents=True)
        (tmp_path / ".strata" / "diagrams" / "pols.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\n"
            "kind: diagram\n"
            "meta:\n"
            "  name: pols\n"
            "spec:\n"
            "  sources:\n"
            "    - type: policies\n"
            "  template: '{{ policies.nodes }}'\n",
            encoding="utf-8",
        )
        result = CliRunner().invoke(
            diagram_group, ["show", "-f", "pols", "--print-template", "--work-path", str(tmp_path)]
        )
        assert result.exit_code == 0


class TestDiagramListCommand:
    def test_list_console(self):
        result = CliRunner().invoke(diagram_group, ["list"])
        assert result.exit_code == 0
        assert "topology" in result.output
        assert "built-in" in result.output

    def test_list_json(self):
        result = CliRunner().invoke(diagram_group, ["list", "--output", "json"])
        data = _extract_json(result.output)
        assert data["success"] is True
        assert {d["name"] for d in data["data"]["diagrams"]} >= {"refs", "topology"}


class TestValidateGraphIsGone:
    def test_graph_subcommand_points_at_its_replacement(self):
        """`validate graph` never gated anything (exit 0/1, never 3) — it is replaced."""
        result = CliRunner().invoke(validate_command, ["graph"])
        assert result.exit_code == 2
        assert "strata diagram show" in result.output

    def test_validate_group_help_no_longer_offers_graph(self):
        result = CliRunner().invoke(validate_command, ["--help"])
        assert result.exit_code == 0
        assert "run" in result.output
        assert "graph" not in result.output
