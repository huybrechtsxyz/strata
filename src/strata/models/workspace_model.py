#!/usr/bin/env python3
"""Pydantic models for workspace configuration validation.

The workspace is the "image" (ADR-0011): a static, versioned declaration of
everything that could exist in a solution — providers, resources, topologies
(pure groupings), namespaces, firewalls/dns/networks, and the provisioning
recipe (`ProvisionerModel`/`ProvisioningStepModel`) that builds/deploys them.
A `Deployment` (not yet built, the "container instance") executes that
recipe against a specific `Environment` — it does not invent new tool
bindings, only supplies runtime parameters.
"""

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from strata.models.common_models import (
    CommonLifecycleModel,
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
)
from strata.models.provisioning_model import ProvisionerModel, ProvisioningStepModel, validate_provisioning_steps
from strata.utils.names import check_unique_names


class WorkspaceProviderModel(PlatformBaseModel):
    """Name+file reference to a standalone Provider document."""

    name: PlatformName = Field(description="Unique provider name")
    file: str = Field(description="Path to the provider configuration file")
    description: str | None = Field(None, description="Optional description for documentation purposes")


class WorkspaceNamespaceModel(PlatformBaseModel):
    """Name+file reference to a standalone Namespace document."""

    name: PlatformName = Field(description="Unique namespace name")
    file: str = Field(description="File reference for the namespace configuration")
    description: str | None = Field(None, description="Optional description for documentation purposes")


class WorkspaceFirewallModel(PlatformBaseModel):
    """Name+file reference to a standalone Firewall document."""

    name: PlatformName = Field(description="Unique firewall name")
    file: str = Field(description="File reference for the firewall configuration")
    description: str | None = Field(None, description="Optional description for documentation purposes")


class WorkspaceDnsModel(PlatformBaseModel):
    """Name+file reference to a standalone DNS document."""

    name: PlatformName = Field(description="Unique DNS zone configuration name")
    file: str = Field(description="File reference for the DNS zone configuration")
    description: str | None = Field(None, description="Optional description for documentation purposes")


class WorkspaceNetworkModel(PlatformBaseModel):
    """Name+file reference to a standalone Network document."""

    name: PlatformName = Field(description="Unique network configuration name")
    file: str = Field(description="File reference for the network topology configuration")
    description: str | None = Field(None, description="Optional description for documentation purposes")


class WorkspaceTopologyModel(PlatformBaseModel):
    """Name+file reference to a standalone Topology document (ADR-0011).

    Deliberately just a pointer — Topology's own internal references
    (`components[].resource`, `namespaces[].namespace`) are cross-checked
    against this workspace's `resources`/`namespaces` in Phase 2
    (`WorkspaceService._validate_dynamic()`), once that file is actually
    loaded. Unlike `resources`/`namespaces`/`firewalls`/etc. below, no
    Phase 1 check can reach into Topology's contents from here.
    """

    name: PlatformName = Field(description="Unique topology name")
    file: str = Field(description="File reference for the topology configuration")
    description: str | None = Field(None, description="Optional description for documentation purposes")


class WorkspaceResourceSubnetModel(PlatformBaseModel):
    """Structured reference to a subnet within one of this workspace's networks.

    Replaces v1's unvalidated 'network_name/subnet_name' string — in v1
    that string was never checked against anything real (the only code that
    even parsed it was the diagram controller's best-effort edge-drawing).
    `network` is checked in Phase 1 (below) against `spec.networks[].name`;
    `subnet` can only be checked in Phase 2, once that Network document is
    actually loaded, against its real `NetworkDefinitionModel.subnets[]` —
    same asymmetry as `Topology`'s own Phase 1/Phase 2 split (ADR-0011).
    """

    network: PlatformName = Field(description="Network reference name (must exist in this workspace's spec.networks)")
    subnet: PlatformName = Field(
        description="Subnet name within that network (validated in Phase 2, once the network file is loaded)"
    )


