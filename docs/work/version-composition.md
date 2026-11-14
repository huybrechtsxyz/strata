# Version Composition - Work

- Status: in-progress - Phases 1-8 built on branch `feat/version-multiple-workspaces` for 2.7.0 (not yet
  released); open questions 1, 3 and 5 remain
- Last updated: 2026-11-14

## Overview

A `kind: version` document today is one flat set of pins, and a deployment names exactly one. A document's
`spec.workspace`, when unset, is fixed by the first deployment that references it, so one document cannot
serve two workspaces (`version_workspace_mismatch`). That is enough for one product per ring. It breaks
down when a ring runs several products that share something:

- **One release per ring across several workspaces.** config-repo runs one workspace per product
  (Staging, DataHub, Binocs, ...). A ring release is one version document per product, edited in
  lockstep, and a document shared by two workspaces fails validation.
- **A shared artifact in several versionings.** Anything every product uses (the `iac` remote the
  product roots come from, a shared image, the core modules) is pinned once per product document.
  Nothing says those copies agree, so one ring can silently run two versions of the shared part.
- **A customer that differs from its ring on a platform pin** needs a document repeating every
  platform pin ([remotes-at-scale.md](remotes-at-scale.md) Q6).

This doc designs **version composition at the deployment**: a deployment names one or more version
documents, and the pins it runs with are their union. `strata validate` builds, per deployment, a matrix
of every pinned target with the document that pins it, and rejects a target pinned by two of them.
Build and deploy resolve the same matrix through one resolver, and the build writes the result as a
version file of its own. A version document no longer declares a workspace: which deployments, and so
which workspaces, use it follows from the deployments that list it.

Strata's principle is *configure once, reuse*: every pin has exactly one owning document per deployment,
and every deployment that needs it names that document instead of copying the pin.

The first draft of this doc (2026-11-01) composed inside the version document (`spec.include` plus
`spec.overrides`). It was replaced on 2026-11-14 by composition at the deployment; see "Alternatives
considered" and the Changelog.

## Current Design / Progress

Phases 1-8 are built (see the Implementation Plan), all for 2.7.0: `Version.spec.workspace` is removed in
the same release (ADR-0065). This section is the design as built.

Where the code is:

- `controllers/version_composition.py`: `compose_versions()` (the union and `version_pin_conflict`),
  `effective_version()`, `deployment_version()`, `pin_dropped_findings()`, `version_scope()`,
  `listing_deployments()` and the matrix checks `check_version_composition()`, run from
  `run_semantic_checks()`.
- `controllers/pinnable_targets.py`: `pinnable_targets()` (V5).
- `models/version_model.py`: `ComposedVersionModel`, `pin_owner()`, `version_names()`; in the models layer so
  the SBOM collectors (integrations) can name a pin's owner too.
- `models/deployment_model.py`: `VersionNames`, `VersionPins` (what `--pin` passes down),
  `version_name_list()`.
- `controllers/promotion_controller.py`: the view and `apply_promotion()` built on the deployments (V10, V11).
- Tests: `tests/strata/controllers/test_version_composition.py` follows the Lifecycle below.

### Requirements

| #   | Requirement                                                                                                                                       | Source                                  |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------- |
| R1  | One version document can serve deployments of several workspaces                                                                                 | config-repo request                     |
| R2  | A shared artifact is pinned in one place and reused, never copied                                                                                 | "configure once, reuse"                 |
| R3  | A deployment can never run with two values for one pin; a ring running two values of a shared pin is visible                                      | the core-in-several-versionings problem |
| R4  | A product can still be promoted through rings on its own                                                                                          | config-repo ADR-0006                    |
| R5  | A customer can still move on its own cadence (`promote apply --pin`)                                                                              | promotion.md S1-S10                     |
| R6  | A deployment can differ from its ring without repeating the whole ring                                                                            | remotes-at-scale.md Q6                  |
| R7  | Every applied pin is logged with the document it came from                                                                                        | ADR-0019 decision 7                     |
| R8  | `reason` on a held pin is never lost, and tooling edits the document that owns a pin                                                              | ADR-0019, version-lifecycle.md          |
| R9  | Existing single-document solutions keep working unchanged                                                                                         | -                                       |
| R10 | What a build ran, at which value and from which version document, is readable from the build output, without digging through git history          | -                                       |

### Prior art

| Tool                        | Composition                                 | Same key from two sources     | Taken here                                    |
| --------------------------- | ------------------------------------------- | ----------------------------- | --------------------------------------------- |
| Kustomize `resources`       | many bases, unioned                         | error (duplicate resource ID) | yes: a deployment's documents are a disjoint union |
| Maven BOM `import`          | a release file lists versions of many parts | first import wins, silently   | the BOM idea yes, the silent rule no          |
| Helm `-f a -f b`            | ordered layering                            | the last file wins, silently  | no: last-wins is the drift R3 forbids         |
| strata `Deployment.extends` | one parent, deep merge                      | the child wins                | its list rule only: `version` appends base-first, like `environments` |

### Shape

**Deployment.** `spec.version` takes one name or a list of names, each a `References(PlatformKind.VERSION)`
(the `ParamSetNames` pattern in `template_model.py`):

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: datahub-prd
spec:
  workspace: datahub
  version: [prd-core, prd-datahub]   # a single name still works
  environments: [prd]
```

**Version document.** No new field; `spec.workspace` is removed (V4). A document is shared or
product-specific by use, and its name says which:

```yaml
# versions/prd-core.yaml - listed by every product's prd deployments
spec:
  pins:
    remotes: { iac: v2.1.0 }
---
# versions/prd-datahub.yaml - listed by datahub's prd deployments
spec:
  promotion: { ring: prd, order: 3 }
  pins:
    artifacts: { datahub-api: 1.4.0 }
