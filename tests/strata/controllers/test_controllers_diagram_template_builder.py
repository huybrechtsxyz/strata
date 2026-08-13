#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_controllers_diagram_template_builder.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : layout/style -> Jinja template generation tests (ADR-0034).
===============================================================================
"""

import pytest

from strata.controllers.diagram_template_builder import TemplateBuildError, build_template
from strata.models.diagram_model import (
    DiagramLayoutModel,
    DiagramSourceModel,
    DiagramStyleModel,
)
from strata.utils.templater import TemplateProcessor

NODES = [
    {"id": "web", "label": "web", "kind": "resource", "status": "active", "uri": "strata://a/web"},
    {"id": "db", "label": 'db "primary"', "kind": "resource", "status": "disabled"},
    {"id": "core", "label": "core", "kind": "namespace", "status": "active", "uri": "strata://a/core"},
]
EDGES = [{"source": "web", "target": "db", "label": "depends_on"}, {"source": "web", "target": "core", "label": ""}]
CONTEXT = {"topo": {"nodes": NODES, "edges": EDGES}}


def _layout(**kwargs) -> DiagramLayoutModel:
    return DiagramLayoutModel.model_validate({"type": "flowchart", **kwargs})


def _sources(*names) -> list:
    return [DiagramSourceModel.model_validate({"type": "topology", "as": name}) for name in names]


def _render(layout, style=None, sources=None) -> str:
    template = build_template(layout, style, sources or _sources("topo"))
    assert TemplateProcessor.check_syntax(template) is None, template
    return TemplateProcessor.render(template, CONTEXT)


class TestGeneratedTemplateIsUsable:
    def test_output_is_a_valid_jinja_template(self):
        template = build_template(_layout(), None, _sources("topo"))
        assert TemplateProcessor.check_syntax(template) is None

    def test_minimal_layout_renders_nodes_and_edges(self):
        mermaid = _render(_layout())
        assert mermaid.startswith("flowchart\n")
        assert 'web["web"]' in mermaid
        assert "web -->|depends_on| db" in mermaid
        assert "web --> core" in mermaid

    def test_direction_is_applied(self):
        assert _render(_layout(direction="LR")).startswith("flowchart LR")

    def test_state_diagram_uses_the_v2_keyword(self):
        layout = DiagramLayoutModel.model_validate({"type": "stateDiagram"})
        assert _render(layout).startswith("stateDiagram-v2")

    def test_labels_are_escaped(self):
        """An unescaped quote would truncate the diagram at that node."""
        assert 'db["db #quot;primary#quot;"]' in _render(_layout())

    def test_click_directives_only_for_nodes_with_a_uri(self):
        mermaid = _render(_layout())
        assert 'click web "strata://a/web"' in mermaid
        assert "click db" not in mermaid


class TestColorBy:
    def test_classdefs_are_derived_from_the_data(self):
        """Distinct values are not knowable at generate time, so the template derives them."""
        mermaid = _render(_layout(), DiagramStyleModel.model_validate({"color_by": "kind"}))
        assert "classDef namespace fill:#d1fae5,stroke:#059669" in mermaid
        assert "classDef resource fill:#dbeafe,stroke:#2563eb" in mermaid

    def test_nodes_carry_the_class(self):
        mermaid = _render(_layout(), DiagramStyleModel.model_validate({"color_by": "kind"}))
        assert 'web["web"]:::resource' in mermaid
        assert 'core["core"]:::namespace' in mermaid

    def test_no_classdefs_without_color_by(self):
        assert "classDef" not in _render(_layout())


class TestGroupBy:
    def test_nodes_are_wrapped_in_subgraphs(self):
        mermaid = _render(_layout(), DiagramStyleModel.model_validate({"group_by": "status"}))
        assert 'subgraph active["active"]' in mermaid
        assert 'subgraph disabled["disabled"]' in mermaid
        assert mermaid.count("  end") == 2

    def test_missing_group_attribute_falls_into_a_visible_bucket(self):
        """A node missing the attribute must not fail the whole render."""
        style = DiagramStyleModel.model_validate({"group_by": "team"})
        template = build_template(_layout(), style, _sources("topo"))
        mermaid = TemplateProcessor.render(template, CONTEXT)
        assert "(ungrouped)" in mermaid


class TestHighlight:
    def test_matching_nodes_get_a_class_statement(self):
        style = DiagramStyleModel.model_validate({"highlight": [{"condition": "status == disabled", "token": "warn"}]})
        mermaid = _render(_layout(), style)
        assert "classDef highlight_0 fill:#fff3cd,stroke:#ffc107,stroke-width:3px" in mermaid
        assert "class db highlight_0" in mermaid
        assert "class web highlight_0" not in mermaid

    def test_membership_condition(self):
        style = DiagramStyleModel.model_validate(
            {"highlight": [{"condition": "kind in [namespace, network]", "token": "low"}]}
        )
        mermaid = _render(_layout(), style)
        assert "class core highlight_0" in mermaid
        assert "class web highlight_0" not in mermaid

    def test_multiple_rules_get_distinct_classes(self):
        style = DiagramStyleModel.model_validate(
            {
                "highlight": [
                    {"condition": "status == disabled", "token": "warn"},
                    {"condition": "kind == namespace", "token": "low"},
                ]
            }
        )
        mermaid = _render(_layout(), style)
        assert "class db highlight_0" in mermaid
        assert "class core highlight_1" in mermaid

    def test_highlight_emphasises_with_stroke_width(self):
        """Colour alone is not enough when the token is close to the node's own class."""
        style = DiagramStyleModel.model_validate(
            {"color_by": "kind", "highlight": [{"condition": "kind == resource", "token": "resource"}]}
        )
        assert "stroke-width:3px" in _render(_layout(), style)


class TestMultipleSources:
    def test_every_source_is_drawn(self):
        template = build_template(_layout(), None, _sources("a", "b"))
        mermaid = TemplateProcessor.render(
            template,
            {
                "a": {"nodes": [{"id": "x", "label": "x"}], "edges": []},
                "b": {"nodes": [{"id": "y", "label": "y"}], "edges": []},
            },
        )
        assert 'x["x"]' in mermaid
        assert 'y["y"]' in mermaid


class TestBuildErrors:
    def test_non_node_edge_type_is_rejected(self):
        """The shorthand only covers node/edge diagrams; everything else is a template."""
        layout = DiagramLayoutModel.model_validate({"type": "pie"})
        with pytest.raises(TemplateBuildError, match="only covers node/edge"):
            build_template(layout, None, _sources("topo"))

    def test_error_points_at_spec_template(self):
        layout = DiagramLayoutModel.model_validate({"type": "gantt"})
        with pytest.raises(TemplateBuildError, match="spec.template"):
            build_template(layout, None, _sources("topo"))

    def test_no_sources_is_rejected(self):
        with pytest.raises(TemplateBuildError, match="at least one entry in 'spec.sources'"):
            build_template(_layout(), None, None)
