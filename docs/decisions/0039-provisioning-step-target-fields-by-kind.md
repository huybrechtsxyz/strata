# Split ProvisioningStepModel.targets by kind; derive dns/firewall/network claiming from topology/resource references

- Status: accepted
- Date: 2026-10-13
- Related: [docs/design/workspace-execution-model.md](../design/workspace-execution-model.md)
  (the finished design — full schema, derivation helpers, migrated call
  sites), ADR-0011 (topology and provisioning decoupling — the "topology
  is a derived fact, not declared" rule this decision extends rather than
  reopens), [gap_fit_v1.md](../work/gap_fit_v1.md) gap #12 (the original
  dns/network/firewall claiming mechanism this decision generalizes)

## Context and Problem Statement

`ProvisioningStepModel.targets` was a single flat `list[PlatformName]`
naming resource/namespace/dns/network/firewall document names a step acts
on, cross-checked at the `WorkspaceSpecModel` level against the union of
all five pools combined into one `target_names` set. Two structural
problems followed directly from that single combined pool:

- **Cross-kind name collisions go undetected.** A resource and a dns zone
  sharing a name both validate as a step's `target`, with no way to tell
  from the schema which kind a given entry means — the check only
  confirms the name exists *somewhere* across all five pools, not that it
  exists in the pool the author actually intended.
- **A resource's own `firewalls`/`dns_zones`/`subnet` membership was
  invisible to `${output:...}` claiming** (`deploy_controller.py`,
  `WorkspaceService.claimed_document_names()`) unless that
  firewall/dns/network name was *also* listed directly in the step's
  `targets` — duplicating a fact the resource document already declares.
  The same gap applied to a topology: naming a topology's member
  resources/namespaces required listing every one of them individually,
  with no way to say "this step acts on topology X" and have its members
  follow automatically (`topology` itself already exists for exactly this
  grouping purpose, per ADR-0011 — nothing read it for provisioning
  before this decision).

Neither gap is observable from the schema alone; both were found by
reviewing `step.targets`'s real call sites against what a step actually
needs to express (`docs/design/workspace-execution-model.md`, "Derivation"
sections).

## Considered Options

- **Keep one flat `targets` list, document the ambiguity.** Rejected —
  documentation cannot make a schema distinguish a resource name from a
  dns zone name; the collision risk and the duplicated-listing burden are
  both structural, not a training/documentation gap.
- **Add a `kind:` discriminator field alongside each target entry**
  (e.g. `targets: [{name: x, kind: resource}]`). Rejected: a six-field
  split is more idiomatic YAML (a reader sees `resources:`/`dns_zones:`
  as plain lists, no nested object), and self-documents which pool each
  name is checked against without an extra field to keep in sync.
- **Six separate fields by kind, plus derivation from topology/resource
  membership** (chosen).

## Decision Outcome

Chosen: replace `targets: list[PlatformName]` with six optional fields —
`resources`/`namespaces`/`topologies`/`dns_zones`/`firewalls`/`networks`
— each cross-checked against its own matching `WorkspaceSpecModel` pool
only, never a combined union. At least one field is required per step.

Two derivation rules settled as part of this one decision:

- **`topologies:` expands to that topology's own component
  resources/namespaces** (`expand_topology_members()`) — a step names
  the topology once, rather than re-listing every resource/namespace it
  groups. This is additive to `topology`'s existing pure-grouping role
  (ADR-0011): a topology still has no `provider`/`provisioner` field of
  its own, and naming it in `topologies:` is the step saying "act on
  everything this topology groups," not the topology declaring how it's
  built.
- **A step's effective resource set also derives dns/firewall/network
  claiming** (`claimed_names()`): if a step's effective resources
  (direct `resources:` ∪ topology-expanded) include a resource with its
  own `firewalls`/`dns_zones`/`subnet.network`, those documents are
  claimed for `${output:...}` ownership automatically — no need to also
  list them in the step's `dns_zones:`/`firewalls:`/`networks:` fields.
  Those three fields remain as a **direct escape hatch** for a
  dns/firewall/network document with no resource reference at all.
  Namespace claiming (`WorkspaceService.claimed_document_names()`)
  extends symmetrically: a namespace reached only through `topologies:`
  is claimed exactly as if listed directly under `namespaces:`.

A resource with `enabled=False` is excluded from every derivation
(`resources_by_name` is built pre-filtered) — a disabled resource behaves
as if absent from the document entirely, matching the existing exclusion
from the real Terraform payload, not merely "skipped at build time."

`validate_provisioning_steps()`'s existing shared-target-needs-an-explicit-
`depends_on`-edge rule (ADR-0011) is unchanged in principle but now
operates over each step's full *effective* touched-name set (direct +
topology-expanded + resource-derived claims), not just its directly
declared fields — two steps can become ambiguous through shared
*indirect* membership (e.g. both naming resources that reference the same
firewall) where they weren't before.

### Consequences

- Good: a resource name can never be silently accepted where a dns zone
  was meant, or vice versa — each field is checked against exactly one
  pool.
- Good: a resource's own firewall/dns/subnet facts need to be declared
  once (on the resource), not duplicated on every step that touches it.
- Good: `topologies:` makes topology membership directly useful for
  provisioning scoping, without reopening ADR-0011's "topology doesn't
  own how it's built" boundary — the topology document itself gained no
  new field.
- Bad: every existing `targets:` list (workspace YAML and tests) had to
  be migrated to the correct one of six fields — a real migration cost,
  not a drop-in rename, since the right field depends on what kind of
  name each entry was.
- Bad: a workspace that validates cleanly today can start failing
  `strata validate` after migrating, with no change to its `targets:`
  *content* — only the derivation can make two previously-independent
  steps newly share a claimed name, triggering the existing hard-error
  shared-target rule. Mechanical but requires re-checking real
  consumers' workspaces after migrating, not just a syntax rename.
- Neutral: `assets:` (docs/design/asset-management.md) stays a fully
  separate field/pool, as before — materialised build input a step
  consumes is still a different concept from a provisioning target a
  step acts on.
