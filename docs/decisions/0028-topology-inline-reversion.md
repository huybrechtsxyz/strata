# Topology — Reverting from Standalone Kind to Inline Workspace Field

- Status: implemented — Phases 0-4 and 6 of the rollout plan are done;
  Phase 5 (migrating `.v2-cfg`/`.v2-haven` local fixtures) was deliberately
  skipped, not left pending (see
  [docs/design/topology-standalone-kind-reconsideration.md](../design/topology-standalone-kind-reconsideration.md))
- Date: 2026-10-01
- Related: [ADR-0011](0011-topology-and-provisioning-decoupling.md) (the
  original decision — this ADR supersedes **only** its standalone-kind-promotion
  half; the Topology/Provisioning decoupling decision itself, and
  `ProvisionerModel`/`ProvisioningStepModel`, are unaffected and stand
  unchanged), [ADR-0012](0012-workspace-model-design-decisions.md)
  (`WorkspaceModel` gains the inlined field; its described Phase 2 Topology
  cross-checking collapses to Phase 1 as a result),
  [docs/design/topology-standalone-kind-reconsideration.md](../design/topology-standalone-kind-reconsideration.md)
  (full evidence record, cost breakdown, and phased implementation plan —
  this ADR records only the decision itself)

## Context and Problem Statement

ADR-0011 promoted `Topology` from a v1 sub-block of `Workspace` to a
standalone `kind: topology` document, bundling two distinct justifications:
(1) decoupling grouping from provisioning tooling (a topology can be built/
configured by several provisioners), and (2) promoting topology to a
reusable, cross-workspace document — "exactly the kind of thing a platform
engineer would want to define once and reference from multiple workspaces,
the same way a Provider or Resource definition is reused."

ADR-0011 itself already flagged the direct cost of (2) at the time it was
written: because `Topology` is its own file, `components[].resource`/
`namespaces[].namespace` references can only be syntax-checked (Phase 1), not
existence-checked, without first knowing which workspace is using the
topology — existence-checking was deferred to a future Workspace Phase 2
check (`WorkspaceService.validate_topology_references()`, since built).

Reassessing (2) specifically, prompted by reading a second, independent real
v2 migration (`cfg-int-deployment-v2`'s `control` stack) alongside this
project's own `.v2-cfg`/`.v2-haven` fixtures and the shipped example
solution in `config/`:

- **No real cross-workspace reuse found anywhere.** Every real instance
  examined (`.v2-cfg` `spoke`, the independent `control` migration,
  `.v2-haven`'s `hetzner_hearth`, this repo's own shipped example) is a
  trivial 1:1 pairing with exactly one workspace. Only `.v2-haven`'s
  `hetzner_forge` shows genuine multi-provisioner structure (terraform owns
  the VM resource, helm owns 7 namespaces) — and even there, the fixture's
  own author-comment questions whether that granularity was organically
  necessary or just preserved for a coverage check.
- **The reference direction is structurally inverted.**
  `WorkspaceResourceModel` ([workspace_model.py](../../src/strata/models/workspace_model.py))
  already demonstrates the correct type/instance split: `.resource`
  references the standalone, reusable `kind: resource` *type*; `.name` is
  the workspace-local *instance* name. `TopologyComponentModel.resource`
  resolves against that instance name, not against any standalone-kind
  document's own name — meaning `Topology`'s content only ever resolves at
  the instance layer, one workspace at a time, even though `Topology` itself
  was promoted to live at the type/standalone layer, a peer of `Resource`
  and `Namespace`. A standalone `Topology` document can never validate its
  own component references in isolation; it structurally cannot, no matter
  how much Phase 2 machinery is built around it.

## Considered Options

- **Option A — Full reversion.** Inline `TopologySpecModel` as a field on
  `WorkspaceSpecModel`, at the same model level as `WorkspaceResourceModel`
  (instance-scoped). Delete `TopologyModel`, `PlatformKind.TOPOLOGY`,
  `TopologyService`.
- **Option B — Support both.** `WorkspaceSpecModel.topology[]` entries accept
  either a `PlatformName` reference (current standalone form) or an inline
  `TopologySpecModel`, discriminated at parse time. `Topology` stays a
  standalone kind.
- **Option C — Status quo.** No change; accept the Phase 2 validation cost
  and the inverted-reference-direction as the permanent shape.

## Decision Outcome

Chosen: **Option A — full reversion**, because the evidence found zero real
payoff for standalone-ness (no cross-workspace reuse anywhere it was looked
for) while the structural cost (inverted reference direction, Phase 2
cross-document validation for what is, in every real case, same-document
data) is real, current, and self-admitted in ADR-0011's own original text.
Option B would avoid breaking the one real external migration that already
uses the standalone form (`cfg-int-deployment-v2`'s `control` stack), but
was rejected: it keeps every structural cost of Option C (the standalone
branch still has the inverted-reference problem) for a reuse case with no
confirmed real demand, in exchange for avoiding a one-time, already-scoped
migration cost.

### Consequences

- Good: `components[].resource`/`namespaces[].namespace` existence-checking
  becomes a same-document Phase 1 `model_validator` — no cross-document
  lookup, no "no loaded TopologyModel provided for validation" failure mode
  possible even in principle.
- Good: `Topology`'s placement now matches the instance-level pattern
  `WorkspaceResourceModel` already establishes — one fewer standalone kind,
  one fewer file, for what is in every real case examined a 1:1 pairing with
  a single workspace.
- Bad: breaks `cfg-int-deployment-v2`'s `control` stack migration (already
  authored against the standalone form) the moment it is reconciled back
  into this repo's fixtures — a known, accepted, one-time cost.
- Bad: no longer directly supports "define a topology once, reference it
  from several workspaces" if that demand materializes later — would need
  to be re-added (as Option B's union shape, or similar) if real evidence for
  it ever appears.

## Implemented

See [docs/design/topology-standalone-kind-reconsideration.md](../design/topology-standalone-kind-reconsideration.md)'s
`## Implementation Plan` for the full phased checklist (Phase 0 ADR through
Phase 6 documentation) and what each phase actually did.