```

### Decisions

- **V1 - `Deployment.spec.version` is a name or a list of names.** A single name is a one-element list.
  Order carries no meaning, because nothing is resolved by order (V3). The same name twice in **one**
  written list (or in one `--pin` set) counts once and is a warning, `version_listed_twice`.
- **V2 - `extends` appends `version` base-first**, like `environments` (docs/config/deployment.md),
  de-duplicated silently: a deployment repeating a document its base already lists is not a mistake.
  A partial base `[prd-core]` plus a deployment's own `[prd-datahub]` gives both. A deployment cannot
  remove a document its base lists; it extends a different base instead.
- **V3 - The pin matrix.** For each deployment (after `extends`), the effective pins are the union of
  the pins of every listed document. The matrix has one row per `category/target`: the value and the
  document that owns it. The same `category/target` in two listed documents is an **error**,
  `version_pin_conflict`, naming both documents, even when the values are equal: one owner per pin means
  `version set`, `version update` and `promote apply` never have to choose between two documents, and
  the next bump of one copy cannot silently split the deployment. The message names the fix: keep the
  key in one of the two, or move it into a document both deployments name. Migrating per-product
  documents with copied pins is therefore one step per shared key: move it into a shared document,
  remove it from the product documents, add the shared document to the base deployment.
- **V4 - `Version.spec.workspace` is removed.** Which workspaces use a document follows from the
  deployments that list it, the way it already does for artifacts, modules and every other shared
  document. Each of the field's jobs is done from the deployment side:

  | Job of `spec.workspace` today                                         | Done instead by                                                        |
  | --------------------------------------------------------------------- | ---------------------------------------------------------------------- |
  | Limit: only that workspace's deployments may name the document        | `version_document_unused` (V5) and `version_pin_conflict` (V3)         |
  | Unset: the first deployment fixes it, then `version_workspace_mismatch` | nothing: any deployment may list any document, which fixes the report |
  | Promotion grouping in `build_promotion_view()`                        | the deployments' workspaces (V10)                                      |
  | `version new --workspace` (required) checks it exists and writes it   | nothing: `--workspace` is dropped (V13); it never picked pins          |
  | `resolved.yaml` `promotion.workspace` fallback                        | the deployment's workspace only                                        |

  What is lost: the early error when a deployment lists another product's document. It becomes a
  warning (`version_document_unused`) when that document pins nothing the deployment needs, or an
  error (`version_pin_conflict`) when it repeats a listed key. A document pinning a target both
  products use (an image name present in both workspaces) is not caught. Naming carries what the field
  said (`prd-datahub`), and a new `strata version show NAME` lists the deployments that use a document
  (V13).

  Removed in 2.7 (ADR-0065), in one step: with two users, a release-long deprecation (planned as
  "enforced and warned in 2.7, removed in 3.0", ADR-0064) bought nothing but a second way to say where a
  document applies. A document that still declares the field fails to load with a message naming the
  removal and the fix (`VersionSpecModel.reject_removed_workspace()`), not pydantic's generic "extra
  inputs are not permitted".
- **V5 - Coverage: what a deployment needs, and whether the matrix has it.** A deployment needs a pin
  for the targets it actually uses: the module services' images, Helm modules' charts, remotes with
  `fetch: strata`, artifacts it reaches (`referenced_artifacts()` per deployment) and assets with their
  own location. One function computes that, `pinnable_targets(index, deployment) -> set[(category,
  target)]`, and every check and command that asks "what does this deployment need" uses it (V5, V7,
  V11, V13). Images and artifacts per deployment exist today (`effective_images`, `artifact_usage`);
  remotes and assets per deployment do not and are traced from the workspace's provisioner sources and
  the module and asset locations to the remote. A use it misses is a false finding or a skipped
  promotion, so it is built and tested per category before anything depends on it. Against the matrix:
  - needed and pinned: fine, whichever listed document pins it;
  - needed, not pinned: unchanged from today - the spec's own value applies, and where there is none
    the existing error stands (`unavailable_artifact_token` and friends);
  - pinned by a listed document but needed by none of the deployments that name that document:
    information, `version_pin_unused`. Not a warning: a shared document may pin ahead of the first
    deployment that needs it, as an artifact can be staged before use (`artifact_unused`);
  - a document the deployment lists **itself** that pins **nothing** this deployment needs: a
    **warning**, `version_document_unused`, naming the deployment and the document. This is the check
    that replaces the workspace limit (V4): `datahub-prd` listing `prd-binocs` by mistake. Documents
    inherited through `extends` are not judged here: a base giving every product `prd-core` would warn
    for each product that happens to use nothing in it, and an unused pin there is `version_pin_unused`
    already. A document with no pins yet (a new ring) is exempt: it contributes nothing and cannot be
    wrong.
  Pin targets that do not exist at all stay an error, as today (`check_version_pins()`).
- **V6 - One resolver, every consumer.** `effective_version(index, deployment, pins=None) ->
  EffectiveVersion` returns a merged `VersionModel` (whose `spec.pins` is the matrix), the ordered list
  of document names, and a map `category/target -> owning document`, plus its `Diagnostics` (V3). The
  merged model's `meta.name` is the deployment's name and `spec.promotion` is the ring the listed
  documents agree on (V9); the workspace is the deployment's own. Most consumers already go through
  one function, `value_controller.resolve_version()` (audit, build, effective images, image tag policy,
  resolved context), which becomes a thin wrapper over the resolver. The sites that look the document
  up themselves switch to it directly: `value_controller.py` (`--pin` handling in
  `resolve_deployment()`), `artifact_tokens.py`, `deployment_resolution.py` (`list_deployments()`),
  `artifact_usage.py`, `promotion_controller.py` and `semantic_checks.py`. Pin application
  (`workload_controller.apply_version_pins()`, `build_controller.apply_remote_version_pins()`) takes
  the owner map for logging (V14). A deployment naming one document resolves to that document's pins
  (R9).
- **V7 - `--pin` repeats and replaces the whole list.** `build run`, `deploy run`/`plan`, `deploy
  images`, `policy` and `integration` take `--pin NAME` any number of times; what is passed is exactly
  the list for that run, checked by V3 and V5 like a written list. Not "swap one document": a run's
  versions are then readable from the command line alone. `pinned_by` (V8) is `--pin` when the flag
  was given. The risk of a whole-list replacement is leaving a document out: a needed target that is
  not pinned falls back to the spec's own value (V5), so `--pin qas-datahub-rc` without `qas-core`
  would quietly run `iac` at the remote's own `reference`. So under `--pin`, a target the deployment's
  own list pins but the `--pin` list does not is a **warning**, `version_pin_dropped`, naming the
  target, its value under the deployment's list, and the document that pinned it.
- **V8 - The build writes the effective version file.** `build run` writes `version.yaml` next to
  `resolved.yaml`: an ordinary `kind: version` document holding exactly the pins this deployment uses
  (the needed-and-pinned rows of V5, not every pin of every listed document) and nothing else. Where
  each pin came from is the build's record, so it goes in `resolved.yaml`, which already records
  `version` and `promotion`:

  ```yaml
  # builds/datahub-prd/version.yaml - just the pins, reusable as-is
  apiVersion: strata.huybrechts.xyz/v2
  kind: version
  meta:
    name: datahub-prd                       # the deployment
  spec:
    pins:
      remotes:   { iac: v2.1.0 }
      artifacts: { datahub-api: 1.4.0 }    # status, reason, reviewed kept as written in the owner
  ---
  # builds/datahub-prd/resolved.yaml - the build's record (existing file, version section extended)
  version: [prd-core, prd-datahub]          # was one name; the documents the build used
  pinned_by: spec.version                   # or --pin
  pin_sources:                              # each pin in version.yaml, and the document it came from
    remotes/iac: prd-core
    artifacts/datahub-api: prd-datahub
  promotion: { workspace: datahub, ring: prd, order: 3 }
  ```

  `version.yaml` loads with `VersionModel`, so it can be diffed between two builds, read by a pipeline,
  or copied into a solution to reproduce a run, without provenance that goes stale once the copy is
  edited. Provenance is not written as `meta.annotations` on it: annotations are free-form and nothing
  checks them. It carries no `spec.promotion`: copied into a solution, a tagged copy would claim a ring
  slot (`promotion_slot_duplicate`, or `promotion_orphan_ring` when nothing lists it); the ring is in
  `resolved.yaml`. The audit run record names no version document of its own: it references
  `resolved.yaml` by path and hash, and `resolved.yaml` now carries the list, `pinned_by` and
  `pin_sources`, so R10 holds for a deploy as well as a build without a new manifest field.

  `deploy run` compares with it (`check_build_version()`), keeping today's scope: an error,
  `build_version_mismatch`, only when the build holds a Helm module, and only for the pins such a build
  applies - `charts` and `artifacts` (its `${artifact:}` values). Those are compared by value, so an
  edit to a listed document between build and deploy is caught even when the list is the same. Other
  builds are not judged, as today. A build without `version.yaml` falls back to today's name
  comparison.
- **V9 - A deployment's ring.** The ring of a deployment is the `spec.promotion` of its listed
  documents. Untagged documents (a shared `prd-core`) do not take part. Two tagged documents in one
  list that disagree on `(ring, order, wave)` are an error, `version_ring_conflict`. The deployment's
  wave is its tagged documents' wave. No tagged document: the deployment is not in a rollout (worked
  example A in promotion.md). When other deployments of the same workspace are in a rollout, that is
  information, `version_ring_none`, so "why is it not in `promote status`" has an answer.
- **V10 - Promotion is built from the matrix, not from version documents.** Today `build_promotion_view()`
  groups version documents by their declared `spec.workspace`. With that field removed (V4) and
  documents shared, the only workspace promotion uses is the **deployment's**. For a workspace:
  - the rings are those of its deployments (V9);
  - a ring's rows are the matrix rows of that workspace's deployments in that ring, each with its
    value and owning document; deployments that agree collapse to one row, deployments that differ
    show one row each;
  - the ring/order bijection and `(ring, order, wave)` checks run over those deployments' rings;
  - the orphan warning (`promotion_orphan_ring`) stays per tagged document: tagged, and named by no
    deployment.
- **V11 - `promote apply WORKSPACE RING` works out what needs a pin and writes to the owner.** For the
  workspace's deployments in `RING`:
  1. list the targets they need (V5);
  2. take each target's value from the preceding ring's matrix for the same workspace;
  3. write it into the document that owns that key in `RING`'s matrix, resetting `status` and
     `reviewed` as today.

  A key with no owner in `RING` is reported and not created (that is `version update`'s job, as now).
  A key owned by a document that deployments of **other** workspaces also name is a shared key:
  `apply` skips it and lists it as "shared, not promoted", unless `--shared` is given. Promoting one
  product never silently moves another (R4). `--pin` selection and `--dry-run` keep working on the
  resulting list.

  Several deployments of one workspace in a ring (per environment, per tenant, per customer):
  - **Source.** The preceding ring's deployments must agree on each key's value. When they do not,
    `apply` stops and names them; `--wave` picks the source wave as today (a single-wave ring is used
    whatever the target's wave, as `_select_source_wave()` does now).
  - **Target.** `apply` writes only to the documents owned by the **chosen wave's** deployments
    (`--wave`, required when the ring has more than one, as today). A document no tagged document in
    that wave shares with (an untagged customer document such as `prd-datahub-fabrikam`) is never
    written: customers move on their own cadence (R5) and are listed as "not promoted, own document".
- **V12 - Drift of a shared key is visible.** Across all deployments in one ring, the same
  `category/target` owned by **untagged** documents with different values is a warning,
  `version_ring_drift`, naming the deployments and owners: `prd-core` against `prd-core-old`. Keys owned
  by tagged product documents or by customer documents are not judged: a customer document differing
  from its product document is the point of R6, and a warning on every customer exception would make
  `--strict` unusable. Within a deployment two owners are already an error (V3).
- **V13 - Tooling writes to the owner.** `version set NAME CATEGORY TARGET` names the document, so it is
  unchanged. `version update NAME` judges *missing* keys against the matrices of the deployments that
  name `NAME`: a key another listed document already pins is not missing from `NAME`
  (`version_scope()`; a document no deployment lists is judged against the whole solution, as before).
  *Stale* keys stay what they were, pins whose target no longer exists: a pin no listing deployment
  uses is `version_pin_unused` in `validate` (V5), and may be staged on purpose, so `update` does not
  propose removing it. `version new NAME [--from VERSION]` drops
  `--workspace`: today it is required but only checked and written into the file, it never chose a
  pin. It is removed in 2.7 (Phase 8, ADR-0065). `--from` is unchanged: it copies another document's pin keys, each seeded with its target's
  current value. `version show NAME` (new) lists the deployments that list `NAME`. A deployment's view comes from
  `deploy images DEPLOYMENT` and from the build's `version.yaml` (V8).
- **V14 - Logging.** `log_pin_applied` reports the owning document from the resolver's map
  (`version=prd-datahub`), so a list never hides where a pin came from (R7).
- **V15 - No lock file.** Considered: a generated, committed effective file per deployment, with
  `validate` failing when stale. Rejected: V3 prevents a mismatch inside a deployment, what ran is in
  the build output (V8), and what will run is computed on demand (`deploy images`, `promote status`).
  A generated file adds merge conflicts and a regeneration step for a review aid.
- **V16 - Listing deployments.** `deploy list` (`list_deployments()`, the VS Code views through
  `DeploymentRow`) reads `spec.version` as one string and takes the ring from that document. The row
  has `versions: list[str]` instead of the single `version` (removed in 2.7 with the field, Phase 8).
  `ring`/`order`/`wave` come from V9. `--version X` matches a deployment whose list
  contains `X`; `--ring` matches V9's ring.

### What R1-R10 look like

**config-repo, one shared document and one per product (R1, R2, R3).**

```yaml
# versions/prd-core.yaml - untagged, listed by every product's prd deployments
spec:
  pins:
    remotes: { iac: v2.1.0 }
