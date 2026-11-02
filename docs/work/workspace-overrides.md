# Workspace Overrides (per-deployment rebinding of workspace content) — Work

- Status: in-progress — providers and assets done and graduated to
  [provider-slots.md](../design/provider-slots.md) and
  [asset-management.md](../design/asset-management.md); any other category still open
- Last updated: 2026-10-20

> Formerly `environment-overrides.md`. The decision below is that rebinding lives on the
> **deployment**, not the environment.

## Overview

**Gap.** A v2 workspace is the shared platform recipe, and every binding in it is fixed: which
provider documents it targets (`spec.providers`), which assets a step consumes
(`spec.assets`/`step.assets`), which resources/namespaces it uses. Nothing downstream — not an
environment, not a deployment — can say "same workspace, but for *this* deployment use a different
X". The only per-deployment overlay that exists is `kind: version` pins (remote refs, image tags,
chart versions, artifact tags), which change *versions*, never *which document* is bound.

v1 had this: `environment.spec.overrides` (`EnvironmentOverridesModel`) with `providers`,
`remotes`, `resources`, `modules`, `properties`, `includes`, `output_files`. v2 deliberately did
not port any of it (`environment_model.py` docstring: 0 uses of `overrides` across 26 real
environment documents) and parked `overrides.remotes` with *"Re-add when Deployment exists to
select an environment"* — that condition is now met.

Two concrete needs hit the same gap:

- **Providers** — one workspace deployed to different regions/accounts (westeurope vs
  northeurope). **Done** — see [provider-slots.md](../design/provider-slots.md).
- **Assets** — one APIM workspace, ~300 customers, each needing its own asset (API definitions)
  bound to the same step slot. **Done** — see
  [asset-management.md](../design/asset-management.md); the location half is Option 3b in
  [remotes-at-scale.md](remotes-at-scale.md).

This doc keeps the **scope decisions and the shared rule** that apply to every category; each
category graduates to its own design doc once built.

## Decision summary (2026-10-19)

- **Where:** on the **deployment**, as separate top-level fields — `deployment.spec.providers`
  (built), later `deployment.spec.assets` / `deployment.spec.resources` as needed. **Not** on the
  environment (neither `environment.spec.overrides.*` nor `environment.spec.providers`).
- **What:** **rebind only** — a workspace slot is pointed at a different document. No field
  patches; per-deployment values already flow through environments.
- **Shape:** the workspace declares named slots with a default document; the deployment lists the
  slots it rebinds, each linked to another document of the same kind (identity by name per
  ADR-0015).
- **Reuse:** `extends`/`partial` — a base deployment binds once, every deployment that needs it
  extends it.

### Why the deployment, not the environment (consistency)

v2 already splits the two kinds by responsibility, stated in `DeploymentSpecModel.extends`'s own
description: *"Distinct from `environments` layering, which composes **values**; this composes
**structure**."*

| Kind            | Role                                                  | Holds today                                                                               |
| --------------- | ----------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| **Environment** | Value source for `${var:}`/`${secret:}`/`${feature:}` | `variables`, `secrets`, `features`, `properties` — nothing structural                     |
| **Deployment**  | Composition root: *which documents* run               | `workspace`, `environments`, `tenant`, `version`, `stages`; `extends`/`partial` for reuse |

Choosing which provider/resource/asset fills a workspace slot is a **document selection** — the
same category as `workspace:` or `version:`, which already live on the deployment.

