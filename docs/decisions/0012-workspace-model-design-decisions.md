# Workspace Model — v2 Design Decisions

- Status: partially-implemented — model and thin service built; Phase 2
  Topology cross-checking and `Deployment` (the "container instance") not
  started (see Remaining Work)
- Date: 2026-09-21
- Related: [ADR-0011](0011-topology-and-provisioning-decoupling.md) (the
  whole conceptual design this model implements — image/container framing,
  Topology/Provisioning decoupling, `ProvisionerModel`/`ProvisioningStepModel`),
  [ADR-0015](0015-solution-manifest-and-document-discovery.md) (**supersedes
  the reference shape below**: the `{name, file}` wrapper models are deleted
  and `spec.providers`/`namespaces`/`firewalls`/`dns_zones`/`networks`/
  `topology` are now plain name lists; `WorkspaceResourceModel.file` became
  `.resource`, naming the Resource document an instance is built from)

## Context and Problem Statement

`WorkspaceModel` (`src/strata/models/workspace_model.py`,
`src/strata/services/workspace_service.py`) is the ninth v2 kind — the
"image" that ties every other kind together into one deployable solution.
Built directly on ADR-0011's conclusions rather than a field-by-field v1
port: Topology stays a separate, standalone kind referenced by pointer;
`ProvisionerModel`/`ProvisioningStepModel` (already built) are embedded as
the provisioning recipe; module attachment is exclusively `Topology`'s job,
not duplicated here.

## Decisions

### 1. Simple `{name, file}` pointers for every standalone kind

`WorkspaceProviderModel`, `WorkspaceNamespaceModel`, `WorkspaceFirewallModel`,
`WorkspaceDnsModel`, `WorkspaceNetworkModel`, `WorkspaceTopologyModel` are all
the same shape — a name plus a file path to the real document. No embedded
content, matching every kind's own promotion to a standalone, independently
loadable document.

### 2. `topology` is optional, diverging deliberately from v1

v1's `WorkspaceSpecModel.topology` was `Annotated[List[...], Field(min_length=1)]`
— required, at least one. Given ADR-0011's redesign (Topology is pure,
optional grouping metadata, not an execution concept), a workspace with
resources but no meaningful "shape" grouping (e.g. one standalone VM) should
be allowed to omit it entirely. Made `list[WorkspaceTopologyModel] | None`.

### 3. `WorkspaceResourceModel` has no `modules` field

v1's real `WorkspaceResourceModel.modules` (`WorkspaceModuleReferenceModel`)
attached modules to a resource at the workspace-gluing layer. Since
`TopologyComponentModel.modules` (built earlier, same session) already
covers exactly this case, adding it here too would recreate the same
two-places-for-one-fact duplication already resolved for
`NamespaceModuleModel`/`ModuleReferenceModel` (ADR-0010/0011). Confirmed
explicitly before building this model: module attachment is
`TopologyComponentModel`'s job exclusively. A workspace wanting to attach a
module to a resource must declare a topology for it — deliberate, not an
oversight.

### 4. `ProvisionerModel`/`ProvisioningStepModel` embedded directly

`spec.provisioners: list[ProvisionerModel]` (required, min 1 — matches v1's
requirement that a workspace declares at least one provisioner) and
`spec.provisioning: list[ProvisioningStepModel] | None` (new; v1 had no
recipe-level concept at all, only per-topology `provisioner`/`topology`
fields — see ADR-0011). `validate_provisioning_steps()` (already built,
`provisioning_model.py`) is called directly from `WorkspaceSpecModel`'s own
model_validator — exactly the pattern anticipated in ADR-0011's Remaining
Work.

### 5. Provisioning recipe cross-references — Phase 1, not Phase 2

Unlike Topology's internal references (which need a separate file load),
`ProvisioningStepModel.provisioner` and `.targets` can be validated
**self-contained, Phase 1** — `provisioners`, `resources`, and `namespaces`
are all sibling fields in the same `WorkspaceSpecModel` document. Added
`validate_provisioning()`: checks `step.provisioner` resolves to a real
`ProvisionerModel.name`, and every `step.targets` entry resolves to either a
real `WorkspaceResourceModel.name` or `WorkspaceNamespaceModel.name`.

### 6. `WorkspaceResourceModel` ported from v1 mostly unchanged