---
# versions/prd-staging.yaml
spec:
  promotion: { ring: prd, order: 3 }
  pins:
    artifacts: { staging-api: 1.2.0 }
---
# versions/prd-datahub.yaml
spec:
  promotion: { ring: prd, order: 3 }
  pins:
    artifacts: { datahub-api: 1.4.0 }
---
# deployments/base-prd.yaml - every product's prd deployments extend this
spec:
  partial: true
  version: [prd-core]
  environments: [prd]
---
# deployments/datahub-prd.yaml
spec:
  extends: base-prd
  workspace: datahub
  version: [prd-datahub]        # appended: [prd-core, prd-datahub]
```

`iac` is pinned once. If `prd-datahub` also pinned `remotes.iac`, `datahub-prd` fails validation:
`version_pin_conflict: remotes/iac is pinned by prd-core and prd-datahub`. If `datahub-prd` listed
`prd-staging` instead of `prd-datahub` by mistake, `staging-api` is nothing datahub uses:
`version_document_unused: datahub-prd lists prd-staging, which pins nothing it uses`.

**The matrix `strata validate` builds.**

| deployment  | target                | value  | owner       |
| ----------- | --------------------- | ------ | ----------- |
| datahub-prd | remotes/iac           | v2.1.0 | prd-core    |
| datahub-prd | artifacts/datahub-api | 1.4.0  | prd-datahub |
| staging-prd | remotes/iac           | v2.1.0 | prd-core    |
| staging-prd | artifacts/staging-api | 1.2.0  | prd-staging |

**Product promotion (R4).** `promote apply datahub prd` reads datahub's `qas` matrix and writes
`datahub-api` into `prd-datahub`. `iac` is owned by `prd-core`, which `staging-prd` also names, so it is
listed as "shared, not promoted (use --shared)".

**Customer cadence (R5).** Unchanged: per-customer `assets` pins live in a ring document and
`promote apply --pin` still copies them.

**Customer differs from its ring (R6).** The customer's deployment names a different document for the
part that differs, instead of a copy of the whole ring:

```yaml
# deployments/fabrikam-prd.yaml
spec:
  extends: base-prd                         # [prd-core]
  workspace: datahub
  version: [prd-datahub-fabrikam]           # instead of prd-datahub
