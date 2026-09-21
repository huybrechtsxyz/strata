#!/usr/bin/env python3
"""Topology type registry, as a standalone kind.

Promoted out of `ConfigurationSpecModel` (was a bare embedded list,
`config_topology_model.py`) into its own kind — same reasoning as
`ProviderConfigModel`'s promotion (see that module's docstring): a platform
with many topology types (kubernetes, dockerswarm, azure-native...)
shouldn't have to grow one shared file for every new type. Referenced from
`Configuration` by name+file, same pattern as everywhere else.
"""

from typing import Any

from pydantic import Field, model_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
)
from strata.utils.names import check_unique_names


class TopologyConfigComponentModel(PlatformBaseModel):
    """A topology type's registry entry for one expected component role."""

    role: PlatformName = Field(description="Component role (matched against WorkspaceResourceModel.role)")
    description: str | None = Field(None, description="Description of this component role")
    uses_module: bool = Field(False, description="Whether a resource with this role must have a module attached")
    required: bool = Field(True, description="Whether at least one resource with this role must be present")
    min_count: int = Field(0, ge=0, description="Minimum number of resource instances with this role")
    max_count: int = Field(0, ge=0, description="Maximum number of resource instances with this role (0 = unlimited)")

    @model_validator(mode="after")
    def validate_count_relationship(self) -> "TopologyConfigComponentModel":
        """max_count must be >= min_count, unless max_count is 0 (unlimited)."""
        if self.max_count != 0 and self.max_count < self.min_count:
            raise ValueError(
                f"Component role '{self.role}': max_count ({self.max_count}) must be >= min_count ({self.min_count})"
            )
        return self


class TopologyConfigSpecModel(PlatformBaseModel):
    """A topology type's registry entry: its expected component roles."""

    description: str | None = Field(None, description="Description of the topology type")
    additional_components: bool = Field(
        False, description="Allow component roles not listed in this topology type's registry entry"
    )
    components: list[TopologyConfigComponentModel] | None = Field(
        None, description="Expected component roles for this topology type"
    )

    @model_validator(mode="after")
    def validate_unique_component_roles(self) -> "TopologyConfigSpecModel":
        """Validate that all component roles are unique within this topology type."""
        if self.components:
            check_unique_names([comp.role for comp in self.components], "component roles in topology config")
        return self


class TopologyConfigMetaModel(PlatformBaseModel):
    """Topology config metadata (name, annotations, labels, tags).

    `name` is the topology type key (e.g. 'kubernetes', 'dockerswarm') —
    matched against a real `Topology` document's `spec.type`.
    """

    name: PlatformName = Field(description="Topology type name (e.g. kubernetes, dockerswarm)")
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: list[Any] | None = Field(None, description="Optional list of tags")


class TopologyConfigModel(PlatformBaseModel):
    """Root model for a topology type registry entry."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2,
        frozen=True,
        description="API version for topology config",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.TOPOLOGYCONFIG,
        frozen=True,
        description="Platform kind (always 'topologyconfig')",
    )
    meta: TopologyConfigMetaModel = Field(description="Topology config metadata (name, annotations, labels, tags)")
    spec: TopologyConfigSpecModel = Field(description="Topology config specification (expected component roles)")
