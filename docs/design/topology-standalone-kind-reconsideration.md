# Topology Standalone Kind — Reconsideration

- Status: current — full reversion implemented. Migrating `.v2-cfg`/
  `.v2-haven` local checkout fixtures was deliberately skipped (both are
  fully gitignored, not exercised by the automated suite, and not
  committed — leaving them on the old standalone shape has no effect on
  this repo's own correctness).
- Last updated: 2026-10-01

## Overview

[ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md) promoted
`Topology` from a v1 sub-block of `Workspace` to its own standalone `kind:
topology` document, bundling two distinct justifications: (1) decoupling
grouping from provisioning tooling (a single topology can be built/configured
by several provisioners), and (2) promoting topology to a reusable,
cross-workspace document. This doc records a from-evidence reassessment of
justification (2) specifically — triggered by reading a second, independent
real-world v2 migration (`cfg-int-deployment`'s `control` stack) alongside the
existing `.v2-cfg`/`.v2-haven` fixtures — and lays out the full cost of
reverting Topology to an inline field on `WorkspaceSpecModel`.

Justification (1) is **not** in question here and is not affected by any
option below: `Provisioner`/`ProvisioningStep` stay fully decoupled from
Topology regardless of where Topology's grouping data lives.

## Current Design

`Topology` is a standalone root document (`topology_model.py`):
`TopologyModel` (`apiVersion`/`kind`/`meta`/`spec`) wraps `TopologySpecModel`
(`type`, `components: list[TopologyComponentModel]`, `namespaces`, `volumes`).
`WorkspaceSpecModel.topology` is a `list[PlatformName]` of references,
resolved via `DocumentIndex` discovery (ADR-0015) into
`ResolvedWorkspaceGraph.topologies: dict[str, TopologyModel]`
(`resolved_context.py`), which `terraform_projection.py`'s
`_build_topologies_payload()` and `WorkspaceService.validate_topology_references()`
/`validate_topology_components()` both consume.

### The structural problem

`TopologyComponentModel.resource` is a `PlatformName` reference — but it
resolves against `WorkspaceResourceModel.name`
([workspace_model.py](../../src/strata/models/workspace_model.py)), the
**workspace-local instance name**, not the standalone `Resource` document's
own name. This is visible directly in `WorkspaceResourceModel`'s own field
split: `.resource` references the standalone, reusable `kind: resource`
*type*; `.name` is the instance name, correctly scoped inline per-workspace
("one Resource class can be instantiated several times under different
names"). `Resource` already demonstrates the right type/instance split that
ADR-0011 wanted for Topology — but Topology's own content (`components[].resource`)
only resolves at the *instance* layer, one workspace at a time, while Topology
itself lives at the *type* layer (a peer of `Resource`, `Namespace`). This is
why `validate_topology_references()` cannot be a same-document model
validator today: it must be handed `topology_models` resolved by whichever
workspace happens to be referencing them, and produces no useful information
in isolation — a standalone `Topology` document cannot validate its own
`components[].resource` references without first being told which workspace
is using it.

### Real-world evidence gathered

| Source                                                                                                   | Shape found                                                                                                                                                                                                                                                                                            |
| -------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `.v2-cfg` (`spoke` stack, hand-migrated)                                                                 | Separate `topologies/spoke-cluster.yaml`; trivial 1:1 with its one workspace                                                                                                                                                                                                                           |
| `E:\cfg-int-deployment-v2\...\stacks\control` (independent real migration, not authored by this session) | Same separate-file shape; same trivial 1:1 — reinforces this is the *common* case, not an outlier                                                                                                                                                                                                      |
| `.v2-haven` (`hetzner_hearth` topology)                                                                  | Trivial 1:1 with `haven-platform` workspace                                                                                                                                                                                                                                                            |
| `.v2-haven` (`hetzner_forge` topology)                                                                   | The **one** genuine multi-provisioner case found: terraform owns the VM resource, helm owns 7 namespaces, as separate dependent execution steps — but the author's own comment on this fixture questions whether this granularity was organically necessary or just preserved "for the coverage check" |
| `config/` (this repo's shipped example solution)                                                         | Separate `workspaces/topology.yaml` + `templates/topology.template.yaml` — also a trivial 1:1                                                                                                                                                                                                          |

Net: 1 of 5 real instances shows any genuine payoff from standalone-ness: the
cross-workspace-*reuse* rationale specifically has zero supporting evidence
anywhere it was looked for.

## Full Reversion Design

Revert `Topology` to an inline field on `WorkspaceSpecModel`, at the same
model level as `WorkspaceResourceModel` (instance-scoped, not a peer of
`Resource`/`Namespace`).

### 1. Schema (`models`)

- Move `TopologySpecModel`, `TopologyComponentModel`,
  `TopologyNamespaceReferenceModel`, `TopologyVolumeModel` from
  `topology_model.py` into `workspace_model.py` (or a shared module imported
  by both, if `topology_config_model.py` still needs the component/role
  vocabulary).
- Delete `TopologyMetaModel` and the `TopologyModel` root wrapper
  (`apiVersion`/`kind`/`meta`) — no longer a document, so no root envelope.
- Delete `PlatformKind.TOPOLOGY` from `common_models.py`'s kind enum.
- `WorkspaceSpecModel.topology: list[PlatformName] | None` → `topology:
  list[TopologySpecModel] | None`, each entry gaining its own `name:
  PlatformName` (replacing `TopologyMetaModel.name`).

### 2. Validation (net simplification)

- `WorkspaceService.validate_topology_references()` /
  `validate_topology_components()` collapse from Phase 2 (cross-document,
  caller must pre-load and pass in `topology_models`) into same-document
  `model_validator(mode="after")` methods directly on `WorkspaceSpecModel` —
  no index lookup, no "no loaded TopologyModel provided" error path needed at
  all, since there is nothing left to fail to load.
- `validate_topology_components()`'s cross-check against
  `ConfigurationModel`/`TopologyConfigModel` (ADR-0013/0014) stays a Phase 2
  check — `TopologyConfigModel` remains a genuinely separate, confirmed-reusable
  standalone kind (a type-role registry, not an instance), unaffected by this
  reversion.

### 3. Services / controllers / integrations

- Delete `TopologyService` (`topology_service.py`) and its
  `PlatformKind.TOPOLOGY: TopologyService` entry in `solution_controller.py`'s
  model registry.
- `build_controller.py`: remove the `_lookup_all(index, PlatformKind.TOPOLOGY,
  workspace.spec.topology)` call; `ResolvedWorkspaceGraph.topologies` field
  (`resolved_context.py`) removed.
- `terraform_projection.py`'s `_build_topologies_payload()`: iterate
  `graph.workspace.spec.topology` directly (entries already carry
  `name`/`type`/`components`/`volumes`) instead of `graph.topologies[name]`
  lookups. Output JSON shape (`{"topologies": {name: {type, components,
  volumes}}}`) is unchanged — mechanical, low-risk change.
- `semantic_checks.py`: ~3 call sites referencing `PlatformKind.TOPOLOGY`/
  `TopologyModel` re-pointed at the inline data.
- `reference_fields.py`/`references.py`: `PlatformKind.TOPOLOGY` removed from
  the set of independently-referenceable/discoverable kinds.

### 4. Documentation

- ADR-0011 is immutable (MADR convention) — reversal needs a **new ADR**
  superseding it, not an in-place edit. ADR-0012/0013/0014 reference the
  standalone promotion as settled context and need "Revised" addenda (not
  rewrites) pointing at the new ADR.
- Update: `v2-schema-overview.md` (`topology` row — remove, fold into
  `workspace` row), `solution-loading-and-phase2-validation.md`,
  `terraform-tfvars-parity.md`, `docs/config/topology.md`.
- `docs/work/gap_fit_v1.md` gap #5 reopens (currently closed as "deliberate,
  not an open gap").
- `docs/how-to/migrate-v1-workspace-topology-provisioning.md` (written this
  session, premised entirely on the standalone-document shape) needs a full
  rewrite or retraction.

### 5. Tests

Delete `test_models_topology.py`, `test_services_topology.py` outright.
Edit: `test_services_workspace.py`, `test_solution_controller.py` (9+
`PlatformKind.TOPOLOGY` assertions), `test_solution_context.py`,
`test_references.py`, `test_build_controller.py`, `test_semantic_checks.py`,
`test_integrations_terraform_projection.py`, `test_commands_validate.py`,
`test_models_workspace.py`. Realistically 60-100+ individual test edits.

### 6. Real fixture migration

- This repo's shipped example (`config/workspaces/topology.yaml` +
  `config/templates/topology.template.yaml`) and `.v2-cfg`/`.v2-haven`'s
  topology files all merge back into their respective `workspace.yaml` —
  ours to fix, mechanical.
- **Not ours to fix**: `E:\cfg-int-deployment-v2\...\stacks\control` already
  uses the standalone shape. A full reversion invalidates that real,
  independently-authored migration work the moment it's ported back into
  this repo's fixtures — the one concrete cost that doesn't shrink no matter
  how the code-level work is sequenced.

## Related Decisions

- [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md) —
  original decision being reconsidered (justification (2) only; (1) stands)
- [ADR-0012](../decisions/0012-workspace-model-design-decisions.md) —
  `Workspace` as the composed "image"; would gain the inlined topology field
- [ADR-0013](../decisions/0013-configuration-topology-registry.md),
  [ADR-0014](../decisions/0014-provider-topology-config-standalone-kinds.md) —
  `TopologyConfigModel`, unaffected, stays standalone

## History

- The reversal needed a **new** ADR (0028) rather than editing ADR-0011 in
  place, per this repo's immutable-ADR convention — ADR-0011/ADR-0012 only
  got one-line Status pointers to it, bodies untouched. ADR-0013/ADR-0014
  were deliberately left alone (`TopologyConfigModel` is a separate,
  unaffected kind).
- Schema/services/controllers/integrations and this repo's own shipped
  `config/` fixture all had to land in one combined pass, not phase by
  phase with a green suite in between — `PlatformKind.TOPOLOGY`/
  `TopologyModel`/`TopologyService` were consumed across all of them
  simultaneously (including the fixture, since discovery hard-errors on an
  unrecognized `kind:`), so splitting the work would have meant committing
  a deliberately broken intermediate state.
- Migrating `.v2-cfg`/`.v2-haven` local checkout fixtures was skipped, not
  deferred as an oversight — both are fully gitignored and untouched by
  the automated suite, so the old standalone-reference shape there has no
  effect on this repo's correctness; they'd need manual migration before
  next use.
- `cfg-int-deployment-v2`'s `control` stack had already independently
  adopted the standalone-kind shape before this reversal — reverting
  invalidates that real migration work. Accepted as a one-time cost that
  doesn't shrink regardless of how the code-level work is sequenced.