```

The granularity is the document: a customer that differs on one pin of a large product document
either splits that document or repeats it. Finer overrides are open question 3.

**What ran (R10).** `build run` for `datahub-prd` writes `version.yaml` (V8), and `deploy run` compares
the values it resolves with that file (Phase 5).

### Lifecycle

Two products, Staging and DataHub, each in its own workspace, both fetching infrastructure code from
the `iac` remote, rolled out through `qas` and `prd`. Output is a sketch of the design. **(new)** marks
behaviour that does not exist yet.

**0. Deployments list their documents.**

```yaml
# deployments/base-qas.yaml - every product's qas deployment extends this
spec:
  partial: true
  version: [qas-core]                 # (new) a list, appended by extends
  environments: [qas]
---
# deployments/datahub-qas.yaml
spec:
  extends: base-qas
  workspace: datahub
  version: [qas-datahub]              # effective: [qas-core, qas-datahub]
---
# deployments/staging-qas.yaml
spec:
  extends: base-qas
  workspace: staging
  version: [qas-staging]              # effective: [qas-core, qas-staging]
```

`base-prd`, `datahub-prd` and `staging-prd` are the same with `prd-*`.

**1. Create the documents.**

```
$ strata version new qas-core              # (new) no --workspace
  ✅  Created versions/qas-core.yaml.
$ strata version new qas-datahub
  ✅  Created versions/qas-datahub.yaml.
```

```yaml
# versions/qas-core.yaml - no workspace field (new)
apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: qas-core
spec:
  pins: {}
```

The product documents get `promotion: { ring: qas, order: 2 }` by hand. `qas-core` stays untagged: it
is shared and does not decide the ring (V9).

**2. Fill the pins.** `version update` proposes what the deployments listing the document need
**(new, V13)**:

```
$ strata version update qas-datahub
  add  artifacts/datahub-api   1.3.0      (current image_tag)
  add  remotes/iac             v2.0.0     (current reference)
```

`iac` is shared, so it goes in the shared document:

```
$ strata version set qas-core remotes iac v2.0.0
$ strata version set qas-datahub artifacts datahub-api 1.3.0
$ strata version update qas-datahub
  (nothing to add: remotes/iac is pinned by qas-core)        # (new) the matrix counts every listed document
