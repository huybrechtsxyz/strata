#!/usr/bin/env python3
"""Pydantic models for workspace configuration validation.

The workspace is the "image" (ADR-0011): a static, versioned declaration of
everything that could exist in a solution — providers, resources, topologies
(pure groupings), namespaces, firewalls/dns/networks, and the provisioning
recipe (`ProvisionerModel`/`ProvisioningStepModel`) that builds/deploys them.
A `Deployment` (not yet built, the "container instance") executes that
recipe against a specific `Environment` — it does not invent new tool
bindings, only supplies runtime parameters.

**References are names, not paths.** `spec.providers`/`namespaces`/
`firewalls`/`dns_zones`/`networks`/`topology` are plain document names, and
`WorkspaceResourceModel.resource` names the Resource document an instance is
built from. These were `{name, file}` wrappers, which conflated *identity*
("which Provider do I mean") with *location* ("where its YAML sits") and
carried a `name` that had to duplicate the target's own `meta.name` — the
same fact in two places, with nothing defining which wins if they disagreed.
Documents are found by discovery and indexed by `(kind, meta.name)`, so the
name alone resolves; Kubernetes settled this long ago, where references are
identity (`sourceRef: {kind, name}`) and never paths. Each wrapper also
carried a `description`, which belongs on the target document's own `meta`
rather than being restated at every reference site.
"""

from typing import Annotated, Any, Literal

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
from strata.models.reference_fields import References
from strata.utils.names import check_unique_names


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
    resource: Annotated[PlatformName, References(PlatformKind.RESOURCE)] | None = Field(
        None,
        description="Name of the Resource document this instance is built from (its meta.name, resolved by "
        "discovery). Required unless managed_by is set. `name` above is the workspace-local *instance* "
        "name and may differ — one Resource class can be instantiated several times under different names.",
    )
    managed_by: Literal["provisioner"] | None = Field(
        None,
        description="Indicates the resource is fully managed externally and no Resource document is needed "
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
    def validate_resource_or_managed_by(self) -> "WorkspaceResourceModel":
        """A resource must name either a Resource document or a managed_by declaration."""
        if not self.resource and not self.managed_by:
            raise ValueError(
                f"Resource '{self.name}' must either specify a 'resource' (Resource document name) "
                "or set 'managed_by: provisioner'."
            )
        if self.resource and self.managed_by:
            raise ValueError(f"Resource '{self.name}' cannot both specify a 'resource' and 'managed_by'.")
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
    provisioner-topology binding (ADR-0011) — `topology` is a list of plain
    Topology names (unlike v1, deliberately **optional**, since a workspace
    may have resources with no grouping concept at all), and the actual
    build/deploy recipe is `execution: list[ProvisioningStepModel]`,
    fully decoupled from `topology`.

    The three sibling keys are deliberately distinct words, since they answer
    different questions: `providers` (**where** — target platform/account),
    `provisioners` (**with what** — tool definitions), `execution` (**what
    runs, in what order**).

    Every reference below is a document **name**, resolved by discovery
    against the `(kind, meta.name)` index — not a file path (see the note
    above `WorkspaceResourceSubnetModel`).
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

    providers: list[Annotated[PlatformName, References(PlatformKind.PROVIDER)]] = Field(
        ..., min_length=1, description="Provider document names"
    )
    provisioners: list[ProvisionerModel] = Field(..., min_length=1, description="Provisioner (tool) definitions")
    execution: list[ProvisioningStepModel] | None = Field(
        None,
        description="The ordered recipe: steps binding a Provisioner to a set of targets. Named 'execution' "
        "rather than 'provisioning' so it cannot be confused with the sibling 'provisioners' (tool "
        "definitions) or with the 'deployment' kind.",
    )
    topology: list[Annotated[PlatformName, References(PlatformKind.TOPOLOGY)]] | None = Field(
        None, description="Topology document names (pure grouping)"
    )
    resources: list[WorkspaceResourceModel] | None = Field(None, description="Workspace resource definitions")
    namespaces: list[Annotated[PlatformName, References(PlatformKind.NAMESPACE)]] | None = Field(
        None, description="Namespace document names"
    )
    firewalls: list[Annotated[PlatformName, References(PlatformKind.FIREWALL)]] | None = Field(
        None, description="Firewall document names"
    )
    dns_zones: list[Annotated[PlatformName, References(PlatformKind.DNS)]] | None = Field(
        None, description="DNS document names"
    )
    networks: list[Annotated[PlatformName, References(PlatformKind.NETWORK)]] | None = Field(
        None, description="Network document names"
    )

    @model_validator(mode="after")
    def validate_unique_names(self) -> "WorkspaceSpecModel":
        """Validate that names are unique within each list."""
        check_unique_names(self.providers, "provider names")
        check_unique_names([p.name for p in self.provisioners], "provisioner names")
        if self.topology:
            check_unique_names(self.topology, "topology names")
        if self.resources:
            check_unique_names([r.name for r in self.resources], "resource names")
        if self.namespaces:
            check_unique_names(self.namespaces, "namespace names")
        if self.firewalls:
            check_unique_names(self.firewalls, "firewall names")
        if self.dns_zones:
            check_unique_names(self.dns_zones, "DNS zone names")
        if self.networks:
            check_unique_names(self.networks, "network names")
        return self

    @model_validator(mode="after")
    def validate_resource_firewall_references(self) -> "WorkspaceSpecModel":
        """Validate that resource firewall references exist in this workspace's firewalls."""
        if not self.resources:
            return self
        firewall_names = set(self.firewalls) if self.firewalls else set()
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
        network_names = set(self.networks) if self.networks else set()
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
    def validate_execution(self) -> "WorkspaceSpecModel":
        """Validate the execution recipe: internal step consistency plus
        cross-references against this workspace's own provisioners/resources/namespaces.
        """
        if not self.execution:
            return self

        validate_provisioning_steps(self.execution)

        provisioner_names = {p.name for p in self.provisioners}
        target_names = {r.name for r in (self.resources or [])} | set(self.namespaces or [])

        errors = []
        for step in self.execution:
            if step.provisioner not in provisioner_names:
                errors.append(f"Execution step '{step.name}' references undefined provisioner '{step.provisioner}'")
            for target in step.targets:
                if target not in target_names:
                    errors.append(f"Execution step '{step.name}' targets undefined resource/namespace '{target}'")
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