`file`/`managed_by` mutual exclusivity, `enabled`/`role`/`count`,
`depends_on` (string-to-list coercion kept), `firewalls` (validated against
`spec.firewalls`, self-contained Phase 1 — matches v1's
`validate_firewall_references`), `configuration`/`custom`/`labels`/`tags`.
No `references` field (ADR-0002 — v1 didn't have one here anyway). `subnet`
was NOT ported unchanged — see Decision 7.

### 7. DNS/Network/Firewall integration into Workspace — investigated against v1's real runtime code, not just its schema

The user asked how DNS, Network, and Firewall actually integrate with
Workspace/Resource, noting v1 "never really did this properly." Checked
v1's builders/controllers/services (not just the models) to find out what's
real versus decorative:

- **Firewall**: genuinely wired — `resource.firewalls[]` is checked against
  `spec.firewalls[].name` (v1's `validate_firewall_references`). Ported
  unchanged as `validate_resource_firewall_references`.
- **DNS**: v1's own `dns_service.py` states outright — *"DNS has no
  cross-reference validation — self-contained."* DNS zones are global domain
  configuration, never attached to a specific resource or topology; they
  flow straight into build output (`platform.spec.dns_zones`) and are
  consumed generically via `var:`/`secret:`/`output_key` tokens. This is a
  deliberate design, not a gap — no resource-level DNS field was added here,
  matching it.
- **Network / `subnet`**: the one real gap. v1's
  `WorkspaceResourceModel.subnet: Optional[str]` ("qualified format
  'network_name/subnet_name'") was **never validated anywhere** — the only
  code that even parsed it was `graph_controller.py`, which splits on `/`
  for a best-effort diagram edge with no existence check. It was also
  ambiguous which name `network_name` was meant to be: the workspace-level
  pointer (`WorkspaceNetworkModel.name`) or the internal
  `NetworkDefinitionModel.name` (one network file's `spec.networks[]` can
  define several named networks).

  Fixed in v2: replaced the delimited string with a structured
  `WorkspaceResourceSubnetModel {network, subnet}`. `network` is checked in
  Phase 1 (`validate_resource_subnet_references`) against this workspace's
  own `spec.networks[].name` — the same self-contained check used for
  `firewalls`. `subnet` can only be checked in Phase 2, once that Network
  document is actually loaded, against its real
  `NetworkDefinitionModel.subnets[]` — the same asymmetry already
  established for Topology's internal references (ADR-0011), noted in
  `WorkspaceService`'s docstring alongside the Topology Phase 2 item.

## Consequences

- Good: `Topology`/`Provisioning`'s decoupling (ADR-0011) holds up in
  practice — `WorkspaceSpecModel` cross-references provisioners/resources/
  namespaces for the provisioning recipe entirely in Phase 1, with zero
  coupling to `topology` at all.
- Good: no new duplication introduced — module attachment stayed exclusively
  on `TopologyComponentModel`, confirmed before building rather than after.
- Good: 20 model tests + 1 service test passed on the first implementation
  attempt — a good sign the incremental, ADR-driven design work (Topology,
  Provisioning, the shared `ModuleReferenceModel`) paid off before reaching
  the integration point.
- Neutral: `topology`'s internal references (`components[].resource`,
  `namespaces[].namespace`) still can't be checked against this workspace's
  own `resources`/`namespaces` without a Phase 2 file load — an accepted,
  already-documented consequence of promoting `Topology` to a standalone kind
  (ADR-0011).

## Remaining Work

- ~~`WorkspaceService`: cross-check each referenced `Topology` document's
  `components[].resource`/`namespaces[].namespace` against this workspace's
  own `resources`/`namespaces`, and its components' resolved
  `WorkspaceResourceModel.role` against the configuration's topology type
  registry~~ — done as `validate_topology_references()`/
  `validate_topology_components()`, both self-contained public methods a
  future solution-loading layer calls once it has resolved
  `spec.topology[].file` into real `TopologyModel` instances (not wired into
  `_validate_dynamic()` itself — that hook's fixed signature only threads a
  `configuration_model`, not the extra loaded `TopologyModel`s these checks
  also need). See [ADR-0013](0013-configuration-topology-registry.md).
- Same for each referenced `Network` document: cross-check
  `resource.subnet.subnet` against that network's real
  `NetworkDefinitionModel.subnets[]` (Decision 7) — still not ported.
- Extend `ConfigurationModel` with `topologies`/`ConfigurationTopologyModel`
  so a referenced Topology's `spec.type` can be checked against a registry
  (mirrors Provider's `type`/`region` check) — still not built.
- The `Deployment` kind (the "container instance") — references a Workspace
  + an Environment, executes the baked-in `ProvisioningStep` recipe via thin
  `DeploymentStage` entries (approval gates, secrets scope/Grant). Not
  started; `DeploymentStageModel.provisioner`/`.topology` must not be
  ported from v1 (that responsibility belongs entirely here now).
- Cross-repo reference convention inconsistency (`SourceModel.repository` vs.
  `@reponame/path`) still not resolved — noted again in ADR-0011, unchanged.
