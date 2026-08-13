"""Tests for the diagram definition model (ADR-0034)."""

import pytest
import yaml
from pydantic import ValidationError

from strata.models.common_models import PlatformKind
from strata.models.diagram_model import (
    DiagramModel,
    DiagramSourceModel,
    DiagramSourceType,
    DiagramSpecModel,
    MermaidDiagramType,
    MermaidDirection,
)

MINIMAL_TEMPLATE_YAML = """
apiVersion: strata.huybrechts.xyz/v1
kind: diagram
meta:
  name: static_view
spec:
  template: |
    flowchart TD
      a --> b
"""

SOURCES_YAML = """
apiVersion: strata.huybrechts.xyz/v1
kind: diagram
meta:
  name: prd_topology
  annotations:
    description: "Production topology"
  labels:
    category: topology
spec:
  sources:
    - type: topology
      as: topo
      filter:
        environment: prd
    - type: drift
      filter:
        severity: [critical, high]
  template: |
    flowchart TD
    {% for n in topo.nodes %}
      {{ n.id }}["{{ n.label }}"]
      click {{ n.id }} "{{ n.uri }}"
    {% endfor %}
"""

SUGAR_YAML = """
apiVersion: strata.huybrechts.xyz/v1
kind: diagram
meta:
  name: sugar_view
spec:
  sources:
    - type: topology
      as: topo
  layout:
    type: flowchart
    direction: TD
  style:
    color_by: status
    group_by: namespace
    highlight:
      - condition: "drift.severity == critical"
        token: critical
"""


def _load(text: str) -> DiagramModel:
    return DiagramModel(**yaml.safe_load(text))


class TestDiagramModelParsing:
    def test_minimal_template_only(self):
        model = _load(MINIMAL_TEMPLATE_YAML)
        assert model.kind == PlatformKind.DIAGRAM
        assert model.meta.name == "static_view"
        assert model.spec.sources is None
        assert "flowchart TD" in model.spec.template

    def test_sources_with_template(self):
        model = _load(SOURCES_YAML)
        assert model.meta.annotations["description"] == "Production topology"
        assert len(model.spec.sources) == 2
        assert model.spec.sources[0].type == DiagramSourceType.TOPOLOGY
        assert model.spec.sources[0].filter == {"environment": "prd"}

    def test_layout_style_sugar_without_template(self):
        model = _load(SUGAR_YAML)
        assert model.spec.template is None
        assert model.spec.layout.type == MermaidDiagramType.FLOWCHART
        assert model.spec.layout.direction == MermaidDirection.TD
        assert model.spec.style.color_by == "status"
        assert model.spec.style.highlight[0].token == "critical"

    def test_kind_and_api_version_default(self):
        model = DiagramModel(
            meta={"name": "defaults"},
            spec={"template": "flowchart TD"},
        )
        assert model.kind == PlatformKind.DIAGRAM
        assert model.apiVersion.value == "strata.huybrechts.xyz/v1"

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError) as exc:
            DiagramModel(
                meta={"name": "x"},
                spec={"template": "flowchart TD"},
                bogus="nope",
            )
        assert exc.value.errors()[0]["type"] == "extra_forbidden"


class TestContextNameBinding:
    def test_bind_defaults_to_type(self):
        source = DiagramSourceModel(type=DiagramSourceType.DRIFT)
        assert source.context_name == "drift"

    def test_bind_via_yaml_alias(self):
        source = DiagramSourceModel(**{"type": "topology", "as": "topo"})
        assert source.context_name == "topo"

    def test_bind_via_python_field_name(self):
        # populate_by_name — 'as' is a reserved keyword in Python
        source = DiagramSourceModel(type=DiagramSourceType.TOPOLOGY, bind="topo")
        assert source.context_name == "topo"

    @pytest.mark.parametrize("name", ["my-source", "1source", "My_Source", "has space"])
    def test_rejects_non_jinja_identifiers(self, name):
        # A hyphen would parse as subtraction in Jinja ({{ my-source }}),
        # so context names must be valid identifiers.
        with pytest.raises(ValidationError):
            DiagramSourceModel(**{"type": "topology", "as": name})

    @pytest.mark.parametrize("name", ["topo", "_private", "src2", "a_b_c"])
    def test_accepts_valid_jinja_identifiers(self, name):
        source = DiagramSourceModel(**{"type": "topology", "as": name})
        assert source.context_name == name


class TestSpecValidation:
    def test_requires_template_or_layout(self):
        with pytest.raises(ValidationError) as exc:
            DiagramSpecModel(sources=[{"type": "topology"}])
        assert "spec.template" in str(exc.value)

    def test_layout_alone_is_sufficient(self):
        spec = DiagramSpecModel(layout={"type": "pie"})
        assert spec.template is None
        assert spec.layout.type == MermaidDiagramType.PIE

    def test_template_alone_is_sufficient(self):
        spec = DiagramSpecModel(template="pie title X")
        assert spec.layout is None

    def test_duplicate_context_names_rejected(self):
        with pytest.raises(ValidationError) as exc:
            DiagramSpecModel(
                sources=[
                    {"type": "topology", "as": "data"},
                    {"type": "drift", "as": "data"},
                ],
                template="flowchart TD",
            )
        assert "Duplicate" in str(exc.value)

    def test_implicit_and_explicit_name_collision_rejected(self):
        # 'drift' defaults to its type name, which collides with the explicit bind
        with pytest.raises(ValidationError):
            DiagramSpecModel(
                sources=[
                    {"type": "drift"},
                    {"type": "topology", "as": "drift"},
                ],
                template="flowchart TD",
            )

    def test_distinct_context_names_accepted(self):
        spec = DiagramSpecModel(
            sources=[
                {"type": "topology", "as": "topo"},
                {"type": "drift"},
            ],
            template="flowchart TD",
        )
        assert [s.context_name for s in spec.sources] == ["topo", "drift"]


class TestNonFlowchartTypes:
    """Every Mermaid type is expressible — the template emits arbitrary text."""

    @pytest.mark.parametrize("mermaid_type", [t.value for t in MermaidDiagramType])
    def test_all_mermaid_types_accepted_in_layout(self, mermaid_type):
        spec = DiagramSpecModel(layout={"type": mermaid_type})
        assert spec.layout.type.value == mermaid_type

    def test_pie_chart_via_template(self):
        # Previously "structurally inexpressible" — now just Jinja's groupby filter.
        model = _load("""
apiVersion: strata.huybrechts.xyz/v1
kind: diagram
meta:
  name: drift_by_severity
spec:
  sources:
    - type: drift
  template: |
    pie title Drift by severity
    {% for severity, entries in drift.entries | groupby('severity') %}
      "{{ severity }}" : {{ entries | length }}
    {% endfor %}
""")
        assert "pie title" in model.spec.template
        assert "groupby" in model.spec.template