```

**3. Validate.** `strata validate` builds the matrix per deployment **(new, V3)**:

```
datahub-qas   remotes/iac             v2.0.0   qas-core
datahub-qas   artifacts/datahub-api   1.3.0    qas-datahub
staging-qas   remotes/iac             v2.0.0   qas-core
staging-qas   artifacts/staging-api   2.1.0    qas-staging
```

| Mistake                                                     | Finding                                                                                      |
| ----------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `qas-datahub` also pins `remotes/iac`                       | error `version_pin_conflict`: remotes/iac is pinned by qas-core and qas-datahub              |
| `datahub-qas` lists `qas-staging` instead of `qas-datahub`  | warning `version_document_unused`: datahub-qas lists qas-staging, which pins nothing it uses |
| `qas-core` pins `charts/redis`, which nothing uses          | info `version_pin_unused`                                                                    |
| `qas-datahub` tagged `qas/2`, another listed one `prd/3`    | error `version_ring_conflict`                                                                |
| `staging-qas` lists `qas-core-old` with `iac: v1.9.0`       | warning `version_ring_drift`: shared remotes/iac in ring qas is v2.0.0 and v1.9.0            |
| a document still has `spec.workspace`                       | it fails to load: delete the `workspace:` line (ADR-0065) |

**4. Build.**

```
$ strata build run datahub-qas
  pin remotes/iac → v2.0.0 (version=qas-core)                 # (new) the owning document (V14)
  pin artifacts/datahub-api → 1.3.0 (version=qas-datahub)
  wrote builds/datahub-qas/resolved.yaml
  wrote builds/datahub-qas/version.yaml                        # (new, V8)
```

```yaml
# builds/datahub-qas/version.yaml - just the pins
apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: datahub-qas
spec:
  pins:
    remotes:   { iac: v2.0.0 }
    artifacts: { datahub-api: 1.3.0 }
---
# builds/datahub-qas/resolved.yaml - the build's record (version section)
version: [qas-core, qas-datahub]
pinned_by: spec.version
pin_sources:
  remotes/iac: qas-core
  artifacts/datahub-api: qas-datahub
promotion: { workspace: datahub, ring: qas, order: 2 }
```

To try other documents for one run, repeat `--pin` **(new, V7)**; the list replaces the deployment's:

```
$ strata build run datahub-qas --pin qas-core --pin qas-datahub-rc
```

`resolved.yaml` then records `version: [qas-core, qas-datahub-rc]` and `pinned_by: --pin`. Leaving
`qas-core` out would drop `iac` back to the remote's own reference:

```
$ strata build run datahub-qas --pin qas-datahub-rc
  warning version_pin_dropped: remotes/iac is v2.0.0 under the deployment's versions (qas-core) but not
  pinned by --pin; the remote's own reference applies
```

**5. Deploy.**

```
$ strata deploy run datahub-qas
  pins match the build (builds/datahub-qas/version.yaml)      # (new) compares values, not the name
```

After `version set qas-datahub artifacts datahub-api 1.3.1` without a rebuild:

```
  error build_version_mismatch: artifacts/datahub-api is 1.3.1 now (qas-datahub) but 1.3.0 in the build.
  Rebuild, or deploy what the build holds.
```

Today that edit goes unnoticed: only the document name is compared. As today, only pins the build
applied (charts, `${artifact:}` values in Helm, artifact tags in `strata.variables`) are judged (V8).

**6. Update a version.** A new DataHub release touches only DataHub's deployments, because only they
list `qas-datahub`:

```
$ strata version set qas-datahub artifacts datahub-api 1.4.0
$ strata validate
$ strata build run datahub-qas && strata deploy run datahub-qas
```

A bump of the shared part is one edit every product in the ring picks up on its next build:

```
$ strata version set qas-core remotes iac v2.1.0
```

Holding a pin back is a hand edit, as today; `version set` refuses to move a held pin without `--force`:

```yaml
artifacts:
  datahub-api: { version: 1.4.0, status: held, reason: "1.5 needs the schema migration first" }
```

**7. Promote to `prd`.** The first time, start the `prd` documents from the `qas` keys:

```
$ strata version new prd-core --from qas-core
$ strata version new prd-datahub --from qas-datahub      # then add promotion: { ring: prd, order: 3 }
```

```
$ strata promote status datahub                          # (new) from the deployments, owner column (V10)
  ring  order  target                 version   owner
  qas   2      remotes/iac            v2.1.0    qas-core      (shared)
  qas   2      artifacts/datahub-api  1.4.0     qas-datahub
  prd   3      remotes/iac            v2.0.0    prd-core      (shared)
  prd   3      artifacts/datahub-api  1.3.0     prd-datahub   ← behind

$ strata promote apply datahub prd                       # (new) needed targets, written to the owner (V11)
  copied  artifacts/datahub-api  1.3.0 → 1.4.0   into prd-datahub
  skipped remotes/iac            v2.0.0 → v2.1.0  prd-core is shared (staging-prd); use --shared

$ strata promote apply datahub prd --shared
  copied  remotes/iac  v2.0.0 → v2.1.0  into prd-core   (also used by: staging-prd)
```

Then validate, build and deploy `datahub-prd` as in steps 3-5.

**8. A customer that differs.**

```yaml
# deployments/fabrikam-prd.yaml
spec:
  extends: base-prd                       # [prd-core]
  workspace: datahub
  version: [prd-datahub-fabrikam]         # instead of prd-datahub
