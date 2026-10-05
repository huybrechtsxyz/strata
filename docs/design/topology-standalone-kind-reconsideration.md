# Topology Standalone Kind — Reconsideration

- Status: current — full reversion implemented (Phases 0-4, 6); Phase 5
  skipped per user direction (see `## Implementation Plan`)
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

## Implementation Plan

Decision: proceeding with **full reversion** (not "support both") — per
user direction 2026-10-01. The `cfg-int-deployment-v2` `control` migration
breakage is accepted as a known, one-time cost; it is not yet ported back
into this repo, so nothing here breaks a committed fixture until Phase 5.

Each phase must leave the full check suite green
(`mypy src` / `ruff check --fix src tests` / `lint-imports` / `pytest -q`)
before the next phase starts — no phase hands off a red suite.

- [x] **Phase 0 — New ADR.** Wrote `docs/decisions/0028-topology-inline-reversion.md`,
  superseding ADR-0011's justification (2) only (cross-workspace reuse/
  standalone-kind promotion); justification (1) — decoupling grouping from
  provisioning tooling — is unaffected and stands. Per `docs/decisions/README.md`'s
  immutable-ADR convention, old ADRs are **not** rewritten in place — only
  their `- Status:` line gets a one-line pointer to the new ADR. Updated:
  ADR-0011's and ADR-0012's Status lines. **Not** ADR-0013/ADR-0014 — those
  describe `TopologyConfigModel` (`kind: topologyconfig`), a separate,
  confirmed-unaffected kind (see `## Related Decisions` above).

- [x] **Phase 1 — Schema.** In `src/strata/models/`:
  - Move `TopologySpecModel`, `TopologyComponentModel`,
    `TopologyNamespaceReferenceModel`, `TopologyVolumeModel` into
    `workspace_model.py` (keep `topology_config_model.py`'s own imports of
    these working — check for a circular-import risk before moving, since
    `topology_config_model.py` currently sits below `topology_model.py`
    in the layer order).
  - Add `name: PlatformName` to `TopologySpecModel` (replaces
    `TopologyMetaModel.name`); delete `TopologyMetaModel` and the
    `TopologyModel` root wrapper; delete `topology_model.py` once empty.
  - Delete `PlatformKind.TOPOLOGY` from `common_models.py`.
  - Change `WorkspaceSpecModel.topology` from `list[PlatformName] | None`
    to `list[TopologySpecModel] | None`.
  - Add two `model_validator(mode="after")` methods on `WorkspaceSpecModel`
    (same-document now, no index lookup needed) replacing the
    component/namespace-existence half of
    `WorkspaceService.validate_topology_references()`: each
    `topology[].components[].resource` must be in `spec.resources`; each
    `topology[].namespaces[].namespace` must be in `spec.namespaces`.
  - Update/delete `test_models_topology.py` (move surviving cases into
    `test_models_workspace.py`).

- [x] **Phase 2 — Services.** In `src/strata/services/`:
  - Delete `topology_service.py` and `test_services_topology.py`.
  - In `workspace_service.py`: delete `validate_topology_references()`
    (superseded by the Phase 1 model validators); keep
    `validate_topology_components()` but simplify its signature — it no
    longer needs a separate `topology_models` dict (reads
    `self.model.spec.topology` directly), only `topology_config_models`
    for the ADR-0013/0014 cross-check.
  - Update `test_services_workspace.py` accordingly.

- [x] **Phase 3 — Controllers and integrations.**
  - `solution_controller.py`: remove `PlatformKind.TOPOLOGY: TopologyService`
    from the model registry.
  - `build_controller.py`: remove the `_lookup_all(index, PlatformKind.TOPOLOGY, ...)`
    call; `resolved_context.py`: remove `ResolvedWorkspaceGraph.topologies`.
  - `terraform_projection.py`: rewrite `_build_topologies_payload()` to
    iterate `graph.workspace.spec.topology` directly instead of
    `graph.topologies[name]`.
  - `semantic_checks.py`: re-point the ~3 `PlatformKind.TOPOLOGY`/
    `TopologyModel` call sites at the inline data.
  - `reference_fields.py`/`references.py`: remove `PlatformKind.TOPOLOGY`
    from the referenceable-kinds set.
  - Update `test_solution_controller.py`, `test_solution_context.py`,
    `test_build_controller.py`, `test_semantic_checks.py`,
    `test_references.py`, `test_integrations_terraform_projection.py`,
    `test_commands_validate.py`.

