# Workspace Execution Model

A `WorkspaceSpecModel.execution` step (`ProvisioningStepModel`) binds a
Provisioner to a set of documents it acts on. This doc covers that
binding's final shape — the six target-ish fields, how a step's
*effective* set is derived from direct references plus topology
membership, and how dns/firewall/network `${output:...}` claiming is
derived from a step's effective resources — plus the handful of related
questions settled alongside it (step ordering, CLI scope filtering,
multi-root IaC composition, resource reuse across topologies).

## Schema

```python
class ProvisioningStepModel(PlatformBaseModel):
    name: PlatformName
    provisioner: PlatformName
    resources: list[PlatformName] | None = None    # direct Resource name references
    namespaces: list[PlatformName] | None = None   # direct Namespace name references
    topologies: list[PlatformName] | None = None   # expands to that topology's own resources/namespaces
    dns_zones: list[PlatformName] | None = None    # direct escape hatch — not resource-derivable
    firewalls: list[PlatformName] | None = None    # same
    networks: list[PlatformName] | None = None     # same
    assets: list[PlatformName] | None = None       # build-input materialisation, independent pool
    depends_on: list[PlatformName] | None = None
    scope: str | None = None
```

At least one of the six target-ish fields is required per step
(`validate_at_least_one_target`). Each field's names are checked for
uniqueness independently (`validate_unique_targets`), and cross-checked
at the `WorkspaceSpecModel` level against its own matching pool only
(`resources` against `spec.resources`, `namespaces` against
`spec.namespaces`, `topologies` against `spec.topology`, and so on) —
never against a combined union, so a resource name can never be accepted
where a dns zone name was meant. `assets` stays a fully independent
field/pool (docs/design/asset-management.md): materialised build input a
step consumes, not a provisioning target a step acts on.