```

`version new prd-datahub-fabrikam --from prd-datahub` starts that file; the matrix rules apply to it
like any other deployment.

| Exists today                                                 | New in this design                                                                                    |
| ------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| `version new --workspace`, `--from`                          | `version new` without `--workspace`                                                                   |
| `version update`, judged against the whole solution          | judged against the deployments that list the document                                                |
| `version set`, held pins need `--force`                      | -                                                                                                     |
| `validate` checks pin targets exist                          | the matrix: `version_pin_conflict`, `version_document_unused`, `version_pin_unused`, `version_ring_conflict`, `version_ring_drift` |
| `build run --pin NAME`, `resolved.yaml` names one document   | `--pin` repeatable; `version.yaml` in the build; `resolved.yaml` with the list, `pinned_by`, `pin_sources` |
| `deploy run` compares the document name                      | compares pin values with the build's `version.yaml`                                                   |
| `promote status/apply` over the documents' `spec.workspace`  | built from the deployments' matrices, owner column, shared keys skipped unless `--shared`             |

### Alternatives considered

- **`spec.workspaces: [a, b]` on the version document.** One file per ring, a shared pin written once.
  Rejected: an edit to one product's pin is a change to a file every listed workspace runs, a product
  cannot be promoted on its own (`promote apply` copies the whole document), and it gives a second way
  to cover several workspaces next to composition.
- **`spec.workspace: "*"`.** The list without the limit: a new workspace picks the document up
  silently, and promotion has no workspace to group it under. Rejected.
- **Keep `spec.workspace` as an optional limit** (this redesign's first version, earlier on 2026-11-14):
  set, only that workspace may list the document; unset, any may. Rejected for removal (V4): every
  other job of the field moved to the deployment side, the limit is mostly covered by
  `version_document_unused`, and an optional field that only restricts invites a second, stale
  statement of what the deployments already say.
- **`spec.include` and `spec.overrides` on the version document** (this doc's first draft). A ring
  document includes per-product release documents, which include a core; includes are a disjoint union,
  an `overrides` section with a required `reason` is the only way to change an included value, and a
  document's workspaces are the union of its includes'. It gives one file that says what a ring runs.
  Replaced because:
  - the deployment is where pins take effect, so checking the union there catches the same
    conflicts with one level of composition: no nesting, no cycles, no "effective workspaces of an
    include", no override stacking;
  - promotion keeps writing into one owning document per key, as today, instead of copying include
    entries forward;
  - a customer that differs names a different document (R6) instead of an override layer.
  What it gave up, "one file says what a ring runs", is `promote status` (V10) and the build's
  `version.yaml` (V8) instead.

### What composition does not solve

- **One remote carrying several products.** If every product root is fetched from `iac`, a shared
  document owns it and the products' infrastructure code moves together in a ring, while images and
  artifacts move per product. Independent infrastructure versions per product need a pinnable target
  per product (open question 1).
- **Run order between products** (Staging before its consumers). Pins express versions, not order.

### Implementation Plan

Eight phases in three releases. Each phase ends green on the full gate: `ruff`, `mypy src`,
`lint-imports`, the strict Sphinx build and the full test suite. Each phase is one PR on its own branch
off `main`; the user reviews and merges.

| Release | Phases | Ships                                                                                    |
| ------- | ------ | ---------------------------------------------------------------------------------------- |
| 2.7.0   | 1-8    | All of it, on one branch: shared documents, version lists, the matrix, `--pin` lists,    |
|         |        | the build's `version.yaml`, promotion, tooling, and the field's removal (ADR-0065)       |

Phase 1 touches no consumer and can ship while Phases 2-7 are built. Phases 2-7 change behaviour only
for a deployment that lists more than one document, or for `--pin` given twice, so they can land one by
one on `main` without a release in between.

#### Phase 1 - ADR and the workspace deprecation (2.7.0)

- [x] ADR-0063 "A deployment lists several version documents" and ADR-0064 "`Version.spec.workspace` is
  deprecated" (one decision per ADR). Each names this doc, without a link, as `version-composition.md` (work doc).
- [x] `semantic_checks._check_version_workspace()`: drop the first-referrer rule (an unset workspace
  binds nothing); keep `version_workspace_mismatch` for a declared value; add a warning,
  `version_workspace_deprecated`, on every document that declares it, located at `spec.workspace`.
  Partial bases are no longer counted as referrers (today they duplicate the finding).
- [x] `promotion_controller`: no change in this phase. Grouping still reads the declared field, so a
  solution keeps its `promote status` until Phase 5 replaces the grouping.
- [x] `version new`: `--workspace` optional; when given, ignored with a deprecation note on stderr and in
  the JSON data (`deprecated: ["--workspace"]`); `scaffold_version()` writes no `spec.workspace`.
- [x] `version_model.py`: `spec.workspace` description says deprecated, with the 3.0 removal.
- [x] Tests: `test_semantic_checks.py` (two workspaces sharing an unset document validate; a declared
  mismatch still errors; the deprecation warning; no duplicate finding through a partial base),
  `test_commands_version.py` and `test_version_controller.py` (`version new` without `--workspace`,
  with it ignored).
- [x] Docs: `docs/config/version.md` (`spec.workspace` deprecated, what replaces it),
  `docs/how-to/` page on promotion if it shows `--workspace`, CHANGELOG `### Deprecated` and
  `### Fixed` (the reported error).
- **Done when:** the reporter's solution (one document, deployments of two workspaces) validates with
  only the deprecation warning, and every existing test passes.

#### Phase 2 - Model, `extends`, the targets function and the resolver (2.8.0)

- [x] `deployment_model.py`: `version` becomes `VersionNames = Annotated[PlatformName,
  References(VERSION)] | list[Annotated[PlatformName, References(VERSION)]]` (the `ParamSetNames`
  pattern), with a `versions` property returning the normalised, de-duplicated list. The reference walk
  and schema export follow the existing pattern; check `References` on a list in
  `reference_fields.py`.
- [x] `deployment_service.merge_deployment_specs()`: `version` appends base-first and de-duplicates
  (V2); a scalar on either side counts as a one-element list. Document it next to `environments`.
- [x] New `controllers/pinnable_targets.py`: `pinnable_targets(index, deployment) -> set[tuple[str,
  str]]` (V5). Images and charts from the workspace's modules, artifacts from `artifact_usage`'s
  per-deployment walk, remotes from provisioner sources and module/asset locations with `fetch:
  strata`, assets with their own location. One test per category, plus a deployment using none.
- [x] New `controllers/version_composition.py`:
  - `EffectiveVersion` (dataclass): `model: VersionModel | None` (merged pins, `meta.name` = the
    deployment, `promotion` from V9), `names: list[str]`, `owners: dict[str, str]`
    (`"category/target" -> document`), `pinned_by: Literal["spec.version", "--pin"]`.
  - `effective_version(index, deployment, pins: Sequence[str] | None = None) -> tuple[EffectiveVersion,
    Diagnostics]`: union, `version_pin_conflict` (V3), `version_listed_twice` (V1), `version_ring_conflict`
    (V9), and `version_pin_dropped` (V7) when `pins` is given.
  - A deployment with one document returns that document's pins unchanged (R9); no document returns
    `model=None`, as today.
- [x] `import-linter` contract: `version_composition` and `pinnable_targets` are controllers and import
  no command module.
- [x] Tests: `test_version_composition.py` (union, conflict with equal and with different values, one
  document, none, listed twice, ring conflict, wave taken from the tagged document, pin dropped),
  `test_pinnable_targets.py`, `test_services_deployment.py` (append, de-duplicate, scalar plus list),
  model tests for the name-or-list shape.
- **Done when:** the resolver returns the matrix of the Lifecycle example and nothing calls it yet.

#### Phase 3 - Consumers, `--pin` lists and listing (2.8.0)