class WorkspaceResourceModel(PlatformBaseModel):
    """Workspace resource definition — the gluing layer between a Resource
    document and this workspace's deployment-specific overrides.

    No `modules` field here (unlike v1's `WorkspaceModuleReferenceModel`) —
    attaching a module to a resource is `TopologyComponentModel.modules`'
    job exclusively (ADR-0011); duplicating it here would reintroduce the
    same two-places-for-one-fact problem already resolved for
    `NamespaceModuleModel`/`ModuleReferenceModel`.
    """

    name: PlatformName = Field(description="Unique resource name")
    file: str | None = Field(
        None, description="Path to the resource configuration file. Required unless managed_by is set."
    )
    managed_by: Literal["provisioner"] | None = Field(
        None,
        description="Indicates the resource is fully managed externally and no resource file is needed "
        "(e.g. resource details defined entirely in Terraform/Ansible).",
    )
    description: str | None = Field(None, description="Optional description for documentation purposes")
    enabled: bool = Field(
        default=True,
        description="Whether this resource is deployed in this workspace (ADR-0083, v1). Set false to "
        "exclude it from the built platform artifact, and therefore from every provisioner that consumes "
        "it. Intended to be overridable per-environment via environment.spec.resources[].enabled once "
        "Environment is built. 'enabled' is strata's consistent cross-schema keyword for conditional "
        "inclusion — 'condition'/'when'/'if' are not accepted.",
    )
    role: PlatformName | None = Field(None, description="Role of the resource (e.g., networking, database, api)")
    count: int = Field(default=1, ge=1, le=100, description="Number of resource instances")
    depends_on: list[str] | None = Field(
        None, description="List of resource names this resource depends on (workspace-specific gluing)"
    )
    firewalls: list[str] | None = Field(
        None, description="References to firewall resource names for network security"
    )
    subnet: WorkspaceResourceSubnetModel | None = Field(
        None, description="Structured reference to a subnet within one of this workspace's networks"
    )
    configuration: dict[str, Any] | None = Field(
        None, description="Workspace-specific configuration overrides (merged with resource file configuration)"
    )
    custom: dict[str, Any] | None = Field(None, description="Optional additional properties for the resource")
    labels: dict[str, Any] | None = Field(None, description="Optional labels (key-value pairs for classification)")
    tags: list[Any] | None = Field(None, description="Optional tags (list of values for categorization)")
    default_tags: dict[str, str] | None = Field(
        None,
        description="Workspace-specific override for this resource's default_tags (see "
        "ResourceSpecModel.default_tags). Deliberately distinct from the `tags` field above (a free-form "
        "list for strata-internal categorization, not cloud tags).",
    )
    custom_tags: dict[str, str] | None = Field(
        None,
        description="Workspace-specific additions to this resource's custom_tags (see "
        "ResourceSpecModel.custom_tags).",
    )

    @field_validator("depends_on", mode="before")
    @classmethod
    def coerce_depends_on(cls, v: str | list[str] | None) -> list[str] | None:
        """Allow a single string as shorthand for a one-element list."""
        if isinstance(v, str):
            return [v]
        return v

    @model_validator(mode="after")
    def validate_file_or_managed_by(self) -> "WorkspaceResourceModel":
        """A resource must have either a file reference or a managed_by declaration."""
        if not self.file and not self.managed_by:
            raise ValueError(
                f"Resource '{self.name}' must either specify a 'file' path or set 'managed_by: provisioner'."
            )
        if self.file and self.managed_by:
            raise ValueError(f"Resource '{self.name}' cannot both specify a 'file' and 'managed_by'.")
        return self