`WorkspaceResourceModel` carries the structural facts a step's effective
resources can derive from: `firewalls: list[str] | None`,
`dns_zones: list[str] | None`, and `subnet: WorkspaceResourceSubnetModel
| None` (whose `.network` names the resource's network). All three are
cross-checked in Phase 1 against the matching workspace-level pool
(`validate_resource_firewall_references()`, `validate_resource_dns_
references()`, `validate_resource_subnet_references()`).

`dns_zones` is projected into the Terraform payload alongside `firewalls`/
`subnet` (`terraform_projection.py`'s `_strata_resources()`, and
`strata_variables.tf`'s generated `resources` object type) — closed once
ADR-0035's unified projection landed; previously inert (validated, never
read) while the old per-category projection was still in place.

## Derivation: a step's effective resources/namespaces

`topologies:` lets a step act on everything a topology groups without
re-listing every member resource/namespace by hand — a topology can still
be built/configured by several different provisioners acting on
different subsets of it, or a single provisioner can span several
topologies (ADR-0011); naming it under `topologies:` doesn't reintroduce
a 1:1 step↔topology binding, it's just shorthand for "this step's
effective set includes every one of this topology's members."

```python
def expand_topology_members(
    topologies_by_name: dict[str, TopologySpecModel], topology_names: list[str],
) -> tuple[set[str], set[str]]:
    """A named topology's own components[]/namespaces[] members. An
    unknown name is silently skipped (not an error here) — that's a
    separate, independent pool-existence check elsewhere."""

def effective_resources_and_namespaces(
    step: ProvisioningStepModel,
    resources_by_name: dict[str, WorkspaceResourceModel],
    topologies_by_name: dict[str, TopologySpecModel],
) -> tuple[set[str], set[str]]:
    """A step's real provisioning set: direct `resources`/`namespaces`
    plus topology-expanded members. `resources_by_name` is built
    EXCLUDING any resource with `enabled=False` — a disabled resource
    behaves as if absent from the document entirely, the same rule the
    real Terraform payload already applies, not merely "skip at build
    time." Intersecting the effective resource set against
    `resources_by_name.keys()` is what makes every downstream
    `resources_by_name[name]` lookup (`claimed_names()` below) provably
    safe, never a bare/unchecked index."""
```

## Derivation: dns/firewall/network claiming

A document is "claimed" once some execution step owns it for
`${output:...}` token resolution (the original mechanism: ADR gap #12 in
`gap_fit_v1.md`). A targeted/topology-expanded resource's own
`firewalls`/`dns_zones`/`subnet.network` are claimed automatically —
no need to also list them on the step — because the two facts ("this
step builds the resource" and "this resource's firewall needs owning")
are the same fact in practice, not two independently-maintained ones.

```python
def claimed_names(
    step: ProvisioningStepModel,
    resources_by_name: dict[str, WorkspaceResourceModel],
    topologies_by_name: dict[str, TopologySpecModel],
) -> set[str]:
    """The dns/firewall/network claimed set — the union of (a) every
    effective resource's own firewalls/dns_zones/subnet.network, and
    (b) whatever is still directly listed on the step's own dns_zones/
    firewalls/networks fields (the escape hatch for a document with no
    resource reference at all, e.g. a dns zone whose only record is a
    literal IP). Deliberately scoped to dns/firewall/network only, not
    namespaces — the two claiming mechanisms
    (`WorkspaceService.claimed_document_names()`'s broad vocabulary vs.
    this narrower dns/network/firewall-only one) stay separate rather
    than being unified."""
```

Namespace claiming (`WorkspaceService.claimed_document_names()`) extends
symmetrically: a namespace reached only through `topologies:` is claimed
exactly as if listed directly under `namespaces:` — the same "all items
can be found through the same type of union" principle applied to both
resources and namespaces, not just one.

The shared-target ordering rule (`validate_provisioning_steps()`,
ADR-0011 — two steps sharing a target need an explicit `depends_on` edge
between them, a hard error otherwise) now operates over each step's full
*effective* touched-name set (direct + topology-expanded + resource-
derived claims), not just its directly declared fields: two steps can
become ambiguous through shared *indirect* membership (e.g. both naming
resources that reference the same firewall) where they weren't before.
`provisioning_model.py` itself has no dependency on `workspace_model.py`'s
types (would be circular — `workspace_model.py` already imports from
`provisioning_model.py`, ADR-0003 layering): `validate_provisioning_
steps()` instead accepts an already-computed `touched_names_by_step:
dict[str, set[str]] | None`, built by its one real caller
(`WorkspaceSpecModel.validate_execution()`) from the two functions above.
Omitted entirely (e.g. a direct unit test constructing a bare
`ProvisioningStepModel` with no workspace context), it falls back to each
step's own directly-declared fields.

## Call sites

- `provisioning_model.py` — `ProvisioningStepModel`'s own per-field
  validators; `validate_provisioning_steps()`'s shared-target rule.
- `workspace_model.py`'s `WorkspaceSpecModel.validate_execution()` — six
  separate pool-existence checks, one per target-ish field; builds
  `resources_by_name`/`topologies_by_name` and threads the derived
  `touched_names_by_step` into `validate_provisioning_steps()`.
- `workspace_service.py`'s `WorkspaceService.claimed_document_names()` —
  namespaces (direct + topology-expanded) unioned with `claimed_names()`.
- `deploy_controller.py` — the claiming loop (`claimed_by_category`), the
  container-capable-step namespace lookup, and the per-document claim
  filter in the Terraform payload rewrite, all three now built from
  `effective_resources_and_namespaces()`/`claimed_names()` rather than a
  flat `.targets` list.
- `semantic_checks.py`'s `_documents_reachable_from_workspace()` needs no
  code of its own — it calls `claimed_document_names()`, never `.targets`
  directly.

## Related Decisions

- [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md) —
  Topology and Provisioning are independently referenced, n:m, never
  bound 1:1; the shared-target-needs-`depends_on` rule this doc's
  derivation now operates over a wider input for.
- [ADR-0039](../decisions/0039-provisioning-step-target-fields-by-kind.md)
  — the decision to split `targets` by kind and derive dns/firewall/
  network claiming from topology/resource references (this doc is its
  "how it works" counterpart).
- [gap_fit_v1.md](../work/gap_fit_v1.md) gap #12 — the original
  dns/network/firewall claiming mechanism this doc's `claimed_names()`
  generalizes.
- [asset-management.md](asset-management.md) — `step.assets` independently
  converged on the same "pool on `WorkspaceSpecModel`, selection on
  `ProvisioningStepModel`" shape this doc's six target-ish fields use.

## History

- `ProvisionerModel.depends_on` (a list of *provisioner* names) is the
  correct, already-existing mechanism for multi-root IaC composition
  where a dependency is pure library code with no independent
  `apply`/state (e.g. `primitives`/`components` composed into a `stack`
  via relative Terraform `source` paths) — confirmed against a real
  workspace (`config-deploy`'s `stacks/spoke/workspace.yaml`). No new
  execution step is needed for the dependency itself, only for whatever
  actually gets `apply`'d. The alternative shape — a dependency with its
  own real `apply`/state whose *outputs* (not just its source) are
  needed — is a different problem entirely (cross-stack state access,
  conventionally `terraform_remote_state`, never strata's own
  `${output:...}` mechanism, which exists to inject values into strata's
  own schema fields, not for Terraform-to-Terraform composition) and
  needs no schema change either, just separate execution steps ordered
  via `depends_on`.
- Binding a step's targets *to* a topology (rather than letting
  `topologies:` expand into resources/namespaces) was considered and
  rejected — it would force a 1:1 step↔topology relationship, destroying
  the deliberate n:m flexibility ADR-0011 and ADR-0028 both already
  established for independent reasons (ADR-0028 killed standalone
  Topology documents after finding zero real cross-workspace reuse).
- A `WorkspaceResourceModel` reused as a component across two *different*
  topologies already worked before this doc's changes and needed no
  fix — `validate_unique_component_resources()`'s uniqueness check was
  already scoped per-topology, not global.
- Embedding the topology-expansion shorthand as a structured entry
  *inside* `resources` itself (e.g. `resources: ["vm-1", {topology:
  aks}]`) was considered and rejected — it would turn a flat
  `list[PlatformName]` into a union type, rippling into every place that
  treats it as plain strings (`check_unique_names`, the shared-target
  ordering rule, `claimed_names()`'s set operations) for no benefit a
  sibling `topologies:` field doesn't already get for free.
- Two unrelated design efforts (this one, and `asset-management.md`'s
  `assets` field) independently converged on the identical "pool on
  `WorkspaceSpecModel`, selection on `ProvisioningStepModel`" shape —
  `asset-management.md` first tried attaching `assets` to
  `ProvisionerModel` and rejected that specifically because it didn't
  match the pattern this doc's `namespaces`/`firewalls`/`dns_zones`/
  `networks` pools + step-level selection already establishes.
- Real migration risk, worth remembering for any future consumer
  migration: once resource/topology-derived claiming is live, two
  previously-independent steps can newly *share* a derived name they
  didn't share before (e.g. two steps whose resources both reference the
  same firewall, or two steps whose `topologies:` names the same
  topology and therefore share every one of its namespaces) — the
  existing shared-target rule is a hard error, so a workspace that
  validated cleanly before migrating to the split fields can start
  failing `strata validate` with no change to its content, only its
  shape. Not yet observed in this repo's own `config/` (its one
  workspace has a single execution step with no sharing), but flag it
  explicitly before migrating any real multi-step/multi-topology
  consumer workspace (`haven`/`config-deploy`).