- [x] `value_controller.resolve_version()` returns the resolver's merged model; a new
  `resolve_effective_version()` returns the whole `EffectiveVersion` for callers that need the owners.
  `resolve_deployment(..., version_pin)` takes `version_pins: Sequence[str] | None` and stores the list
  on the resolved model.
- [x] Direct lookups switched (V6): `artifact_tokens.py`, `artifact_usage.py`,
  `deployment_resolution.list_deployments()` (V16, `versions` on `DeploymentSummary`, `version` kept for
  one document), `semantic_checks` (Phase 4 adds the matrix checks).
- [x] Pin application logs the owner (V14): `apply_version_pins()` and `apply_remote_version_pins()`
  take an optional `owners` map; `log_pin_applied()` gets the owning document instead of the
  deployment's single name.
- [x] `--pin` repeatable (`multiple=True`) on `build run`, `deploy run`/`plan`/`setup`/`check`/`apply`/
  `destroy`, `deploy images`, `policy` and `integration` (V7). Help text: "Repeatable; replaces the
  deployment's version list for this run."
- [x] `deploy images` JSON and console: the owner per image (`data.images[].version`), `data.versions`
  as the list.
- [x] VS Code: `DeploymentRow.versions: string[]` in `strataClient.ts`; the views show the list where they
  show `version` today.
- [x] Tests: `test_value_controller.py`, `test_effective_images.py`, `test_commands_deploy_images.py`,
  `test_commands_deploy_list.py` (filter by a name in the list, ring from V9),
  `test_commands_build.py`/`test_commands_deploy.py` (`--pin` twice; `version_pin_dropped`),
  `test_version_pins.py` (owner in the log line), `test_artifact_usage.py`.
- **Done when:** the Lifecycle's `datahub-qas` builds and deploys with `[qas-core, qas-datahub]`, and the
  log names `qas-core` for `iac`.

#### Phase 4 - The matrix checks in `validate` (2.8.0)

- [x] `semantic_checks`: per resolved deployment (partial bases skipped), run `effective_version()` and
  report its findings, then coverage against `pinnable_targets()` (V5): `version_document_unused` for a
  document the deployment lists itself, `version_pin_unused` per document across the deployments that
  list it, `version_ring_none` (V9), `version_ring_drift` over untagged owners (V12).
- [x] `_check_version_workspace()` judges a declared workspace per listed document.
- [x] Every new code says what to do: a `hint` on the finding (the new `Diagnostic.hint`,
  docs/work/diagnostic-guidance.md "Decided"), and `docs/config/version.md` "Findings" lists each code
  with its severity, when it fires and what to do.
- [x] Tests: `test_semantic_checks.py`, one per finding, plus the Lifecycle table's six mistakes as one
  scenario test; `test_commands_validate.py` for `--strict` failing on the warnings only.
- **Done when:** each row of the Lifecycle's "Validate" table produces exactly its finding.

#### Phase 5 - Build output and the deploy check (2.8.0)

- [x] `build_controller.write_resolved_manifest()`: `version` as the list, `pinned_by`, `pin_sources`
  (only pins in `version.yaml`), `promotion` from V9 with the deployment's workspace (V8).
- [x] New `write_effective_version(build_path, effective, targets)`: `version.yaml` with the
  needed-and-pinned rows, no `promotion`, loaded back through `VersionModel` before returning.
- [x] `_manifest.json` and the audit run record (`audit_run.py`): built differently. The record has no
  version field of its own; it hashes `resolved.yaml`, which now holds the list, `pinned_by` and
  `pin_sources`, so no manifest field was added (V8).
- [x] `deploy_controller.check_build_version()`: compare the values of the pins the build applied
  (charts and Helm `${artifact:}` values, artifact tags in `strata.variables`) with `version.yaml`;
  name the target, both values and the owner; fall back to the name comparison when the build has no
  `version.yaml`.
- [x] Tests: `test_build_controller.py` (both files, `pin_sources`, no promotion in `version.yaml`, the
  file loads), `test_deploy_controller.py` (edit between build and deploy caught for a chart and for an
  artifact tag, not for a Terraform-only remote; old build falls back), `test_audit_run.py`.
- **Done when:** the Lifecycle's step 5 behaves as written.

#### Phase 6 - Promotion from the matrix (2.8.0)

- [x] `promotion_controller.build_promotion_view(workspace, ...)`: rings and rows from that workspace's
  resolved deployments (V10), owner per row, rows collapsed when deployments agree; bijection and slot
  checks over the deployments' rings and waves; orphan check per tagged document named by no
  deployment. No read of `Version.spec.workspace`.
- [x] `apply_promotion()`: needed targets (V5), source values from the preceding ring with the
  agreement rule, write to owners in the chosen wave, skip shared keys unless `shared=True`, skip
  untagged customer documents, report every skip (V11). Keeps `--wave`, `--pin` selection and
  `--dry-run`.
- [x] `promote` command: `--shared` on `apply`; the owner column and `(shared)` marker in `status`;
  JSON rows gain `owner` and `shared`.
- [x] `validate_promotions()` iterates the workspaces of the resolved deployments.
- [x] Tests: `test_promotion_controller.py` (rewritten fixtures with a shared core: status rows,
  `behind`, shared skip, `--shared`, customer document skipped, disagreeing source stops, waves),
  `test_commands_promote.py`.
- **Done when:** the Lifecycle's step 7 behaves as written, and existing single-document promotion
  tests still pass with the fixtures' `spec.workspace` removed.

#### Phase 7 - Tooling, docs and example (2.8.0)

- [x] `version update NAME`: missing keys judged against `pinnable_targets()` of the deployments that
  list `NAME`, minus keys another of their documents pins; stale keys are `NAME`'s own pins none of them
  needs (V13). A document no deployment lists falls back to today's solution-wide inventory.
- [x] New `version show NAME`: the document's pins and the deployments that list it (own or inherited),
  console and JSON.
- [x] Docs: `docs/config/deployment.md` (`version` as a list, `extends` appending),
  `docs/config/version.md` (composition, every finding), `docs/how-to/` page "Several workspaces in one
  ring" from the Lifecycle, `docs/design/` updates where `spec.version` is described as one name,
  agent skill table (`templates/agent/strata/SKILL.md`).
