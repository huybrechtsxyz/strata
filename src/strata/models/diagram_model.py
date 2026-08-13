#!/usr/bin/env python3
"""Pydantic models for diagram definition validation (ADR-0034).

A ``kind: diagram`` document describes how to turn live workspace data into a
Mermaid diagram.  The render pipeline is::

    spec.sources[]  ->  Jinja context  ->  spec.template  ->  Mermaid  ->  SVG

Strata owns the first two steps (fetching workspace data and binding it into a
context); the template owns Mermaid syntax; Mermaid owns the picture.  Because
the template is Jinja and emits arbitrary text, *every* Mermaid diagram type is
expressible — there is no schema ceiling.

For the common node/edge case the template may be omitted and generated from
``spec.layout`` / ``spec.style`` instead.
"""

from enum import Enum
from typing import Annotated, Any, Dict, List, Optional

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    check_unique_names,
)

# Name a source is bound to in the Jinja context.
#
# Deliberately stricter than PlatformName, which permits hyphens: a hyphen is
# not a valid Jinja identifier, so ``{{ my-source }}`` would parse as a
# subtraction rather than a variable lookup.  Context names must therefore be
# valid Python/Jinja identifiers.
DiagramContextName = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z_][a-z0-9_]*$",
        min_length=1,
        max_length=64,
        strip_whitespace=True,
    ),
]


class DiagramSourceType(str, Enum):
    """Workspace data source that can be bound into the Jinja context."""

    TOPOLOGY = "topology"
    FILES = "files"
    RESOURCES = "resources"
    MODULES = "modules"
    NAMESPACES = "namespaces"
    STAGES = "stages"
    PROMOTION = "promotion"
    NETWORK = "network"
    FIREWALLS = "firewalls"
    DNS = "dns"
    SECRETS = "secrets"
    VARIABLES = "variables"
    FEATURES = "features"
    DRIFT = "drift"
    HISTORY = "history"
    POLICIES = "policies"
    TENANTS = "tenants"
    ENVIRONMENTS = "environments"
    REPOSITORIES = "repositories"
    SBOM = "sbom"
    APPROVALS = "approvals"
    LOCKS = "locks"
    OUTPUTS = "outputs"
    VALUES = "values"


class MermaidDiagramType(str, Enum):
    """Mermaid diagram type produced by the template."""

    FLOWCHART = "flowchart"
    SEQUENCE = "sequence"
    GANTT = "gantt"
    PIE = "pie"
    MINDMAP = "mindmap"
    CLASS = "class"
    STATE_DIAGRAM = "stateDiagram"
    TIMELINE = "timeline"
    QUADRANT = "quadrant"
    SANKEY = "sankey"


class MermaidDirection(str, Enum):
    """Layout direction for node/edge diagram types."""

    TD = "TD"
    LR = "LR"
    BT = "BT"
    RL = "RL"


class DiagramSourceModel(PlatformBaseModel):
    """One workspace data source bound into the Jinja render context."""

    # populate_by_name so the field is settable as both 'as' (YAML, where 'as'
    # reads naturally) and 'bind' (Python, where 'as' is a reserved keyword).
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: DiagramSourceType = Field(..., description="Workspace data source to fetch.")
    bind: Optional[DiagramContextName] = Field(
        None,
        alias="as",
        serialization_alias="as",
        description=(
            "Name this source is bound to in the Jinja context (e.g. 'topo' makes it "
            "available as {{ topo.nodes }}). Defaults to the source type. Must be a "
            "valid Jinja identifier — lowercase letters, digits, and underscores only."
        ),
    )
    filter: Optional[Dict[str, Any]] = Field(
        None,
        description="Narrow the source to a subset (e.g. environment: prd, severity: [critical, high]).",
    )

    @property
    def context_name(self) -> str:
        """Name this source is bound to in the Jinja context."""
        return self.bind or self.type.value


class DiagramLayoutModel(PlatformBaseModel):
    """Layout hints used to generate a template when ``spec.template`` is omitted."""

    type: MermaidDiagramType = Field(
        default=MermaidDiagramType.FLOWCHART,
        description="Mermaid diagram type to generate.",
    )
    direction: Optional[MermaidDirection] = Field(
        None,
        description="Layout direction (node/edge diagram types only).",
    )


class DiagramHighlightModel(PlatformBaseModel):
    """Conditional emphasis rule applied to matching nodes."""

    condition: str = Field(
        ...,
        min_length=1,
        description="Expression evaluated per node, e.g. 'drift.severity == critical'.",
    )
    token: str = Field(
        ...,
        min_length=1,
        description=(
            "Design System token name (e.g. 'critical'), never a raw colour. Tokens "
            "resolve to theme-aware values at render time so a saved diagram stays "
            "portable across users with different editor themes."
        ),
    )


class DiagramStyleModel(PlatformBaseModel):
    """Styling hints used to generate a template when ``spec.template`` is omitted."""

    color_by: Optional[str] = Field(
        None,
        description="Node field driving colour (e.g. 'status', 'drift_status'), mapped to a token ramp.",
    )
    group_by: Optional[str] = Field(
        None,
        description="Node field to group into subgraphs (e.g. 'namespace', 'topology').",
    )
    highlight: Optional[List[DiagramHighlightModel]] = Field(
        None,
        description="Conditional emphasis rules.",
    )


class DiagramSpecModel(PlatformBaseModel):
    """Specification for a diagram definition."""

    sources: Optional[List[DiagramSourceModel]] = Field(
        None,
        description=(
            "Workspace data sources bound into the Jinja context. Omit for a purely "
            "static diagram whose template contains no live data."
        ),
    )
    template: Optional[str] = Field(
        None,
        description=(
            "Jinja2 template rendering the context into Mermaid source. Takes precedence "
            "over layout/style. Omit to have one generated from layout/style instead."
        ),
    )
    layout: Optional[DiagramLayoutModel] = Field(
        None,
        description="Layout hints used to generate a template when 'template' is omitted.",
    )
    style: Optional[DiagramStyleModel] = Field(
        None,
        description="Styling hints used to generate a template when 'template' is omitted.",
    )

    @model_validator(mode="after")
    def validate_renderable(self) -> "DiagramSpecModel":
        """Require either an explicit template or layout hints to generate one."""
        if self.template is None and self.layout is None:
            raise ValueError(
                "A diagram needs either 'spec.template' (a Jinja template) or 'spec.layout' "
                "(hints to generate one). Provide at least one."
            )
        return self

    @model_validator(mode="after")
    def validate_unique_source_names(self) -> "DiagramSpecModel":
        """Each source must bind to a distinct name — they become context keys."""
        if self.sources:
            check_unique_names([s.context_name for s in self.sources], "diagram source context names")
        return self


class DiagramMetaModel(PlatformBaseModel):
    """Model for diagram metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(..., description="Unique name for the diagram.")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: Optional[List[Any]] = Field(None, description="Optional list of tags for the diagram.")


class DiagramModel(PlatformBaseModel):
    """Top-level model for a diagram definition."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version of the diagram model.",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.DIAGRAM,
        frozen=True,
        description="Resource kind: always 'diagram'.",
    )
    meta: DiagramMetaModel = Field(..., description="Metadata for the diagram.")
    spec: DiagramSpecModel = Field(..., description="Specification for the diagram definition.")

    @model_validator(mode="after")
    def validate_kind_is_diagram(self) -> "DiagramModel":
        """Validate that kind is always 'diagram'."""
        if self.kind != PlatformKind.DIAGRAM:
            raise ValueError(f"Expected kind 'diagram', got '{self.kind}'")
        return self
