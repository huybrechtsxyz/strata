#!/usr/bin/env python3
"""Pydantic models for workspace configuration validation.

The workspace is the "image" (ADR-0011): a static, versioned declaration of
everything that could exist in a solution — providers, resources, topologies
(pure groupings), namespaces, firewalls/dns/networks, and the provisioning
recipe (`ProvisionerModel`/`ProvisioningStepModel`) that builds/deploys them.
A `Deployment` (not yet built, the "container instance") executes that
recipe against a specific `Environment` — it does not invent new tool
bindings, only supplies runtime parameters.

**References are names, not paths.** `spec.namespaces`/
`firewalls`/`dns_zones`/`networks`/`topology` are plain document names
(`spec.providers` too, via its bare-string slot shorthand), and
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

from pydantic import ConfigDict, Field, field_validator, model_validator

from strata.models.checkov_model import CheckovPolicyModel
from strata.models.common_models import (
    CommonLifecycleModel,
    ModuleReferenceModel,
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    scalar_shorthand_schema,
    validate_kind_matches,
)
from strata.models.provisioning_model import ProvisionerModel, ProvisioningStepModel, validate_provisioning_steps
from strata.models.reference_fields import References
from strata.models.sbom_model import CveMaxSeverityPolicyModel
from strata.utils.dependency_order import topological_order
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
    firewalls: list[str] | None = Field(None, description="References to firewall resource names for network security")
    dns_zones: list[str] | None = Field(
        None,
        description="References to DNS document names this resource is associated with (mirrors `firewalls` "
        "above). A structural, provisioning-time fact — not itself a claim of ${output:...} ownership; see "
        "`WorkspaceSpecModel.validate_execution()`'s `target_names` for the separate claiming mechanism.",
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
        description="Workspace-specific additions to this resource's custom_tags (see ResourceSpecModel.custom_tags).",
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


class TopologyComponentModel(PlatformBaseModel):
    """A resource reference belonging to a topology, optionally with modules attached.

    ``modules`` attaches application code directly to this resource (e.g. an
    Azure Function App's function code onto its Function App resource) —
    no container-orchestration namespace involved. Uses the same
    `ModuleReferenceModel` as `Namespace.spec.modules`: both are ultimately
    "a pointer to a Module document plus placement metadata," just attached
    at different levels (namespace grouping vs. a single resource).
    """

    resource: PlatformName = Field(
        ..., description="Resource name reference (must exist in this workspace's spec.resources)"
    )
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
            raise ValueError(f"Resource '{self.resource}' has multiple enabled modules but no 'main' slot defined")
        if len(main_slots) > 1:
            raise ValueError(
                f"Resource '{self.resource}' has multiple modules marked as 'main' slot: {[m.name for m in main_slots]}"
            )
        return self


class TopologyNamespaceReferenceModel(PlatformBaseModel):
    """A namespace reference belonging to a topology."""

    namespace: PlatformName = Field(
        ..., description="Namespace name reference (must exist in this workspace's spec.namespaces)"
    )


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
    """A named grouping of resources and namespaces that conceptually belong
    together (e.g. "the AKS cluster + its blob store + its key vault"), for
    documentation, diagramming, and workload placement. It deliberately has
    **no** `provider`/`provisioner` field: which tool builds/deploys these
    resources, and in what order, is a separate concern
    (`ProvisionerModel`/`ProvisioningStepModel`) — a topology can be built
    and configured by several different provisioners acting on different
    subsets of it, or a single provisioner can span several topologies.
    Binding "grouping" and "tooling" together was v1's design; see ADR-0011.

    Inline on `WorkspaceSpecModel.topology` (ADR-0028, reverted from a
    standalone `kind: topology` document, ADR-0011) — `components[].resource`/
    `namespaces[].namespace` only ever resolve against *this* workspace's own
    `spec.resources`/`spec.namespaces`, one workspace at a time, so a
    standalone document could never validate its own references in
    isolation; see ADR-0028 for the full evidence.
    """

    name: PlatformName = Field(description="Unique topology name within this workspace")
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
        check_unique_names([c.resource for c in self.components], f"resource references in topology '{self.name}'")
        return self

    @model_validator(mode="after")
    def validate_unique_namespace_refs(self) -> "TopologySpecModel":
        """Validate that namespace references are unique within this topology."""
        if self.namespaces:
            check_unique_names(
                [n.namespace for n in self.namespaces], f"namespace references in topology '{self.name}'"
            )
        return self

    @model_validator(mode="after")
    def validate_unique_volume_names(self) -> "TopologySpecModel":
        """Validate that volume names are unique within this topology."""
        if self.volumes:
            check_unique_names([v.name for v in self.volumes], f"volume names in topology '{self.name}'")
        return self


def expand_topology_members(
    topologies_by_name: dict[str, TopologySpecModel], topology_names: list[str]
) -> tuple[set[str], set[str]]:
    """A named topology's own `components[]`/`namespaces[]` members
    (docs/design/workspace-execution-model.md).

    An unknown `name` is silently skipped rather than raising — whether a
    `topologies:` entry actually names a real topology is a separate,
    independent concern (`WorkspaceSpecModel.validate_execution()`'s own
    pool-existence check), and this function must stay safe to call before
    that check has run (both run from the same `validate_execution()`,
    with no guaranteed ordering between them)."""
    resources: set[str] = set()
    namespaces: set[str] = set()
    for name in topology_names:
        topo = topologies_by_name.get(name)
        if topo is None:
            continue
        resources |= {c.resource for c in topo.components}
        namespaces |= {n.namespace for n in (topo.namespaces or [])}
    return resources, namespaces


def effective_resources_and_namespaces(
    step: ProvisioningStepModel,
    resources_by_name: dict[str, WorkspaceResourceModel],
    topologies_by_name: dict[str, TopologySpecModel],
) -> tuple[set[str], set[str]]:
    """A step's real provisioning set: direct + topology-expanded
    (docs/design/workspace-execution-model.md).

    `resources_by_name` is deliberately built EXCLUDING any resource with
    `enabled=False` (a disabled resource behaves as if absent from the
    document entirely) — intersecting against its keys here means a
    disabled resource silently drops out of the effective set the same way
    it already drops out of the real Terraform payload, and makes every
    `resources_by_name[name]` lookup downstream (`claimed_names()` below)
    provably safe, never a bare/unchecked index.
    """
    topo_resources, topo_namespaces = expand_topology_members(topologies_by_name, step.topologies or [])
    resources = (set(step.resources or []) | topo_resources) & resources_by_name.keys()
    return resources, (set(step.namespaces or []) | topo_namespaces)


def claimed_names(
    step: ProvisioningStepModel,
    resources_by_name: dict[str, WorkspaceResourceModel],
    topologies_by_name: dict[str, TopologySpecModel],
) -> set[str]:
    """The dns/firewall/network claimed set for `${output:...}` ownership
    (docs/design/workspace-execution-model.md).

    Deliberately scoped to dns/firewall/network only, NOT namespaces —
    mirrors this codebase's own established principle that the two
    claiming mechanisms (`WorkspaceService.claimed_document_names()`'s
    broad vocabulary vs. `deploy_controller.py`'s narrower dns/network/
    firewall-only one) stay separate rather than being unified. Namespace
    claiming's topology extension lives in `claimed_document_names()`
    itself — a second, independent union term, not folded in here.
    """
    resources_eff, _ = effective_resources_and_namespaces(step, resources_by_name, topologies_by_name)
    claimed: set[str] = set(step.dns_zones or []) | set(step.firewalls or []) | set(step.networks or [])
    for name in resources_eff:
        resource = resources_by_name[name]  # safe: resources_eff is a subset of resources_by_name.keys()
        claimed |= set(resource.firewalls or []) | set(resource.dns_zones or [])
        if resource.subnet is not None:
            claimed.add(resource.subnet.network)
    return claimed


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


class WorkspaceProviderModel(PlatformBaseModel):
    """A named provider slot with its default Provider document
    (docs/design/provider-slots.md).

    `slot` is the stable key IaC code addresses (`strata.providers.<slot>`);
    `provider` is the document bound by default. A deployment may rebind the
    slot to another Provider document of the same type, so the same workspace
    can target a second region/account without its IaC code changing.

    A bare string is shorthand for a slot named after its own document —
    `azure-main` means `{slot: azure-main, provider: azure-main}` — so a
    workspace that never rebinds anything reads exactly as before.
    """

    model_config = ConfigDict(json_schema_extra=scalar_shorthand_schema(PlatformName))

    slot: PlatformName = Field(description="Stable slot name; IaC addresses the provider as strata.providers.<slot>")
    provider: Annotated[PlatformName, References(PlatformKind.PROVIDER)] = Field(
        description="Name of the Provider document bound to this slot by default"
    )

    @model_validator(mode="before")
    @classmethod
    def coerce_scalar_shorthand(cls, value: Any) -> Any:
        """Treat a bare string as `{slot: <string>, provider: <string>}`."""
        if isinstance(value, str):
            return {"slot": value, "provider": value}
        return value


class WorkspaceAssetModel(PlatformBaseModel):
    """A named asset slot with its default Asset document (docs/work/asset-slots.md).

    `slot` is the stable key a step selects (`step.assets`) and IaC addresses
    (`strata.assets.<slot>`, `build_path/assets/<slot>/`); `asset` is the
    document bound by default. A bare string is shorthand for a slot named
    after its own document, so a workspace that never rebinds anything reads
    exactly as before. Same shape and rule as `WorkspaceProviderModel`.
    """

    model_config = ConfigDict(json_schema_extra=scalar_shorthand_schema(PlatformName))

    slot: PlatformName = Field(description="Stable slot name; steps select it and IaC addresses strata.assets.<slot>")
    asset: Annotated[PlatformName, References(PlatformKind.ASSET)] = Field(
        description="Name of the Asset document bound to this slot by default"
    )

    @model_validator(mode="before")
    @classmethod
    def coerce_scalar_shorthand(cls, value: Any) -> Any:
        """Treat a bare string as `{slot: <string>, asset: <string>}`."""
        if isinstance(value, str):
            return {"slot": value, "asset": value}
        return value


class WorkspaceSpecModel(PlatformBaseModel):
    """Workspace specification: the static declaration of everything a solution needs.

    No `references` field (ADR-0002). No `WorkspaceIacModel`-style embedded
    provisioner-topology binding (ADR-0011) — `topology` is a list of inline
    `TopologySpecModel` groupings (unlike v1, deliberately **optional**,
    since a workspace may have resources with no grouping concept at all;
    unlike ADR-0011's original design, inline rather than a standalone-kind
    reference — see ADR-0028), and the actual build/deploy recipe is
    `execution: list[ProvisioningStepModel]`, fully decoupled from
    `topology`.

    The three sibling keys are deliberately distinct words, since they answer
    different questions: `providers` (**where** — target platform/account),
    `provisioners` (**with what** — tool definitions), `execution` (**what
    runs, in what order**).

    Every reference below (other than `topology`, inline since ADR-0028) is
    a document **name**, resolved by discovery against the `(kind,
    meta.name)` index — not a file path (see the note above
    `WorkspaceResourceSubnetModel`).
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

    providers: list[WorkspaceProviderModel] = Field(
        ...,
        min_length=1,
        description="Provider slots: a Provider document name, or {slot, provider} to give the slot a stable "
        "name a deployment can rebind (docs/design/provider-slots.md)",
    )
    provisioners: list[ProvisionerModel] = Field(..., min_length=1, description="Provisioner (tool) definitions")
    execution: list[ProvisioningStepModel] | None = Field(
        None,
        description="The ordered recipe: steps binding a Provisioner to a set of targets. Named 'execution' "
        "rather than 'provisioning' so it cannot be confused with the sibling 'provisioners' (tool "
        "definitions) or with the 'deployment' kind.",
    )
    output_store: PlatformName | None = Field(
        None,
        description="Key/value backend TYPE for cross-run step-output persistence (e.g. 'azure-keyvault', "
        "'azure-appconfig', 'azure-blob') — auto-binds to zero-or-one enabled Integration document of this "
        "type, same rule as a Provisioner's bare 'tool' (ADR-0022 D2). Omit to disable cross-run output "
        "persistence entirely (today's in-memory-only behavior). Checked against Configuration.spec.security."
        "allowed_key_value_stores when set (docs/work/cross-run-step-output-delivery.md).",
    )
    output_store_integration: Annotated[PlatformName, References(PlatformKind.INTEGRATION)] | None = Field(
        None,
        description="Explicit Integration document name for the output-store backend, overriding auto-bind "
        "— same precedence as a Provisioner's '.integration' field winning over '.tool'.",
    )
    output_store_max_age: int | None = Field(
        None,
        ge=1,
        description="Trust window in seconds for durable cross-run step outputs read back via "
        "'output_store'/'output_store_integration' — a record older than this is a hard 'deploy run' "
        "failure, never a silent stale read. None (default) disables staleness enforcement entirely "
        "(today's behavior). 'deploy run --output-max-age' overrides this for a single invocation.",
    )
    topology: list[TopologySpecModel] | None = Field(
        None,
        description="Inline topology groupings (type, components, namespaces, volumes) — pure grouping, no "
        "provider/provisioner field (ADR-0011). Inline rather than a standalone-document reference "
        "(ADR-0028, reverted from the original standalone-kind design of ADR-0011).",
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
    assets: list[WorkspaceAssetModel] | None = Field(
        None,
        description="Asset slots this workspace may materialise into a build (docs/design/asset-management.md, "
        "docs/work/asset-slots.md): an Asset document name, or {slot, asset} to give the slot a stable name. "
        "A step claims which slots it needs via its own 'assets' field, cross-checked in "
        "validate_execution() below (a second, independent set from 'target_names' — an asset is "
        "materialised input, not a provisioning target).",
    )
    cve_policy: CveMaxSeverityPolicyModel | None = Field(
        None,
        description="Gates 'build run' on CVE findings in the SBOM it just generated (docs/archive/"
        "cve-scanner-integration.md). Lives here, not on Deployment, because it is a build-phase "
        "concern — the same scope `execution`/`provisioners` already own — not a per-deployment one.",
    )
    checkov_policy: CheckovPolicyModel | None = Field(
        None,
        description="Gates 'build run' on Checkov static-IaC-security findings for this workspace's "
        "terraform/bicep/ansible provisioners, or its Helm namespace modules (docs/archive/"
        "checkov-integration.md). Same placement reasoning as cve_policy — a build-phase concern, "
        "not a per-deployment one.",
    )

    @model_validator(mode="after")
    def validate_unique_names(self) -> "WorkspaceSpecModel":
        """Validate that names are unique within each list."""
        check_unique_names([p.slot for p in self.providers], "provider slots")
        check_unique_names([p.name for p in self.provisioners], "provisioner names")
        if self.topology:
            check_unique_names([t.name for t in self.topology], "topology names")
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
        if self.assets:
            check_unique_names([a.slot for a in self.assets], "asset slots")
        return self

    @model_validator(mode="after")
    def validate_topology_component_resource_references(self) -> "WorkspaceSpecModel":
        """Validate that each topology's component resource references exist in this
        workspace's own `spec.resources` (ADR-0011/ADR-0028).

        A same-document Phase 1 check — possible only because topology is now
        inline; see `TopologySpecModel`'s docstring and ADR-0028 for why this
        could never be checked from a standalone Topology document alone.
        """
        if not self.topology:
            return self
        resource_names = {r.name for r in (self.resources or [])}
        errors = []
        for topo in self.topology:
            for component in topo.components:
                if component.resource not in resource_names:
                    errors.append(
                        f"Topology '{topo.name}': component references undefined resource '{component.resource}'"
                    )
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def validate_topology_namespace_references(self) -> "WorkspaceSpecModel":
        """Validate that each topology's namespace references exist in this
        workspace's own `spec.namespaces` (ADR-0011/ADR-0028)."""
        if not self.topology:
            return self
        namespace_names = set(self.namespaces or [])
        errors = []
        for topo in self.topology:
            for ns_ref in topo.namespaces or []:
                if ns_ref.namespace not in namespace_names:
                    errors.append(f"Topology '{topo.name}': references undefined namespace '{ns_ref.namespace}'")
        if errors:
            raise ValueError("; ".join(errors))
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
    def validate_resource_dns_references(self) -> "WorkspaceSpecModel":
        """Validate that resource DNS references exist in this workspace's dns_zones (mirrors
        validate_resource_firewall_references above)."""
        if not self.resources:
            return self
        dns_names = set(self.dns_zones) if self.dns_zones else set()
        errors = []
        for resource in self.resources:
            for dns_zone in resource.dns_zones or []:
                if dns_zone not in dns_names:
                    errors.append(f"Resource '{resource.name}' references undefined DNS zone '{dns_zone}'")
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
        cross-references against this workspace's own provisioners/resources/
        namespaces/topology/dns_zones/networks/firewalls.

        Each of the six target-ish fields on a step (`resources`/
        `namespaces`/`topologies`/`dns_zones`/`firewalls`/`networks`) is
        checked against its own matching pool only
        (docs/design/workspace-execution-model.md) — a resource name is
        never silently accepted as a valid dns_zone just because both pools
        happen to share a name, unlike the old single flat `target_names`
        union this replaces.

        The shared-target ordering rule (`validate_provisioning_steps()`)
        now runs against each step's fully *effective* touched-name set —
        topology-expanded resources/namespaces plus resource-derived
        dns/firewall/network claiming (point 8), not just a step's own
        directly-declared fields — two steps sharing a target become
        ambiguous even when that sharing is only indirect (e.g. both acting
        on resources that reference the same firewall).

        `step.assets` (docs/design/asset-management.md Phase 2) is checked
        against a second, independent `asset_slots` set below — deliberately
        NOT folded into any of the six fields above: an asset is
        materialised input a step consumes, not a provisioning target a
        step "acts on".
        """
        if not self.execution:
            return self

        resources_by_name = {r.name: r for r in (self.resources or []) if r.enabled}
        topologies_by_name = {t.name: t for t in (self.topology or [])}
        touched_names_by_step = {}
        for step in self.execution:
            resources_eff, namespaces_eff = effective_resources_and_namespaces(
                step, resources_by_name, topologies_by_name
            )
            claimed = claimed_names(step, resources_by_name, topologies_by_name)
            touched_names_by_step[step.name] = resources_eff | namespaces_eff | claimed

        validate_provisioning_steps(self.execution, touched_names_by_step)

        provisioner_names = {p.name for p in self.provisioners}
        resource_names = {r.name for r in (self.resources or [])}
        namespace_names = set(self.namespaces or [])
        topology_names = {t.name for t in (self.topology or [])}
        dns_names = set(self.dns_zones or [])
        firewall_names = set(self.firewalls or [])
        network_names = set(self.networks or [])
        asset_slots = {a.slot for a in (self.assets or [])}

        errors = []
        for step in self.execution:
            if step.provisioner not in provisioner_names:
                errors.append(f"Execution step '{step.name}' references undefined provisioner '{step.provisioner}'")
            for resource in step.resources or []:
                if resource not in resource_names:
                    errors.append(f"Execution step '{step.name}' references undefined resource '{resource}'")
            for namespace in step.namespaces or []:
                if namespace not in namespace_names:
                    errors.append(f"Execution step '{step.name}' references undefined namespace '{namespace}'")
            for topology in step.topologies or []:
                if topology not in topology_names:
                    errors.append(f"Execution step '{step.name}' references undefined topology '{topology}'")
            for dns_zone in step.dns_zones or []:
                if dns_zone not in dns_names:
                    errors.append(f"Execution step '{step.name}' references undefined dns_zone '{dns_zone}'")
            for firewall in step.firewalls or []:
                if firewall not in firewall_names:
                    errors.append(f"Execution step '{step.name}' references undefined firewall '{firewall}'")
            for network in step.networks or []:
                if network not in network_names:
                    errors.append(f"Execution step '{step.name}' references undefined network '{network}'")
            for asset in step.assets or []:
                if asset not in asset_slots:
                    errors.append(f"Execution step '{step.name}' references undefined asset '{asset}'")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def validate_provisioner_depends_on(self) -> "WorkspaceSpecModel":
        """Validate `ProvisionerModel.depends_on` (ADR-0029): every name must
        reference a real sibling in `self.provisioners`, and the resulting
        graph must be acyclic.

        A same-document Phase 1 check, same shape as `validate_execution()`'s
        own `step.provisioner`/`depends_on` cross-checks above — a lone
        `ProvisionerModel` has no sibling awareness (confirmed:
        `test_provisioner_accepts_depends_on()` validates a `depends_on` name
        with zero existence checking), so this can only be done here, once
        every provisioner in the workspace is loaded together.

        Deliberately independent of `validate_execution()`/
        `validate_provisioning_steps()` above: a provisioner's `depends_on`
        names other *provisioners* (materialisation order), never *steps*
        (execution order) — a provisioner with no execution step at all is
        not only allowed here, it's the entire point of ADR-0029 (no
        borrowed `targets:`/fake step needed just to get materialised).
        """
        names = {p.name for p in self.provisioners}
        errors = []
        for provisioner in self.provisioners:
            for dep in provisioner.depends_on or []:
                if dep not in names:
                    errors.append(
                        f"Provisioner '{provisioner.name}': depends_on '{dep}' is not a declared provisioner."
                    )
        if errors:
            raise ValueError("; ".join(errors))

        topological_order(
            names, {p.name: p.depends_on or [] for p in self.provisioners}, label="provisioner depends_on"
        )
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