- [x] `config/` example: a shared core document and two products listing it.
- [x] CHANGELOG under `[Unreleased]` (the release number is the release's to choose: the plan split this
  into 2.7.0 and 2.8.0, but all of it is on one branch).
- [ ] This work doc moves to `docs/design/version-composition.md` with a `## History` section once the
  open questions are settled.
- **Done when:** the strict Sphinx build passes and the example validates with `--strict`.

#### Phase 8 - Remove the field (2.7.0, brought forward from 3.0 by ADR-0065)

- [x] `VersionSpecModel.workspace` removed; a document that still has it fails to load with a message
  naming the removal and the fix (`reject_removed_workspace()`).
- [x] `_check_version_workspace()`, `version_workspace_mismatch` and `version_workspace_deprecated`
  removed; `version new --workspace` removed.
- [x] `DeploymentSummary.version` (V16) removed in favour of `versions` (VS Code `DeploymentRow` too).
- [x] Promotion reads only deployable deployments: no fallback for documents that declare a workspace.
  `path list version --workspace W` lists what W's deployments list.
- [x] CHANGELOG `### Removed`, marked breaking; ADR-0065, ADR-0064 superseded.

## Related Decisions

- [ADR-0019](../decisions/0019-version-pinning.md) - `kind: version`, pins with `reason`, logged application.
- [ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md) - a version reference is an identity, not a path.
- [promotion.md](promotion.md) - ring/order/wave tags and `promote status`/`apply`, which V9-V11 rebuild on the matrix.

## Remaining Work / Open Questions

1. **A pinnable target per product root.** For products whose infrastructure code must move on its
   own: a remote per product root (same URL declared several times, against configure-once), or a
   pin keyed by remote and path with product-scoped tags (`datahub/v1.4`). Needed only if config-repo
   wants that independence; ask them.
2. ~~**`promote status` per ring across workspaces.**~~ Built: `WORKSPACE` is optional on `promote
   status`, `--ring` is repeatable, and without a workspace the table has a workspace column
   (`rollout_workspaces()`).
3. **Overrides.** Whether a deployment needs to differ on one pin of a listed document without a copy
   of it (R6 at finer grain). Deliberately not built: wait for a real case.
4. ~~**Equal values from two owners (V3).**~~ Decided as an error and built. Revisit only if migrating
   existing per-product documents with copied pins proves painful.
5. **Unused documents.** For version documents: built as information, `version_document_unlisted`
   (untagged, listed by no deployment; a tagged one is `promotion_orphan_ring`), information because
   CI may use it only through `--pin`. The generic `document_unreferenced` for the other kinds is built
   too, tracked in [unreferenced-documents.md](unreferenced-documents.md).
6. ~~**Phase 8 (3.0).**~~ Brought forward to 2.7 (ADR-0065): two users, who drop the line in the same
   upgrade.

## Changelog

- 2026-11-01: First draft, from the config-repo request for one release document per ring across
  workspaces and the shared-core problem it exposes: `spec.include` and `spec.overrides` on the version
  document, C1-C15.
- 2026-11-02: Parked until after 2.0.0-RC3.
- 2026-11-14: Redesigned after the 2.6.3 report of `version_workspace_mismatch` on a document shared by
  two workspaces. Composition moves to the deployment: `spec.version` is a list, the per-deployment pin
  matrix rejects two owners of one key (V3), `Version.spec.workspace` becomes an optional limit without
  the first-referrer rule (V4), `extends` appends the list, `--pin` repeats, the build writes the
  effective `version.yaml`, and promotion is computed from the deployments' matrices with shared keys
  skipped unless `--shared`. The include/overrides design moved to "Alternatives considered".
- 2026-11-14: `Version.spec.workspace` removed instead of kept as an optional limit (V4): deprecated in
  2.7 (still enforced when declared, `version_workspace_deprecated`), gone in 3.0 (Phase 8). The limit
  is replaced by `version_document_unused` (V5); promotion uses only the deployments' workspaces (V10);
  `version new --workspace` is dropped (ignored with a deprecation note in 2.7, removed in 3.0) and
  `version show` is added (V13).
- 2026-11-14: V8: the build's `version.yaml` holds only the pins; where they came from (`version` list,
  `pinned_by`, `pin_sources`) moves to `resolved.yaml` instead of `meta.annotations`, which are
  free-form, unchecked, and would go stale in a copied file. Added the "Lifecycle" walkthrough: create,
  fill, validate, build, deploy, update, promote, customer.
- 2026-11-14: Review fixes: `pinnable_targets()` as one function every "what does it need" uses (V5);
  `version_document_unused` judges only a deployment's own list (V5); `version_pin_dropped` under
  `--pin` (V7); `version.yaml` without `promotion`, and `build_version_mismatch` keeps its scope of
  build-applied pins, now by value (V8); wave from the tagged documents and `version_ring_none` (V9);
  several deployments per workspace and ring in `promote apply` (V11); ring drift only for untagged
  owners (V12); V6's consumer list corrected (most go through `resolve_version()`); `deploy list` and
  the VS Code row (V16); the run record's `version` as a list. Implementation plan detailed: Phase 1
  alone as 2.7.0, Phases 2-7 as 2.8.0, the removal as 3.0.0.
- 2026-11-14: Phases 1-7 built on one branch. Settled in the building: `version_document_unused` only
  for a document whose pins other deployments use (a document no deployment uses is `stale_pin` or
  `version_pin_unused`, not this mistake); the audit record needs no new field (it hashes
  `resolved.yaml`); `build_version_mismatch` judges `charts` and `artifacts` of Helm builds only, as
  before; `version update` keeps "stale" as "target gone"; until 3.0 a tagged document that declares
  `spec.workspace` and that no deployment lists still shows in that workspace's `promote status`.
  ADR-0063 and ADR-0064, `docs/config/version.md`, the how-to "Share version pins between products in
  one ring", and the `config/` example (a shared `platform` document) added.
- 2026-11-14: Phase 8 brought forward into 2.7 (ADR-0065, ADR-0064 superseded): `Version.spec.workspace`,
  its checks, `version new --workspace` and `deploy list`'s single `version` are removed. A document that still
  declares the field fails to load with the fix in the message. Promotion reads only deployable
  deployments; `path list version --workspace` follows the deployments.
