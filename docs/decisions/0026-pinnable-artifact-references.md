# Pinnable artifact references — `kind: artifact`

- Status: accepted
- Date: 2026-09-27

Related: [docs/archive/artifact-references.md](../archive/artifact-references.md)
(full design history, evidence, and rejected-alternative reasoning — this
ADR summarizes the outcome, that doc is the living record)

## Context and Problem Statement

A container image (or chart) reference living on a plain `constant`/
`environment` variable has no way to be version-pinned: `kind: version`'s
`VersionPinsModel` only targets `ModuleServiceModel.image` (a workload
strata itself deploys via Compose/Helm), `SourceModel.chart_version`, or a
`fetch: strata` remote's `reference`. A variable holding a raw image string
is invisible to it.

Real evidence (`config-deploy`'s `stacks/ring/environment-dispatcher.
yaml`/`environment-datahub.yaml`/`environment-sci.yaml`) shows at least 7
distinct real image references declared this way, several explicitly
meant to share one pin value, flowing into external Terraform provisioners
(`dispatcher_api`/`datahub_api`) strata never models as a `resource`/
`topology` — there is no attachment point for a `Module` there. The
target repo's own `adr-0013-external-app-dispatcher-api.md` independently
anticipates this exact gap, unprompted.

## Considered Options

- **Stretch `Module`** to cover it (relax `default_labels`, treat it as a
  reference-only module). Rejected: `ModuleServiceModel` is still full of
  Compose/Helm-only fields, and — more fundamentally — there is no
  namespace/topology component to attach a Module *to* in the real
  motivating case.
- **`kind: version` overlays a variable's value directly** (a
  `pins.variables` category keyed by variable name). Rejected: breaks
  strata's identity-reference convention — every other real thing is its
  own top-level, independently-referenceable document.
- **Promote `SourceModel` itself into a top-level, referenceable kind.**
  Rejected: `sync_source()` always materialises a `SourceModel` by value
  into the caller's own `build_path`; giving it cross-document identity
  forces an undesigned "resolve once vs. resolve per-referencer" decision
  for zero evidenced benefit, and touches two already-working mechanisms.
- **A new, dedicated `kind: artifact`** (chosen) — see Decision Outcome.

## Decision Outcome

Chosen: **a new, dedicated `kind: artifact`**, because it's the only
option matching strata's existing identity-reference convention
(`(kind, meta.name)` indexing, ADR-0015) without touching or repurposing
any existing, working mechanism. Real-multiplicity evidence (7+ distinct
image references, several meant to share one pin value) rules out a
one-off embedded field.

Shape: `ArtifactSpecModel` — `image_name` (identity, never pin-overlaid),
`image_tag` (pin-overlayable), `registry` (free text, not a
`SolutionRemoteModel` reference — strata never fetches an image, so
`SourceModel`'s fetch-oriented `remote` doesn't apply), optional
`integration` (capability: `sources`, reusing the existing mechanism
rather than inventing a new one), and `properties`/`configuration`/
`custom` matching every other kind's convention.

Two, non-overlapping consumption paths:
1. **`ModuleServiceModel.artifact`** — a direct
   `References(PlatformKind.ARTIFACT)` field, mutually exclusive with
   `image: str`, for the strata-owned Compose/Helm case.
2. **`store: artifact` + a new `field` selector** on `VariableStoreModel`
   (`image_name`/`image_tag`/`image_ref`) — the only channel that can
   reach an arbitrary, externally-defined Terraform variable name on an
   opaque external provisioner, which is the real motivating case.

`kind: version` gained a fourth, separate pin category — `pins.artifacts`
— rather than overloading `pins.images` (which stays unambiguously
`ModuleServiceModel.name`, zero disambiguation logic, zero risk to the
existing, real, production-proven Compose/Helm image-pinning path
confirmed in `haven/versions/prd.yaml`).

### Consequences

- Good: zero blast radius on existing, working mechanisms
  (`SourceModel`/`sync_source()`, `pins.images`/`pins.charts`,
  `Module.spec.source`) — every addition is new fields/categories, nothing
  existing changed shape.
- Good: `store: artifact` resolves once at build time into
  `build_value_references()`'s existing pipeline (same treatment as
  `constant`/`environment`) — no new deploy-time-only mechanism, no
  regression in when the value becomes usable.
- Good: the real "four images share one build tag" case is expressible
  today (four `pins.artifacts` entries with the same `version`, each still
  independently carrying its own `status`/`reason`/`reviewed`).
- Both Path 1 and Path 2 are now fully implemented (Path 1 shipped
  2026-10-06, via `resolve_artifact_services()` in
  `workload_controller.py`, mirroring `apply_version_pins()`'s own
  overlay shape — `compose.py`/`helm.py` needed zero changes). The
  `ModuleServiceModel.artifact`-has-no-resolution-wiring gap below is
  historical, kept for the record of the original tradeoff, not current
  state.
- ~~Bad: `ModuleServiceModel.artifact` (Path 1) has no resolution wiring
  yet~~ — it validates and round-trips, but nothing renders it into an
  actual image in Compose/Helm output. Deliberate, not an oversight — Path
  1 resolution wiring was deferred until a real module sets `.artifact`
  (checked directly: zero real haven module did at the time).
- Bad: two ways to reference an artifact (Path 1 vs. Path 2) is more
  surface area than one — judged necessary rather than redundant, since
  each solves a structurally different problem (strata owns vs. does not
  own the target field).
