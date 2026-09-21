#!/usr/bin/env python3
"""Pydantic models for topology configuration validation."""

from typing import Any

from pydantic import Field, model_validator

from strata.models.common_models import (
    ModuleReferenceModel,
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    check_unique_names,
)


class TopologyComponentModel(PlatformBaseModel):
    """A resource reference belonging to this topology, optionally with modules attached.

    ``modules`` attaches application code directly to this resource (e.g. an
    Azure Function App's function code onto its Function App resource) —
    no container-orchestration namespace involved. Uses the same
    `ModuleReferenceModel` as `Namespace.spec.modules`: both are ultimately
    "a pointer to a Module document plus placement metadata," just attached
    at different levels (namespace grouping vs. a single resource).
    """

    resource: PlatformName = Field(..., description="Resource name reference (must exist elsewhere in the solution)")
    modules: list[ModuleReferenceModel] | None = Field(
        None, description="Modules (application code) attached directly to this resource"
    )

    @model_validator(mode="after")
    def validate_unique_module_names(self) -> "TopologyComponentModel":
        """Validate that module names are unique within this component."""
        if self.modules:
            check_unique_names([m.name for m in self.modules], f"module names on resource '{self.resource}'")
        return self

    @model_validator(mode="after")
    def validate_single_main_slot(self) -> "TopologyComponentModel":
        """When multiple modules are enabled on one resource, exactly one must be 'main'."""
        if not self.modules or len(self.modules) <= 1:
            return self
        enabled = [m for m in self.modules if m.enabled]
        if not enabled:
            return self
        main_slots = [m for m in enabled if m.slot_type == "main"]
        if not main_slots:
            raise ValueError(
                f"Resource '{self.resource}' has multiple enabled modules but no 'main' slot defined"
            )
        if len(main_slots) > 1:
            raise ValueError(
                f"Resource '{self.resource}' has multiple modules marked as 'main' slot: "
                f"{[m.name for m in main_slots]}"
            )
        return self


class TopologyNamespaceReferenceModel(PlatformBaseModel):
    """A namespace reference belonging to this topology."""

    namespace: PlatformName = Field(..., description="Namespace name reference (must exist elsewhere in the solution)")


class TopologyVolumeModel(PlatformBaseModel):
    """Model for a topology volume."""

    name: PlatformName = Field(description="Unique volume name within the topology")
    type: str = Field(
        default="local", description="Volume storage type (e.g., local, replicated, distributed, nfs, iscsi, etc.)"
    )
    size: str | None = Field(None, description="Volume capacity (e.g., '10Gi', '500Mi', '1Ti')")
    mount_path: str | None = Field(None, description="Mount path for the volume (e.g., '/data', '/mnt/shared')")
    access_mode: str | None = Field(
        None,
        description="Volume access mode - concurrency pattern at container level "
        "(e.g., 'ReadWriteOnce', 'ReadWriteMany', 'ReadOnlyMany')",
    )
    mode: str | None = Field(
        None, description="Filesystem permissions within the volume (e.g., '0755' octal, 'rw', 'ro')"
    )
    driver: str | None = Field(None, description="Storage driver or CSI plugin (e.g., 'nfs.csi.k8s.io', 'local-path')")
    configuration: dict[str, Any] | None = Field(
        None, description="Driver-specific configuration (e.g., server, share, secretRef)"
    )


class TopologySpecModel(PlatformBaseModel):
    """Topology specification: a named grouping of resources and namespaces.

    A topology answers "what conceptually belongs together" (e.g. "the AKS
    cluster + its blob store + its key vault"), for documentation, diagramming,
    and workload placement. It deliberately has **no** `provider`/`provisioner`
    field: which tool builds/deploys these resources, and in what order, is a
    separate concern (`Provisioner`/`ProvisioningStep`, a later ADR) — a
    topology can be built and configured by several different provisioners
    acting on different subsets of it, or a single provisioner can span
    several topologies. Binding "grouping" and "tooling" together was v1's
    design; see ADR-0011.
    """

    type: PlatformName = Field(..., description="Topology type (e.g., dockerswarm, kubernetes, azure-native)")
    components: list[TopologyComponentModel] = Field(
        ..., min_length=1, description="Resource references that belong to this topology"
    )
    namespaces: list[TopologyNamespaceReferenceModel] | None = Field(
        None, description="Namespace references deployed on this topology"
    )
    volumes: list[TopologyVolumeModel] | None = Field(None, description="Topology volumes")

    @model_validator(mode="after")
    def validate_unique_component_resources(self) -> "TopologySpecModel":
        """Validate that resource references are unique within this topology."""
        check_unique_names([c.resource for c in self.components], "resource references in topology")
        return self

    @model_validator(mode="after")
    def validate_unique_namespace_refs(self) -> "TopologySpecModel":
        """Validate that namespace references are unique within this topology."""
        if self.namespaces:
            check_unique_names([n.namespace for n in self.namespaces], "namespace references in topology")
        return self

    @model_validator(mode="after")
    def validate_unique_volume_names(self) -> "TopologySpecModel":
        """Validate that volume names are unique within this topology."""
        if self.volumes:
            check_unique_names([v.name for v in self.volumes], "volume names in topology")
        return self


class TopologyMetaModel(PlatformBaseModel):
    """Topology metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique topology name")
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: list[Any] | None = Field(None, description="Optional list of tags")


class TopologyModel(PlatformBaseModel):
    """Root model for a topology resource."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2,
        frozen=True,
        description="API version for topology configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.TOPOLOGY,
        frozen=True,
        description="Platform kind (always 'topology')",
    )
    meta: TopologyMetaModel = Field(description="Topology metadata (name, annotations, labels, tags)")
    spec: TopologySpecModel = Field(description="Topology specification (type, components, namespaces, volumes)")