class WorkspaceMetaModel(PlatformBaseModel):
    """Workspace metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique workspace name")
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: list[Any] | None = Field(None, description="Optional tags (list of values for categorization)")


class WorkspaceSpecModel(PlatformBaseModel):
    """Workspace specification: the static declaration of everything a solution needs.

    No `references` field (ADR-0002). No `WorkspaceIacModel`-style embedded
    provisioner-topology binding (ADR-0011) — `topology` is a list of pure
    name+file pointers (unlike v1, deliberately **optional**, since a
    workspace may have resources with no grouping concept at all), and the
    actual build/deploy recipe is `provisioning: list[ProvisioningStepModel]`,
    fully decoupled from `topology`.
    """

    lifecycle: CommonLifecycleModel | None = Field(None, description="Workspace lifecycle phases")
    properties: dict[str, Any] | None = Field(None, description="Workspace properties")
    configuration: dict[str, Any] | None = Field(None, description="Workspace-level configuration passthrough")
    custom: dict[str, Any] | None = Field(None, description="Optional additional properties (key-value pairs)")
    default_tags: dict[str, str] | None = Field(
        None,
        description="Solution-wide baseline cloud provider tags (e.g. 'managed-by: strata'), applied on top "
        "of/underneath each Provider's own default_tags — useful when a workspace spans multiple providers. "
        "Optional (unlike ResourceSpecModel.default_tags): not every workspace needs an organization-wide "
        "tagging policy.",
    )

    providers: list[WorkspaceProviderModel] = Field(..., min_length=1, description="Provider references")
    provisioners: list[ProvisionerModel] = Field(..., min_length=1, description="Provisioner (tool) definitions")
    provisioning: list[ProvisioningStepModel] | None = Field(
        None, description="The provisioning recipe: ordered steps binding a Provisioner to a set of targets"
    )
    topology: list[WorkspaceTopologyModel] | None = Field(None, description="Topology references (pure grouping)")
    resources: list[WorkspaceResourceModel] | None = Field(None, description="Workspace resource definitions")
    namespaces: list[WorkspaceNamespaceModel] | None = Field(None, description="Namespace references")
    firewalls: list[WorkspaceFirewallModel] | None = Field(None, description="Firewall references")
    dns_zones: list[WorkspaceDnsModel] | None = Field(None, description="DNS zone references")
    networks: list[WorkspaceNetworkModel] | None = Field(None, description="Network topology references")

    @model_validator(mode="after")
    def validate_unique_names(self) -> "WorkspaceSpecModel":
        """Validate that names are unique within each list."""
        check_unique_names([p.name for p in self.providers], "provider names")
        check_unique_names([p.name for p in self.provisioners], "provisioner names")
        if self.topology:
            check_unique_names([t.name for t in self.topology], "topology names")
        if self.resources:
            check_unique_names([r.name for r in self.resources], "resource names")
        if self.namespaces:
            check_unique_names([n.name for n in self.namespaces], "namespace names")
        if self.firewalls:
            check_unique_names([f.name for f in self.firewalls], "firewall names")
        if self.dns_zones:
            check_unique_names([d.name for d in self.dns_zones], "DNS zone names")
        if self.networks:
            check_unique_names([n.name for n in self.networks], "network names")
        return self

    @model_validator(mode="after")
    def validate_resource_firewall_references(self) -> "WorkspaceSpecModel":
        """Validate that resource firewall references exist in this workspace's firewalls."""
        if not self.resources:
            return self
        firewall_names = {f.name for f in self.firewalls} if self.firewalls else set()
        errors = []
        for resource in self.resources:
            for firewall in resource.firewalls or []:
                if firewall not in firewall_names:
                    errors.append(f"Resource '{resource.name}' references undefined firewall '{firewall}'")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def validate_resource_subnet_references(self) -> "WorkspaceSpecModel":
        """Validate that resource subnet references name a real workspace network.

        Only the `network` half is checkable here (Phase 1, self-contained);
        the `subnet` half names something inside that Network document's own
        content and can only be checked once that file is loaded (Phase 2 —
        see `WorkspaceService`, same deferred pattern as Topology).
        """
        if not self.resources:
            return self
        network_names = {n.name for n in self.networks} if self.networks else set()
        errors = []
        for resource in self.resources:
            if resource.subnet and resource.subnet.network not in network_names:
                errors.append(
                    f"Resource '{resource.name}' subnet references undefined network '{resource.subnet.network}'"
                )
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def validate_provisioning(self) -> "WorkspaceSpecModel":
        """Validate the provisioning recipe: internal step consistency plus
        cross-references against this workspace's own provisioners/resources/namespaces.
        """
        if not self.provisioning:
            return self

        validate_provisioning_steps(self.provisioning)

        provisioner_names = {p.name for p in self.provisioners}
        target_names = {r.name for r in (self.resources or [])} | {n.name for n in (self.namespaces or [])}

        errors = []
        for step in self.provisioning:
            if step.provisioner not in provisioner_names:
                errors.append(
                    f"Provisioning step '{step.name}' references undefined provisioner '{step.provisioner}'"
                )
            for target in step.targets:
                if target not in target_names:
                    errors.append(
                        f"Provisioning step '{step.name}' targets undefined resource/namespace '{target}'"
                    )
        if errors:
            raise ValueError("; ".join(errors))
        return self


class WorkspaceModel(PlatformBaseModel):
    """Root model for a workspace configuration file."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2,
        frozen=True,
        description="API version for workspace configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.WORKSPACE,
        frozen=True,
        description="Platform kind (always 'workspace')",
    )
    meta: WorkspaceMetaModel = Field(description="Workspace metadata (name, annotations, labels, tags)")
    spec: WorkspaceSpecModel = Field(
        description="Workspace specification (providers, provisioners, provisioning, topology, resources, ...)"
    )

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.WORKSPACE)