- `environment.spec.providers`/`.resources` would turn a value document into a structural one.
- `environment.spec.overrides.*` (v1's shape) is the same, one level deeper — and v1 shipped it
  with zero tracked use.
- Environments stack (tenant environments, then the deployment's own), so a structural rebind
  could come from any layer and would need its own precedence rule. On the deployment, precedence
  is simply the existing `extends` rule: child wins.
- The objection to binding on the deployment — "every deployment repeats the binding" — is solved
  by an existing mechanism: a `partial: true` base deployment carries the binding, children
  `extends` it.

### General rule (all categories)

1. The **workspace** declares named **slots**, each with a default document.
2. The **deployment** rebinds slots by name, one top-level field per category.
3. **Rebind only**, never field patches.
4. Reuse via **`extends`/`partial`**, child wins.
5. Separate top-level fields per category (`providers`, `assets`, `resources`). *(An early draft
   justified this with "`extends` replaces top-level fields wholesale" — wrong; see "How
   `extends` merges" below. Separate fields are kept for readability and per-category
   validation, not because a grouping field would break.)*
6. **Every list of named items merges by its identity key** across `extends` (and any other
   layering): same key → child wins (deep-merged), new key → appended. Plain value lists replace.
   One rule for every key, declared once (a merge-key table), not a special case per field.
   Built for `stages`, `providers` and `assets`; extending it is Q7.

### How `extends` merges (verified, `merge_deployment_specs()`)

`deployment_service.py`'s `merge_deployment_specs()`. The `extends` field description used to say
"top-level fields are replaced"; that was stale and has been corrected.

| Shape                                          | Rule                                                                                                             |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Dicts (`locking`, `properties`, `custom`, ...) | Deep-merged per leaf key (`deep_merge`), child wins — deliberately diverges from v1, which replaced whole values |
| `stages`, `providers`, `assets`                | Merged by key (`_LIST_MERGE_KEYS`: `step`, `slot`, `slot`), new keys appended                                    |
| `environments`                                 | Base list, then child's (deduplicated) — later wins at value resolution                                          |
| Any other list                                 | **Replaced wholesale** by the child's                                                                            |

Still replaced wholesale: `configurations` (a list of names, unlike `environments`) and lists
*inside* a stage (`health_checks`, keyed by `name`, replaced by `deep_merge`).

### What a DevOps engineer expects

The common convention across the tools this audience already uses: **a keyed collection merges
by key; an unkeyed list replaces.**

- Kustomize strategic-merge-patch: lists merge by a declared `patchMergeKey` (`containers` by
  `name`, `env` by `name`); unkeyed lists replace.
- Docker Compose override files: `services` merge by service name.
- Helm values: maps deep-merge, lists replace (which is why charts prefer maps for keyed things).
- Terraform `merge()` on maps: later map wins per key.
- strata's own environments: `merge_environment_models()` merges variables/secrets/features **by
  `key`**, later wins.

So "the same merge as environments" — later wins *per key* — is exactly the expectation, and it
should hold for **every** keyed list, not only providers. `merge_deployment_specs()` now does this
through one merge-key table (`stages`→`step`, `providers`→`slot`, `assets`→`slot`); `environments`/`configurations`
(name lists) keep append-dedupe; anything not in the table replaces. The remaining keyed list
(`health_checks`→`name`) is Q7. Alternative considered: make bindings
**maps** (`providers: {azure: azure-north}`) — per-key via `deep_merge` with zero new code, but
inconsistent with the list shape on the workspace side.

## Current Design / Progress

### Scope inventory — v1 categories vs v2

| v1 `overrides.*`          | What it did (v1)                                                      | v2 today                                                    | Candidate?                                                      |
| ------------------------- | --------------------------------------------------------------------- | ----------------------------------------------------------- | --------------------------------------------------------------- |
| `providers`               | Swap provider **file** per env + patch provider properties (ADR-0036) | `{slot, provider}` slots + `deployment.spec.providers`      | **Done** — [provider-slots.md](../design/provider-slots.md)     |
| `remotes`                 | Override a remote's `reference` only                                  | Covered by `version.spec.pins.remotes`                      | Probably no — overlaps version pins                             |
| `resources`               | count / configuration / enabled per resource                          | None                                                        | Later, evidence needed                                          |
| `modules`                 | enabled / slot_type / configuration / per-service image               | Images covered by `pins.images`; rest none                  | Later, evidence needed                                          |
| `properties`              | Override workspace properties                                         | Environment `properties` already merged into the deployment | Already covered                                                 |
| `includes`/`output_files` | Terraform file merging / output shaping                               | Not ported (pure build machinery)                           | No                                                              |
| *(new)* `assets`          | —                                                                     | `{slot, asset}` slots + `deployment.spec.assets`            | **Done** — [asset-management.md](../design/asset-management.md) |


## Related Decisions

- [provider-slots.md](../design/provider-slots.md) — the first category, built: provider slots on
  the workspace, rebinding on the deployment. The pattern every later category reuses.
- [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md) — workspace/provisioning
  decoupling; removed v1's per-stage provider/topology binding.
- [ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md) — identity is
  `(kind, meta.name)`, never a file; why a rebind needs a stable slot name.
- [remotes-at-scale.md](remotes-at-scale.md) — the asset slot-binding need (Option 3b, S1) that
  became asset slots plus `location`: [asset-management.md](../design/asset-management.md).
- v1 ADR-0036 (`strata/docs/decisions/0036-workspace-provider-environment-overrides.md`, the v1
  repo) — the original provider-override design.

## Remaining Work / Open Questions

- **Assets.** Built and graduated: workspace slots, `deployment.spec.assets`, the `assets: slot`
  merge key, validation, the self-contained asset `location` (remotes-at-scale.md Option 3b) and
  `pins.assets` — see [asset-management.md](../design/asset-management.md).
- **Q7 — Remaining keyed lists.** Add `health_checks`→`name` (inside a merged stage, today
  replaced) and decide whether `configurations` (a name list) should append like `environments`.
  Changes behaviour for stages that set `health_checks` in both base and child, so it needs its
  own test and note.
- **Q6 — Other categories.** After assets, do `resources`/`modules` rebinding have any real
  evidence, or stay out?
- **`depends_on` editor schema.** `WorkspaceResourceModel.depends_on` accepts a single string as
  shorthand for a one-element list, but its exported schema is array-only, so the shorthand is
  flagged in the editor (the same class of gap `scalar_shorthand_schema()` fixed for provider
  slots and version pins, here on a list field). Not part of this feature.

## Changelog

- 2026-10-19: Created. Scope inventory of v1 `overrides.*` vs v2; v1 provider override traced
  (ADR-0036: swap file per environment, zero use in four consumer repos); direction decided —
  bindings on the **deployment**, rebind only, reuse via `extends`/`partial`, not on the
  environment, following v2's values-vs-structure split; shared rule for all categories; the
  `extends` merge facts corrected against `merge_deployment_specs()` and the merge-key rule added.
- 2026-10-19: Providers built in five phases (merge-key table, workspace slots, deployment
  bindings, validation, docs + dogfood) and graduated to
  [provider-slots.md](../design/provider-slots.md); the provider-specific analysis (options,
  constraints, technical design, plan) moved there as design + History. Renamed from
  `environment-overrides.md`. Q1-Q5 closed (need confirmed; deployment binding; rebind only;
  `extends` precedence; `slot`/`provider` field names kept).
- 2026-10-19: Assets designed as the next category.
- 2026-10-20: Assets built in six phases (deploy/type/path fixes, workspace slots, deployment
  bindings, asset `location` with `pins.assets`, validation, docs + dogfood) and graduated to
  [asset-management.md](../design/asset-management.md). The shared merge-key table now covers
  `assets`.
