# Workspace Execution Model — Review Notes

- Status: draft
- Last updated: 2026-10-10

## Overview

A brainstorm session re-examining `ProvisioningStepModel`/`ProvisionerModel`
(`provisioning_model.py`) and `WorkspaceResourceModel`/`TopologySpecModel`
(`workspace_model.py`) against real multi-root IaC composition scenarios —
not a response to a bug report, a deliberate "does the schema actually
support this" stress test. Points 1/2/3/6 confirmed no schema change
needed; point 4 is already a deliberate, documented decision elsewhere,
not revisited; points 5/7/8/9 are decided schema changes, 7/8/9 composed
into one combined `ProvisioningStepModel` schema (see point 9's final
field list and Remaining Work).

## 1. `depends_on` (step ordering) — confirmed, no change

`ProvisioningStepModel.depends_on` already expresses "this step must
complete before that one runs" (e.g. a `bootstrap-customer` step
provisioning a Key Vault before a later step that reads from it). No gap.

## 2. `scope` (CLI filtering label) — confirmed, no change

`ProvisioningStepModel.scope` is deliberately free-form, no fixed
vocabulary (matches v1). `"bootstrap"` is exactly as valid a label as
`"infra"`/`"apps"` today — no schema change needed to use it that way.

## 3. Multi-root IaC composition (`primitives` → `components` → stack) — resolved, no schema change

Initially proposed as "`ProvisioningStepModel.provisioner` should be a
list." Pushed back: this exact scenario is already evidenced in this
codebase (`source_sync.py`'s own docstring cites a real workspace,
`config-deploy`'s `stacks/spoke/workspace.yaml`, with a `core_modules`
copy-only provisioner staging a shared Terraform module library so the
real provisioner's `.tf` composes it via a relative path).

**Two existing, different mechanisms cover this, and which one applies
depended on a real question: do `primitives`/`components` have their own
independent state/lifecycle, or are they pure library code?** Answered
(2026-10-10), by the user's own description of the real layering:

- **`primitives`** — basic building blocks (e.g. a VNet module, a VM
  module). No `apply` of their own.
- **`components`** — *use* a primitive, adding RBAC and related
  components (private endpoints, etc.) around it. Still composition, not
  a separate `apply` — a component's own `.tf` has no backend/state block
  of its own, it only exists to be composed *into* something else.
- **`stack`** — uses *both* components and primitives together, and is
  the one thing that actually gets `terraform apply`'d — the real
  backend/state lives here, only here.

This is squarely the **pure library code, no separate apply/state** shape
already identified below — `primitives` and `components` are both
library code the `stack` composes via relative `source = "../../..."`
paths, never independently applied. **Resolved: `ProvisionerModel
.depends_on` (list of *provisioner* names, not step names) is the correct,
already-existing mechanism** — it materializes `primitives`'/`components`'
source alongside the `stack` provisioner's own, purely so its relative
module `source` paths resolve; no new execution step needed for
`primitives`/`components` themselves, only the `stack` gets one. This is
the exact mechanism the real `core_modules` example above already uses.
No schema change needed here.

The alternative shape (**independently-applied stacks**, where a
dependency needs its own real `terraform apply`/state and its
*outputs*, not just its source) remains documented below for
completeness, in case a *different* real scenario needs it later — not
applicable to the `primitives`/`components`/`stack` case just resolved:

- `primitives` gets its own real `terraform apply`/state;
  `ring`/`customer`/`spoke` need its *outputs*, not just its source →
  not a provisioner-list problem at all. Needs separate execution steps,
  `ProvisioningStepModel.depends_on` for apply ordering, and cross-stack
  state access — conventionally Terraform's own `terraform_remote_state`
  reading the same backend `provisioner.backend` already declares,
  **not** strata's `${output:...}` token mechanism (which exists to
  inject values into strata's own schema fields — a DNS record, a
  firewall rule — not for Terraform-to-Terraform composition).

## 4. `targets` should reference topologies instead of resources/namespaces

**Rejected — already a deliberate decision, not an oversight.**
`TopologySpecModel`'s own docstring: "a topology can be built and
configured by several different provisioners acting on different subsets
of it, or a single provisioner can span several topologies" — binding
`targets` to topologies would force a 1:1 step↔topology relationship,
destroying that deliberate n:m flexibility (ADR-0011). ADR-0028
independently killed standalone Topology documents for the same kind of
reason (zero real cross-workspace reuse found). Not revisiting without
new evidence.

## 5. `WorkspaceResourceModel` has no DNS-zone reference — confirmed real, decided and implemented

