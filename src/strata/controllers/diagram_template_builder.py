#!/usr/bin/env python3
"""Generate a Jinja template from ``spec.layout`` / ``spec.style`` (ADR-0034).

``layout``/``style`` are sugar, not a second format: they *generate* a template
that is then rendered by the same code path an authored ``spec.template`` takes.
``strata diagram show --print-template`` emits the result, so the sugar is never
a dead end — outgrow it and you keep the generated template as a starting point.

The sugar covers the node/edge case only. Anything else (pie, gantt, sankey…) is
a template, which is the primary authoring path anyway.
"""

from __future__ import annotations

from typing import List, Optional

from strata.models.diagram_model import (
    DiagramLayoutModel,
    DiagramSourceModel,
    DiagramStyleModel,
    MermaidDiagramType,
)
from strata.utils.diagram_expressions import parse_condition

# Mermaid diagram types the sugar can generate. Both are node/edge shaped and
# both support classDef, which is what style.color_by and style.highlight need.
GENERATABLE_TYPES = {
    MermaidDiagramType.FLOWCHART: "flowchart",
    MermaidDiagramType.STATE_DIAGRAM: "stateDiagram-v2",
}

HIGHLIGHT_CLASS_PREFIX = "highlight_"


class TemplateBuildError(ValueError):
    """Raised when layout/style cannot be turned into a template."""


def build_template(
    layout: DiagramLayoutModel,
    style: Optional[DiagramStyleModel],
    sources: Optional[List[DiagramSourceModel]],
) -> str:
    """Generate a Jinja template rendering *sources* per *layout* and *style*.

    Args:
        layout: Diagram type and direction.
        style: Optional colouring, grouping and highlight rules.
        sources: Sources whose nodes and edges the template iterates.

    Returns:
        Jinja template source emitting Mermaid.

    Raises:
        TemplateBuildError: If the layout type is not node/edge shaped, or there
            are no sources to draw.
    """
    if layout.type not in GENERATABLE_TYPES:
        raise TemplateBuildError(
            f"'spec.layout.type: {layout.type.value}' cannot be generated from layout/style — "
            f"the shorthand only covers node/edge diagrams "
            f"({', '.join(sorted(t.value for t in GENERATABLE_TYPES))}). "
            f"Write 'spec.template' instead; every Mermaid type is expressible there."
        )
    if not sources:
        raise TemplateBuildError(
            "A generated template needs at least one entry in 'spec.sources' to draw. "
            "For a static diagram, write 'spec.template' directly."
        )

    keyword = GENERATABLE_TYPES[layout.type]
    header = f"{keyword} {layout.direction.value}" if layout.direction else keyword
    names = [source.context_name for source in sources]

    lines: List[str] = [header]
    lines += _class_definitions(style, names)
    for name in names:
        lines += _nodes(style, name)
    for name in names:
        lines += _edges(name)
    for name in names:
        lines += _highlights(style, name)
    return "\n".join(lines) + "\n"


def _class_definitions(style: Optional[DiagramStyleModel], names: List[str]) -> List[str]:
    """Emit a classDef per colour actually present in the data, plus one per highlight rule.

    The distinct values are not knowable when the template is generated, so the
    template derives them at render time rather than guessing a fixed set.
    """
    lines: List[str] = []
    if style and style.color_by:
        for name in names:
            lines.append(f"{{%- for value in {name}.nodes | map(attribute='{style.color_by}') | unique | sort %}}")
            lines.append("  classDef {{ value }} {{ value | token }}")
            lines.append("{%- endfor %}")
    for index, rule in enumerate(_highlight_rules(style)):
        # stroke-width so a highlight reads as emphasis even where the colour
        # is close to the node's own class.
        lines.append(f"  classDef {HIGHLIGHT_CLASS_PREFIX}{index} {{{{ '{rule.token}' | token }}}},stroke-width:3px")
    return lines


def _nodes(style: Optional[DiagramStyleModel], name: str) -> List[str]:
    """Emit node declarations, wrapped in subgraphs when grouping is requested."""
    color_by = style.color_by if style else None
    group_by = style.group_by if style else None

    if not group_by:
        return [f"{{%- for node in {name}.nodes %}}", *_node_body(color_by, indent="  "), "{%- endfor %}"]

    # default= so a node missing the grouping attribute lands in a visible
    # bucket rather than failing the whole render.
    return [
        f"{{%- for group, members in {name}.nodes | groupby('{group_by}', default='(ungrouped)') %}}",
        '  subgraph {{ group | slug }}["{{ group | mermaid_escape }}"]',
        "{%- for node in members %}",
        *_node_body(color_by, indent="    "),
        "{%- endfor %}",
        "  end",
        "{%- endfor %}",
    ]


def _node_body(color_by: Optional[str], indent: str) -> List[str]:
    """One node declaration plus its click directive, at a shared indent."""
    css = f":::{{{{ node.{color_by} }}}}" if color_by else ""
    return [
        f'{indent}{{{{ node.id }}}}["{{{{ node.label | mermaid_escape }}}}"]{css}',
        "{%- if node.uri %}",
        f'{indent}click {{{{ node.id }}}} "{{{{ node.uri }}}}"',
        "{%- endif %}",
    ]


def _edges(name: str) -> List[str]:
    return [
        f"{{%- for edge in {name}.edges %}}",
        "{%- if edge.label %}",
        "  {{ edge.source }} -->|{{ edge.label | mermaid_escape }}| {{ edge.target }}",
        "{%- else %}",
        "  {{ edge.source }} --> {{ edge.target }}",
        "{%- endif %}",
        "{%- endfor %}",
    ]


def _highlights(style: Optional[DiagramStyleModel], name: str) -> List[str]:
    """Emit a trailing `class` statement per matching node, overriding its colour."""
    lines: List[str] = []
    for index, rule in enumerate(_highlight_rules(style)):
        lines.append(f"{{%- for node in {name}.nodes %}}")
        lines.append(f"{{%- if {parse_condition(rule.condition, node_var='node')} %}}")
        lines.append(f"  class {{{{ node.id }}}} {HIGHLIGHT_CLASS_PREFIX}{index}")
        lines.append("{%- endif %}")
        lines.append("{%- endfor %}")
    return lines


def _highlight_rules(style: Optional[DiagramStyleModel]) -> List:
    if not style or not style.highlight:
        return []
    return list(style.highlight)