- [x] **Phase 4 — This repo's own fixtures.** Merge
  `config/workspaces/topology.yaml` + `config/templates/topology.template.yaml`
  into their owning `workspace.yaml`/workspace template; delete the
  standalone files. Confirm `test_shipped_example_solution_loads_cleanly`'s
  pre-existing unrelated failure is unchanged (not newly broken, not
  accidentally fixed in a way that masks the real issue).

  **Note on phase boundaries**: Phases 1-4 necessarily landed together in
  one pass, not sequentially with a green suite in between each — `PlatformKind.TOPOLOGY`/
  `TopologyModel`/`TopologyService` are consumed across models, services,
  controllers, integrations, *and* this repo's own shipped `config/`
  fixture (discovery hard-errors on an unrecognized `kind:`, so the fixture
  migration wasn't deferrable either). Splitting them would have meant
  committing a deliberately broken intermediate state. The full check
  suite (mypy/ruff/import-linter/pytest) is green after all four combined —
  1662 passed, 1 known pre-existing unrelated failure
  (`test_shipped_example_solution_loads_cleanly`'s `RESOURCE` set mismatch).

- [x] ~~**Phase 5 — Local checkout fixtures.**~~ **Skipped per user direction
  (2026-10-01).** Would have merged `.v2-cfg/topologies/spoke-cluster.yaml`
  into `.v2-cfg/workspaces/spoke.yaml` and `.v2-haven/topologies/hetzner-hearth.yaml`/
  `hetzner-forge.yaml` into `.v2-haven/workspaces/haven-platform.yaml`.
  Confirmed both `.v2-cfg/` and `.v2-haven/` are fully gitignored (each has
  its own `.gitignore` of `*`/`!.gitignore`) — neither is committed, neither
  is exercised by the automated test suite, so leaving them on the old
  standalone-reference shape has no effect on this repo's own correctness;
  they'll simply keep using a form `WorkspaceModel` no longer parses if
  anyone runs `strata validate`/`build run` against them until migrated by
  hand later.

- [x] **Phase 6 — Documentation.** Updated `v2-schema-overview.md` (removed
  the `topology` kind row, folded its status into the `workspace` row),
  `solution-loading-and-phase2-validation.md` (removed the deleted
  `validate_topology_references()`/`TopologyService._validate_dynamic()`
  rows, simplified `validate_topology_components()`'s description, updated
  the Status line's now-smaller validator count), `docs/config/workspace.md`
  (merged `docs/config/topology.md`'s schema in as a "Topology grouping"
  section, updated the inline example), `docs/config/topologyconfig.md`/
  `docs/config/readme.md`/`docs/index.rst` (fixed the now-dead link to the
  deleted `topology.md`). Confirmed `terraform-tfvars-parity.md` needed no
  change (its `TopologyComponentModel.modules` mentions are about ADR-0023's
  unrelated deliberate-omission decision, not the standalone-vs-inline
  question). Revised `gap_fit_v1.md` gap #5 (now genuinely less migration
  work than first documented, not more) and flagged its `.v2-cfg` bullet as
  stale. Updated `docs/how-to/migrate-v1-workspace-topology-provisioning.md`
  in place (not retracted — the provisioner/execution-recipe half it
  documents is unaffected and still accurate; only the topology-splitting
  half, now unnecessary, was rewritten). `docs/config/topology.md` deleted.

## Changelog

- 2026-10-01: Created. Captures the evidence gathered (real `control` stack
  migration, `.v2-haven` topology instances, `WorkspaceResourceModel`'s
  type/instance split) and the full cost/design of reverting Topology to
  inline, per user request to centralize this before any decision is made.
- 2026-10-01: Decision made — proceeding with full reversion. Added phased
  `## Implementation Plan` (Phase 0 ADR through Phase 6 documentation).
- 2026-10-01: Phase 0 done — [ADR-0028](../decisions/0028-topology-inline-reversion.md)
  written; ADR-0011/ADR-0012 Status lines updated to point at it (one-line
  pointers only, per the repo's immutable-ADR convention — bodies untouched).
  ADR-0013/ADR-0014 deliberately left alone (separate `TopologyConfigModel`
  kind, unaffected).
- 2026-10-01: Phases 1-4 done (schema, services, controllers/integrations,
  this repo's shipped `config/` fixture) — landed together, see the note
  under Phase 4 for why they couldn't be split. `TopologyComponentModel`/
  `TopologySpecModel`/etc. now live in `workspace_model.py`;
  `WorkspaceSpecModel` gained two Phase 1 model validators replacing
  `WorkspaceService.validate_topology_references()` (deleted);
  `validate_topology_components()` simplified (no longer needs a separate
  `topology_models` dict). Full check suite green: mypy (121 files), ruff,
  import-linter, pytest (1662 passed, 1 known pre-existing unrelated
  failure). Phase 5 (`.v2-cfg`/`.v2-haven` local fixtures — not consumed by
  any automated test) and Phase 6 (remaining documentation sweep) still open.
- 2026-10-01: Phase 5 skipped per user direction — both `.v2-cfg/` and
  `.v2-haven/` confirmed fully gitignored (not committed, not exercised by
  the test suite), so left on the old standalone-reference shape rather
  than migrated by hand. Corrected a wrong claim from an earlier session's
  summary that `.v2-haven` was committed — it is not (see repo memory).
- 2026-10-01: Phase 6 done — full documentation sweep (schema overview,
  Phase 2 validation design doc, `docs/config/` merge + dead-link cleanup,
  `gap_fit_v1.md` gap #5 revision, migration guide updated in place).
  `Status` updated to `current` — all phases either implemented or
  deliberately skipped with a recorded reason. This design doc's own job
  is done; further changes to the inline-topology schema itself belong in
  ADR-0028's own evolution or a fresh design doc, not here.