`WorkspaceResourceModel` had `firewalls: list[str]` and
`subnet: WorkspaceResourceSubnetModel` — a resource could declare which
firewalls protect it and which subnet it sits in — but **no DNS field at
all**. Grepped ADR-0005 (the DNS model's own design-decisions ADR) for any
discussion of this asymmetry — nothing. Unlike point 4 above, this was a
genuine gap nobody decided on either way, not a considered exclusion.

**Decided and implemented (2026-10-10):** added `dns_zones: list[str] |
None` to `WorkspaceResourceModel`, mirroring `firewalls` exactly, plus a
Phase 1 `validate_resource_dns_references()` cross-check against
`spec.dns_zones` (mirrors `validate_resource_firewall_references()`). A
resource can now self-declare `firewalls`/`dns_zones` directly and
`networks` indirectly via `subnet.network` — all three are structural,
provisioning-time facts, deliberately still distinct from claimed-output
ownership (`targets`) until point 8 is also decided.

**Known limitation, left as-is on purpose:** checked `terraform_projection
.py`'s `_merge_resource_entry()` directly — `firewalls`/`subnet` are both
already projected into the per-resource Terraform payload entry
(`"firewalls": workspace_resource.firewalls or []`, `"subnet": ...`), so a
provisioner's `.tf` code can actually read them today. `dns_zones` is
**not** projected anywhere yet — the field exists on the model and
validates, but is otherwise inert for every provisioner (dead data, no
consumer). Deliberately not wiring this up now: the projection's own
shape is expected to change regardless (`docs/decisions/0035-unified-
terraform-projection.md`'s payload redesign), so adding `dns_zones` to the
*current* `_merge_resource_entry()` would just be rewritten again shortly
after. Tracked in Remaining Work — project `dns_zones` once the unified
payload (or whichever projection shape lands) is actually being built.

## 6. A `WorkspaceResourceModel` reused across multiple topology components — confirmed already works

`WorkspaceSpecModel.validate_unique_component_resources()`'s uniqueness
check is scoped **per-topology** (`check_unique_names([c.resource for c in
topo.components], ...)`, run once per `topo` in the loop), not globally
across every topology in the workspace. So the same resource can already
appear as a component in two *different* topologies (e.g. a "customer
view" grouping and a "network view" grouping) today — nothing in the
model prevents it. No gap; confirmed correct as designed.

## 7. Topology membership never expands into a step's `targets` — confirmed, real friction, decided

Follow-on to point 4. Point 4 correctly rejected binding `targets` *to*
topologies (would force 1:1, destroying the deliberate n:m story). But a
separate, narrower question survives that rejection: **given** a step
wants full coverage of a topology (a simple, single-`apply` 100-resource
cluster, no partial/multi-tool split needed), does anything help it avoid
manually enumerating every member resource/namespace name? Checked — no:

- ADR-0011 itself: "'this step realizes topology X' becomes a **derived**
  fact (intersect a step's `targets` with a topology's resources), never a
  declared one."
- The migration guide, spelling out the practical consequence: `targets`
  — "List every `Resource`/`Namespace`/`Dns`/`Network`/`Firewall` **name**
  the topology's `components[]`/`namespaces[]` reference" — by hand,
  every time.

The derivation only ever runs *backward* (targets → "which topology did
this realize"), never *forward* (topology → "give me its members"). For
a 100-component topology with one simple Terraform step, that's pure
tedium with a real drift risk: the topology's member list and the step's
`targets` list are two independently-maintained lists describing
overlapping membership, and `strata validate`'s Phase 1 only checks that
each name in each list *exists* — never that the two lists *agree*. Add
an 11th resource to the topology for grouping/diagramming purposes,
forget to add it to the step's `targets`, and it silently never gets
deployed by that step — no validation error, since an incomplete
`targets` list is schema-valid on its own.

**Decided (2026-10-10): an additive `topologies:` field on the step**,
folded into point 9's kind-split schema below rather than kept as a
separate singular `topology:` field — same shape as `resources`/
`namespaces`, a step can expand more than one topology:

```python
class ProvisioningStepModel(PlatformBaseModel):
    resources: list[PlatformName] | None = None
    namespaces: list[PlatformName] | None = None
    topologies: list[PlatformName] | None = None  # expands: "+ every resource/namespace each topology groups"
```

Effective provisioning set, computed at validate/build time, never
stored:

```
resources_eff(step)   = set(step.resources or [])
                      | { c.resource for t in (step.topologies or []) for c in topology(t).components }
namespaces_eff(step)  = set(step.namespaces or [])
                      | { n.namespace for t in (step.topologies or []) for n in (topology(t).namespaces or []) }
```

This doesn't reintroduce the 1:1 binding point 4 rejected: two different
steps can still each name the *same* topology (the existing "shared
target needs an explicit `depends_on` edge" rule just applies
automatically after expansion), and a topology needing
Terraform+Ansible+Helm still splits into 3 steps where only the ones
wanting full coverage list it under `topologies:` — the other two keep
listing their specific slice under `resources`/`namespaces`, exactly as
today. Considered and rejected: embedding the shorthand as a structured
entry *inside* `resources`/`targets` itself (e.g. `resources: ["vm-1",
{topology: aks}]`) — rejected for turning a flat `list[PlatformName]`
into a union type, rippling into every place that treats it as plain
strings today (`check_unique_names`, the shared-target ordering rule,
`claimed_document_names()`'s set operations) for no benefit the sibling
field doesn't already get for free.

**Known limitation of this shape:** `TopologySpecModel` only has
`components[]` (resources) and `namespaces[]`/`volumes[]` — no
dns/network/firewall membership at all. So `topology:` expansion alone
never pulls in a dns/network/firewall document on its own — **resolved
by point 8's derivation**, decided below: a topology's component
resources' own `firewalls`/`dns_zones`/`subnet.network` are derived
automatically once `step.topology` is expanded. A dns/firewall/network
document with no resource reference at all (e.g. a zone with only a
literal-IP record) still needs direct listing — derivation has nothing
to derive it from.

## 8. Resource → firewall/subnet is a *provisioning* fact, structurally disconnected from *claiming* — confirmed real gap, decided

Correction to point 5 above: `firewalls`/`subnet` (network) are **already**
on `WorkspaceResourceModel` today — only `dns_zone` is genuinely missing.
But checking what that existing `firewalls`/`subnet` reference actually
*does* surfaced a second, independent instance of the same "two places
for one fact" pattern as point 7:

- `WorkspaceResourceModel.firewalls: ["fw1"]` — a **provisioning-time**
  fact: "attach this NSG/ruleset to this VM when building it," read by
  the Terraform projection to wire up the real cloud resource.
- `targets: ["fw1"]` (gap #12's claiming) — a **completely separate**
  concern: "this step owns `fw1` for `${output:...}` resolution."

These are unconnected mechanisms today. If a targeted resource
structurally references `fw1`, the step still has to **separately,
manually** list `fw1` in `targets` for claiming to work — the structural
reference doesn't establish ownership on its own, even though the two
facts ("this step builds the resource" and "this resource's firewall
needs owning") are obviously related in practice.

**Decided (2026-10-10): derive dns/firewall/network claimed-ownership as
the union of three sources**, additive to (not a replacement for) direct
listing on the step:

```
claimed(step) = { r.firewalls, r.dns_zones, r.subnet.network for r in resources_eff(step) }  # point 7's resources_eff
               | set(step.dns_zones or [])
               | set(step.firewalls or [])
               | set(step.networks or [])
```

In words, a step's claimed set is the union of:

1. Every resource's own `firewalls`/`dns_zones`/`subnet.network`
   references, for each resource in `resources_eff(step)` (point 7's
   formula — direct `step.resources` plus every `step.topologies`
   component resource).
2. Whatever is **still directly listed** on the step itself
   (`step.dns_zones`/`step.firewalls`/`step.networks` — point 9's
   escape-hatch fields). Not every dns/firewall/network document is
   necessarily tied to a resource (e.g. a zone whose only record is a
   literal IP, with no resource reference at all) — those still need
   manual listing, since nothing would ever derive them.

This resolves point 7's "known limitation" for the common case (a
targeted/topology resource's own firewall/dns_zone is now pulled in
automatically) while keeping direct listing available for the case
derivation can't reach.

**Prerequisite satisfied (2026-10-10):** point 5's `dns_zones` field
landed, so every structural reference this formula needs (`firewalls`,
`dns_zones`, `subnet.network`) exists on `WorkspaceResourceModel` today.
Not yet implemented in code — this is a decided design, not yet built.

## 9. `targets: list[PlatformName]` conflates five different kinds in one ambiguous flat list — confirmed real, decided

`WorkspaceSpecModel.validate_unique_names()` enforces name uniqueness
**per list independently** — `resources`, `namespaces`, `firewalls`,
`dns_zones`, `networks` are each checked in isolation, never against each
other. Nothing stops a resource and a namespace (or a firewall) from
sharing the same name in the same workspace. `target_names` then builds
one shared set via union (`{r.name for r in resources} | set(namespaces)
| set(dns_zones) | set(networks) | set(firewalls)`) with no kind tag
retained at all — so a collision would be genuinely ambiguous at the
schema level: validation passes either way, and nothing records which
kind a given `targets` entry was actually naming.

**Decided: split the flat list into explicit, kind-specific fields**,
eliminating the collision structurally rather than just documenting
around it. Final shape, folding in points 7 and 8's decisions — six
fields total, three "pool selection" (`resources`/`namespaces`/
`topologies`, point 7) plus three "still-direct escape hatch"
(`dns_zones`/`firewalls`/`networks`, point 8's union term 2):

```python
class ProvisioningStepModel(PlatformBaseModel):
    resources: list[PlatformName] | None = None
    namespaces: list[PlatformName] | None = None
    topologies: list[PlatformName] | None = None  # point 7 — expands each topology's members
    dns_zones: list[PlatformName] | None = None   # point 8 — direct escape hatch, not resource-derivable
    firewalls: list[PlatformName] | None = None   # same
    networks: list[PlatformName] | None = None    # same

    @model_validator(mode="after")
    def validate_at_least_one_target(self) -> "ProvisioningStepModel":
        if not (self.resources or self.namespaces or self.topologies
                or self.dns_zones or self.firewalls or self.networks):
            raise ValueError(f"Step '{self.name}': at least one target field is required.")
        return self
```

Together with points 7/8, this shrinks "5 kinds of names in one ambiguous
bag, 3 of which also need separate manual listing despite already being
referenced by a targeted resource" down to "3 explicit pool-selection
fields, plus 3 explicit escape-hatch fields for the cases derivation
can't reach." Real migration cost: every existing `targets:` list in a
real workspace needs splitting by kind.

**Independent reinforcement, found the same day in a different doc:**
[asset-management.md](asset-management.md) arrived at the identical
"pool on `WorkspaceSpecModel`, selection on `ProvisioningStepModel`"
shape for its own new `assets` field — after first trying to attach it to
`ProvisionerModel` and rejecting that specifically *because* it didn't
match the pattern `namespaces`/`firewalls`/`dns_zones`/`networks` (the
pool) + `targets` (the selection) already establishes. Two unrelated
design sessions converging on the same shape is a good sign this split is
the right one, not just a preference.

## Unified Design

Points 5/7/8/9 are four separate findings that land as **one** coherent
schema change — this section is the single source of truth for that
combined shape (point 3 needed no schema change, see above; point 4 was
rejected). Grounded against the real call sites of `step.targets`, found
by grepping the codebase rather than assumed — there are exactly seven
that index `.targets` directly: `provisioning_model.py`
(`validate_unique_targets`, `validate_provisioning_steps()`'s
shared-target ordering rule), `workspace_model.py` (`validate_execution()`),
`workspace_service.py` (`claimed_document_names()`), and
`deploy_controller.py` (three — the claiming loop, a namespace lookup,
and the per-document claim check). Every one of these needs to change;
none of them are optional cleanup. An **eighth consumer**,
`semantic_checks.py`'s `_documents_reachable_from_workspace()`, calls
`WorkspaceService.claimed_document_names()` rather than `.targets`
directly — it needs no code change of its own, migrated for free once
`claimed_document_names()`'s internals change (call site 4 below) — noted
explicitly so it isn't mistaken for a forgotten site.

### `WorkspaceResourceModel` (already implemented, point 5)

```python
class WorkspaceResourceModel(PlatformBaseModel):
    ...
    firewalls: list[str] | None = None   # already existed
    dns_zones: list[str] | None = None   # NEW, implemented 2026-10-10, mirrors firewalls
    subnet: WorkspaceResourceSubnetModel | None = None  # already existed — .network is the resource's network
```

Phase 1 cross-checks (already implemented): `validate_resource_firewall_references()`,
`validate_resource_dns_references()`, `validate_resource_subnet_references()`
— each checks the resource-level list/field against the matching
workspace-level pool (`spec.firewalls`/`spec.dns_zones`/`spec.networks`).

### `ProvisioningStepModel` (not yet implemented, points 7/8/9)

```python
class ProvisioningStepModel(PlatformBaseModel):
    name: PlatformName
    provisioner: PlatformName
    # --- replaces `targets: list[PlatformName]` ---
    resources: list[PlatformName] | None = None    # point 9 — was part of targets
    namespaces: list[PlatformName] | None = None    # point 9 — was part of targets
    topologies: list[PlatformName] | None = None    # point 7 — NEW, expands each topology's members
    dns_zones: list[PlatformName] | None = None     # point 8 — NEW, direct escape hatch
    firewalls: list[PlatformName] | None = None     # point 8 — NEW, direct escape hatch
    networks: list[PlatformName] | None = None      # point 8 — NEW, direct escape hatch
    # --- unchanged ---
    depends_on: list[PlatformName] | None = None
    scope: str | None = None

    @model_validator(mode="after")
    def validate_unique_targets(self) -> "ProvisioningStepModel":
        """Mirrors today's check, run per-field instead of once over the flat
        list. Collects every field's violations before raising, instead of
        stopping at the first offending field — matches this file's
        dominant error-collection style (`validate_execution()`,
        `validate_resource_firewall_references()`, etc. all collect into an
        `errors` list and join once), which a naive per-field loop calling
        `check_unique_names()` directly would NOT do (it raises on the
        first field with a duplicate, hiding a second field's duplicate
        until the first is fixed and the step is re-validated)."""
        errors: list[str] = []
        for field_name in ("resources", "namespaces", "topologies", "dns_zones", "firewalls", "networks"):
            values = getattr(self, field_name)
            if not values:
                continue
            try:
                check_unique_names(values, f"{field_name} in provisioning step '{self.name}'")
            except ValueError as exc:
                errors.append(str(exc))
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def validate_at_least_one_target(self) -> "ProvisioningStepModel":
        if not (self.resources or self.namespaces or self.topologies
                or self.dns_zones or self.firewalls or self.networks):
            raise ValueError(f"Provisioning step '{self.name}': at least one target field is required.")
        return self

    @model_validator(mode="after")
    def validate_no_self_dependency(self) -> "ProvisioningStepModel":
        """Unchanged."""
        if self.depends_on and self.name in self.depends_on:
            raise ValueError(f"Provisioning step '{self.name}' cannot depend on itself.")
        return self
```

### Expansion and derivation helpers (not yet implemented)

Two pure functions, needed by every one of the seven real call sites
above — proposed home: `workspace_model.py`, next to `TopologySpecModel`,
since both only ever operate on in-memory `WorkspaceSpecModel` content
(no I/O, no cross-document lookups beyond what's already loaded):

```python
def expand_topology_members(
    topologies_by_name: dict[str, TopologySpecModel], topology_names: list[str],
) -> tuple[set[str], set[str]]:
    """Point 7 — a named topology's own `components[]`/`namespaces[]` members."""
    resources: set[str] = set()
    namespaces: set[str] = set()
    for name in topology_names:
        topo = topologies_by_name[name]
        resources |= {c.resource for c in topo.components}
        namespaces |= {n.namespace for n in (topo.namespaces or [])}
    return resources, namespaces


def effective_resources_and_namespaces(
    step: ProvisioningStepModel,
    resources_by_name: dict[str, WorkspaceResourceModel],
    topologies_by_name: dict[str, TopologySpecModel],
) -> tuple[set[str], set[str]]:
    """Point 7 — a step's real provisioning set: direct + topology-expanded.

    `resources_by_name` is deliberately built EXCLUDING any resource with
    `enabled=False` (decided 2026-10-10 — a disabled resource behaves as
    if absent from the document entirely, a toggle for test/comment-out
    without deleting, not merely "skip at build time"). Intersecting
    against its keys here means a disabled resource silently drops out of
    the effective set the same way it already drops out of the real
    Terraform payload (`_build_resources_payload()`) — and makes every
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
    """Point 8 — the dns/firewall/network claimed set for `${output:...}`.

    Deliberately scoped to dns/firewall/network only, NOT namespaces —
    mirrors this codebase's own established principle that the two
    claiming mechanisms (`claimed_document_names()`'s broad vocabulary vs.
    `deploy_controller.py`'s narrower dns/network/firewall-only one) stay
    separate rather than being unified (`gap_fit_v1.md`'s own documented
    decision). Namespace claiming's topology extension lives in
    `claimed_document_names()` itself (see migration item 4) — a second,
    independent union term, not folded in here.
    """
    resources_eff, _ = effective_resources_and_namespaces(step, resources_by_name, topologies_by_name)
    claimed: set[str] = set(step.dns_zones or []) | set(step.firewalls or []) | set(step.networks or [])
    for name in resources_eff:
        resource = resources_by_name[name]  # safe: resources_eff is a subset of resources_by_name.keys() by construction
        claimed |= set(resource.firewalls or []) | set(resource.dns_zones or [])
        if resource.subnet is not None:
            claimed.add(resource.subnet.network)
    return claimed
```

**Decided (2026-10-10): namespace claiming extends symmetrically, not
"unaffected."** An earlier draft of this section claimed topology-expanded
namespace claiming was unaffected by this redesign — wrong on inspection:
today, `claimed_document_names()` only claims a namespace listed directly
in `step.targets`; one reached solely through topology membership is NOT
claimed today, the exact same gap point 7 found for resources. Decided:
fix it the same way, symmetrically — "all items can be found through the
same type of union." `claimed_document_names()` (migration item 4) now
unions in `effective_resources_and_namespaces(...)`'s namespace half
(topology-expanded), not just `step.namespaces` directly.

### Migration of the seven real call sites

1. **`provisioning_model.py`'s `validate_unique_targets`** → replaced by
   `ProvisioningStepModel`'s own new per-field validator above (shown in
   the schema itself, not a separate migration step).
2. **`provisioning_model.py`'s `validate_provisioning_steps()`
   shared-target rule** (`for target in step.targets`) → iterate
   `effective_resources_and_namespaces(step, resources_by_name,
   topologies_by_name) ∪ claimed_names(step, resources_by_name,
   topologies_by_name)` instead — two steps sharing *any* name across
   this combined set still need a `depends_on` edge, same rule, wider
   input (see "Real migration risk" below — this widening is not free).
   Needs `topologies_by_name`/`resources_by_name` threaded in as new
   parameters (today's function signature only takes `steps`) — a real,
   non-trivial signature change, not a drop-in. `resources_by_name` must
   exclude `enabled=False` resources (decided above).
3. **`workspace_model.py`'s `validate_execution()`** (`target_names`,
   `for target in step.targets`) → `target_names` becomes six separate
   pool sets (`resource_names`, `namespace_names`, `topology_names`,
   existing `dns_zones`/`networks`/`firewalls` pools), each step field
   checked against its matching pool only — this is what structurally
   fixes point 9's cross-kind collision, not just documentation.
4. **`workspace_service.py`'s `claimed_document_names()`** → namespaces:
   now unions `step.namespaces` **and** topology-expanded namespaces
   (`effective_resources_and_namespaces(...)`'s namespace half — the
   symmetric fix decided above, not the originally-drafted "unaffected"
   claim). dns/networks/firewalls: replaced by `claimed_names()` above.
   `resources_by_name` must be built excluding `enabled=False` resources
   (decided above) before being passed in.
5. **`deploy_controller.py`'s claiming loop** (`claimed_by_category[category]
   |= set(step.targets) & docs.keys()`) → `claimed_by_category[category]
   |= claimed_names(step, resources_by_name, topologies_by_name) &
   docs.keys()`.
6. **`deploy_controller.py`'s namespace lookup**
   (`[graph.namespaces[name] for name in step.targets if name in graph.namespaces]`)
   → iterate `effective_resources_and_namespaces(step, resources_by_name,
   topologies_by_name)[1]` (the namespace half) instead.
7. **`deploy_controller.py`'s per-document claim check**
   (`if name in step.targets or name not in claimed_by_category[category]`)
   → `if name in claimed_names(step, ...) or name not in claimed_by_category[category]`.

### What this does NOT change

- `ProvisionerModel`/`depends_on` (point 3) — untouched, already correct.
- `TopologySpecModel` itself — untouched; `topologies:` only *reads* its
  existing `components[]`/`namespaces[]`, no new fields on the topology
  side.
- The Terraform projection (`terraform_projection.py`) — `dns_zones` on
  `WorkspaceResourceModel` stays unprojected for now (point 5's "known
  limitation," deliberate, tracked separately against ADR-0035).
- `${output:...}` token resolution itself (`value_tokens.py`) — only
  *which* documents are considered claimed changes, not how a claimed
  token is actually resolved.

### Real migration risk, not previously flagged

Once point 8's derivation (and its namespace-claiming extension above)
land, two previously-independent steps can newly **share** a derived name
they didn't share before — e.g. two steps whose `step.resources` both
(indirectly, via a shared firewall reference) now resolve to the same
claimed firewall, or two steps whose `step.topologies` names the same
topology and therefore now share every one of its namespaces.
`validate_provisioning_steps()`'s existing shared-target-needs-an-
explicit-`depends_on`-edge rule is a **hard error**, not a warning — so a
real workspace that validates cleanly today can start **failing `strata
validate`** immediately after this migration, with no change to that
workspace's own `targets:` *content*, only to its *shape*. A genuine
backward-compatibility risk for Phase 4's real-workspace migration, not
just a mechanical field rename — call out explicitly when migrating
`config/`, and warn any real consumer (`haven`/`config-deploy`) before
merging.

## Implementation Plan

Four phases, each independently mergeable and fully tested, per this
repo's "small, tested, documented phases" convention. Phase 1 (point 5)
is already done; phases 2–4 are not started.

### Phase 1 — `WorkspaceResourceModel.dns_zones` (point 5) — DONE

Implemented 2026-10-10: `dns_zones` field,
`validate_resource_dns_references()`, matching tests in
`test_models_workspace.py`. See point 5 above for detail. No further
action this phase.

### Phase 2 — `ProvisioningStepModel` schema split (points 7/9)

- Replace `targets: list[PlatformName]` with the six fields in the
  Unified Design schema above, plus its two validators.
- Add `expand_topology_members()`/`effective_resources_and_namespaces()`
  to `workspace_model.py`.
- Rewrite `workspace_model.py`'s `validate_execution()` per migration
  item 3 above — six separate pool checks instead of one `target_names`.
- Rewrite `provisioning_model.py`'s `validate_provisioning_steps()` per
  migration item 2 above — new `topologies_by_name`/`resources_by_name`
  parameters, threaded in by its one real caller
  (`WorkspaceSpecModel.validate_execution()`).
- **No `claimed_names()`/point 8 work yet** — this phase only makes the
  new fields exist and validate; `deploy_controller.py`/
  `workspace_service.py` still read the old `.targets` attribute, which
  no longer exists — **this phase alone leaves the codebase in a broken,
  non-compiling state** (mypy will catch every one of the five remaining
  call sites immediately). Acceptable only if Phase 3 lands in the same
  PR/commit — not independently mergeable on its own despite the "each
  phase mergeable" convention above; called out explicitly so this isn't
  missed later.
- **Tests:** every existing `test_models_workspace.py`/
  `test_models_provisioning.py` test that builds a step with `targets:`
  needs updating to the new field names — this is the bulk of the
  migration's test cost, not new-behavior testing.

### Phase 3 — Derivation + the five remaining call sites (point 8)

- Add `claimed_names()` to `workspace_model.py`.
- Migrate the five remaining real call sites per migration items 1,
  4–7 above (`workspace_service.py`'s `claimed_document_names()`,
  `deploy_controller.py`'s three sites).
- **Tests:** a real scenario where a step names a resource with its own
  `firewalls`/`dns_zones`/`subnet` and asserts the firewall/dns/network
  is claimed *without* being separately listed on the step — the actual
  behavior point 8 exists to deliver, not just a schema-shape test.
  A second test for the escape hatch: a dns zone with no resource
  reference, listed directly via `step.dns_zones`, still claimed.
  A third test for `topologies:` expansion feeding the same derivation
  (a topology's component resource's firewall claimed via `step.topologies`
  alone, no direct `step.resources` entry). A fourth test for the
  namespace-claiming extension decided above: a namespace reached only
  via `step.topologies` (not listed in `step.namespaces` directly) is
  claimed by `claimed_document_names()`. A fifth test for the
  disabled-resource decision: a resource with `enabled=False` and its own
  `firewalls`/`dns_zones` is named in `step.resources`, but its
  firewall/dns_zone is NOT claimed — confirms `resources_by_name`
  excludes it as designed, not just that it doesn't crash.

### Phase 4 — Real-workspace migration + docs

- Migrate every real `targets:` list in `config/` (this repo's own
  dogfooding solution) to the new split fields — the "migration cost"
  point 9 already called out, now concretely due.
- Update `docs/config/workspace.md` (if it documents `targets` — not yet
  checked, confirm before writing) and the JSON Schema generation for
  `ProvisioningStepModel`.
- Write the ADR recording this decision (mirrors how
  `docs/decisions/0035-unified-terraform-projection.md` was written
  after that doc's own implementation plan) — candidate title:
  "Split ProvisioningStepModel.targets by kind; derive dns/firewall/network
  claiming from topology/resource references."
- Graduate this whole work doc to `docs/design/` once Phases 1–4 are
  done and tested end to end, per `docs/work/README.md`'s own lifecycle.

## Remaining Work

- [x] **Resolved (2026-10-10): point 3** — `primitives`/`components` are
      pure library code (no independent `apply`/state; only `stack`
      applies), confirmed from the user's own real layering description.
      `ProvisionerModel.depends_on` is the correct, already-existing
      mechanism — no schema change needed.
- [x] **Implemented (2026-10-10): `WorkspaceResourceModel.dns_zones`**
      (point 5) — mirrors `firewalls`, Phase 1 cross-checked against
      `spec.dns_zones` via a new `validate_resource_dns_references()`.
      `networks` remains available only indirectly via `subnet.network`
      (no separate flat `networks` field added — not asked for, and
      `subnet` already names the one network a resource structurally
      sits in).
- [ ] Project `dns_zones` into the Terraform payload once its shape is
      rebuilt (ADR-0035's unified projection, or whatever lands instead)
      — currently inert: `firewalls`/`subnet` are already read by
      `terraform_projection.py`'s `_merge_resource_entry()`, `dns_zones`
      is not read anywhere yet. Deliberate, not an oversight: the current
      `_merge_resource_entry()` is expected to be rewritten regardless, so
      wiring `dns_zones` into today's shape would be thrown away almost
      immediately.
- [ ] **Points 7/8/9 — see "Unified Design"/"Implementation Plan" above**,
      the single source of truth for the combined `ProvisioningStepModel`
      schema change and its 4-phase build-out. Phase 1 (point 5) is done;
      Phases 2–4 (points 7/8/9 + the seven real call-site migrations +
      real-workspace migration + ADR) are not started. Superseded the
      three separate "decided, not yet implemented" items that used to be
      listed here individually — tracked as one plan now, not three.

## Changelog

- 2026-10-10: **Critical review of the Unified Design/Implementation
  Plan** found and fixed six real issues before any code was written:
  (1) "namespace claiming is unaffected" was wrong — topology-expanded
  namespace claiming is a real scope extension, decided symmetric with
  point 8 ("all items can be found through the same type of union");
  (2) `semantic_checks.py`'s `_documents_reachable_from_workspace()` is
  an 8th, previously-uncounted consumer of `claimed_document_names()` —
  needs no code change (migrated for free), now noted explicitly so it
  isn't mistaken for a forgotten site; (3) `resources_by_name[name]`
  lookups in `claimed_names()` were bare/unchecked, unlike the existing
  `deploy_controller.py` namespace-lookup's defensive `if name in
  graph.namespaces` style — fixed by constructing `resources_eff` as a
  set intersection against `resources_by_name.keys()`, making every
  downstream lookup provably safe by construction; (4) `enabled=False`
  resources (confirmed excluded from the real Terraform payload by
  `_build_resources_payload()`) were not filtered out of derivation —
  decided (user: "disabled resources are like they are not in the
  document") to exclude them from `resources_by_name` entirely, both
  from the effective provisioning set and claimed-ownership derivation;
  (5) the draft `validate_unique_targets` validator raised on the first
  field with a duplicate instead of collecting across all six fields,
  inconsistent with this file's own dominant error-collection style
  (`validate_execution()` et al.) — fixed to collect and join; (6) a
  previously-unflagged migration/compatibility risk: point 8's
  derivation can make two previously-independent steps newly share a
  claimed name, triggering `validate_provisioning_steps()`'s existing
  hard-error shared-target rule on a real workspace that validates
  cleanly today — added as an explicit Phase 4 warning. Two user
  decisions obtained via direct question before fixing (1) and (4),
  rather than assumed.
- 2026-10-10: **Wrote the Unified Design + Implementation Plan** —
  consolidated points 5/7/8/9 into one `ProvisioningStepModel` schema
  (six fields replacing `targets`) plus two pure helper functions
  (`expand_topology_members()`/`effective_resources_and_namespaces()` for
  point 7, `claimed_names()` for point 8), grounded against all seven
  real `.targets` call sites found by grep (not assumed) across
  `provisioning_model.py`/`workspace_model.py`/`workspace_service.py`/
  `deploy_controller.py`. 4-phase Implementation Plan added (Phase 1/point
  5 already done; Phases 2–4 not started) — mirrors
  `unified-terraform-projection.md`'s own Implementation Plan structure
  for consistency across this repo's `docs/work/` docs. Replaced the
  three separate "decided, not yet implemented" Remaining Work items for
  points 7/8/9 with a single pointer to this plan.
- 2026-10-10: **Decided point 3** — `primitives`/`components`/`stack`
  confirmed as pure library code (only `stack` has a real `apply`/state;
  `primitives`/`components` are composed into it via relative `source`
  paths, never applied independently), from the user's own description
  of the real layering. `ProvisionerModel.depends_on` is the correct,
  already-existing mechanism — no schema change needed, resolved without
  code changes.
- 2026-10-10: **Decided point 7** — the topology-expansion field is
  `topologies: list[PlatformName] | None` (plural), folded directly into
  point 9's final schema rather than kept as a separate singular
  `topology:` field — a step can expand more than one topology, same
  shape as `resources`/`namespaces`. Expands both a topology's
  `components[]` resources and its `namespaces[]` members into the
  step's effective provisioning set.
- 2026-10-10: **Finalized the combined points 7/8/9 schema** —
  `ProvisioningStepModel` replaces `targets` with six fields:
  `resources`/`namespaces`/`topologies` (pool selection) plus
  `dns_zones`/`firewalls`/`networks` (point 8's direct escape hatch,
  corrected from an earlier draft of point 9 that said these would
  "never [be] listed directly" — point 8's decision explicitly keeps them
  as a third, manual-listing union term for a dns/firewall/network
  document with no resource reference at all). None of this implemented
  in code yet.
- 2026-10-10: **Decided and implemented point 5** — added
  `WorkspaceResourceModel.dns_zones: list[str] | None`, mirroring
  `firewalls`, plus a Phase 1 `validate_resource_dns_references()`
  cross-check (`src/strata/models/workspace_model.py`), with matching
  tests in `test_models_workspace.py`. Unblocks point 8's derivation
  formula, which needed `dns_zones` to exist structurally on the resource
  before it could be derived from. `networks` stays subnet-only (no new
  flat field) — point 8 is still undecided, only its prerequisite data now
  exists.
- 2026-10-10: Checked whether the new `dns_zones` field actually does
  anything for a provisioner yet — it doesn't. `terraform_projection.py`
  already projects `firewalls`/`subnet` into the per-resource payload
  (`_merge_resource_entry()`), but has no equivalent line for `dns_zones`
  — the field validates but is otherwise dead data today. Left
  deliberately unwired rather than patched in: the projection itself is
  expected to change shape (ADR-0035), so wiring `dns_zones` into the
  current `_merge_resource_entry()` now would just be replaced again
  shortly after. Tracked in Remaining Work, to be done once the
  projection rebuild actually happens.
- 2026-10-10: **Decided point 8** — claimed dns/firewall/network
  ownership is derived as the union of: (a) each resource directly
  listed in `step.resources`, (b) each resource that is a component
  member of the topology named in `step.topology` (point 7), and (c)
  whatever dns/firewall/network names are still listed directly on the
  step. (a)/(b) contribute each resource's own
  `firewalls`/`dns_zones`/`subnet.network`; (c) stays as an explicit
  escape hatch for a dns/firewall/network document with no resource
  reference at all (nothing to derive it from). Resolves point 7's
  "known limitation" for the common case. Design only — depends on point
  7's `topology:` field also being implemented; neither is built yet.
- 2026-10-10: **Decided point 9** — split `targets` into
  `resources`/`namespaces`/`topologies`, confirmed after independent
  reinforcement from `asset-management.md` reaching the identical
  pool-then-select shape for its own `assets` field in the same session.
  Points 7/8 (the `topology:` expansion field, deriving dns/firewall/
  network ownership from targeted resources) remain open, not yet
  decided — only the kind-split itself is settled.
- 2026-10-10: **Added points 7-9** from a follow-on brainstorm:
  topology-membership-to-`targets` expansion (additive `topology:` field,
  not a 1:1 binding — preserves ADR-0011's n:m story), a second "two
  places for one fact" instance for firewall/network claiming (a
  resource's own `firewalls`/`subnet` reference doesn't establish
  `${output:}` claiming ownership today, unlike what its existence would
  suggest), and a confirmed, previously-unflagged cross-kind name
  collision risk in the flat `targets: list[PlatformName]`
  (`validate_unique_names()` only checks uniqueness within each list,
  never across resources/namespaces/firewalls/dns_zones/networks
  together). All three are related and compose into one proposal: split
  `targets` into `resources`/`namespaces`/`topologies`, and derive
  dns/firewall/network ownership from those instead of listing them
  directly. None of this implemented — tracked as open decisions.

- 2026-10-10: Created from a live brainstorm session re-examining the
  execution/provisioning model against real multi-root IaC composition
  scenarios. Four of six points resolved with no schema change (two
  already work as designed, one already deliberately rejected elsewhere);
  one needs a real-workspace follow-up before closing; one is a genuine,
  previously-untracked gap (DNS-zone reference on `WorkspaceResourceModel`).
