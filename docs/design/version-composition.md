# Version Composition — Design

- Status: current
- Last updated: 2026-11-14

## Overview

A deployment's `spec.version` names one [`version`](../config/version.md) document or a list of them, and their pins
apply together. Several products in one ring (one workspace each) share what they have in common in one document
and keep their own pins in their own document: a shared `prd-core` pins the `iac` remote every product's
infrastructure code comes from, `prd-datahub` pins DataHub's images. Every pin has exactly one owning document per
deployment, so a shared pin is written once and a deployment can never run two values of it.

A version document names no workspace: the deployments that list it decide where it applies. Build and deploy read
the same composed pins through one resolver, the build records what it used and where each pin came from, and
promotion is read from the deployments' pins.

The user guide is [`version`](../config/version.md), "Several documents per deployment", with every finding under
"Findings"; the walk-through is the how-to
[Share version pins between products in one ring](../how-to/share-version-pins-across-workspaces.md).

## Current Design

The labels V1–V16 are the decision numbers code comments cite.

### The deployment lists documents (V1, V2)

```yaml
# deployments/base-prd.yaml - every product's prd deployment extends this
spec:
  partial: true
  version: [prd-core]
  environments: [prd]
---
# deployments/datahub-prd.yaml - effective: [prd-core, prd-datahub]
spec:
  extends: base-prd
  workspace: datahub
  version: [prd-datahub]       # one name works too
```

- **V1.** `spec.version` is a name or a list (`VersionNames`, the `ParamSetNames` pattern); a single name is a
  one-element list (`version_name_list()`). Order carries no meaning: nothing is resolved by order. A name twice
  in one written list is a warning, `version_listed_twice`.
- **V2.** Through `extends` the lists append, base first, each document once (`merge_deployment_specs()`), like
  `environments`. A deployment repeating a document its base lists is not a mistake. It cannot remove one its base
  lists; it extends another base.

### The pin matrix (V3, V5)

- **V3.** A deployment's effective pins are the union of its listed documents' pins: one row per
  `category/target`, with its value and owning document. The same `category/target` in two listed documents is an
  **error**, `version_pin_conflict`, even with equal values: one owner per pin means `version set`,
  `version update` and `promote apply` never choose between two documents, and bumping one copy cannot split a
  deployment.
- **V5.** What a deployment uses comes from one function, `pinnable_targets()`: its enabled module services'
  images, chart modules, every remote a reached document names, every artifact a service, `${artifact:}` token or
  `store: artifact` variable names, and every asset its execution steps select (with that asset's remote).
  Deliberately a little wide: a target counted but unused only hides a `version_pin_unused`, while a target missed
  would make a correct document look wrong. Against the matrix:
  - used and pinned: fine; used and not pinned: the target's own declared value, as before;
  - a document the deployment lists **itself** whose pins it uses none of, while other deployments do: a warning,
    `version_document_unused` (another product's document listed by mistake). Inherited documents and documents
    no deployment uses are not judged here;
  - a pin no deployment listing the document uses: information, `version_pin_unused` (it may be staged);
  - an untagged document no deployment lists: information, `version_document_unlisted` (CI may use it through
    `--pin` only).

### No workspace on a version document (V4)

`Version.spec.workspace` is removed. It is not part of the model; a document that still declares it fails to load
with a message naming the removal and the fix (`VersionSpecModel.reject_removed_workspace()`). Its jobs moved to the
deployments:

| What `spec.workspace` did                                       | Now                                                                 |
| --------------------------------------------------------------- | ------------------------------------------------------------------- |
| Only that workspace's deployments may list the document         | `version_document_unused` (V5) and `version_pin_conflict` (V3)      |
| Unset: the first deployment fixed it for every other one        | nothing: any deployment may list any document                       |
| Grouped documents for `promote status` / `apply`                | the deployments' workspaces (V10)                                   |
| Required by `version new --workspace`                           | nothing: the flag is gone (it never chose a pin)                    |

What is lost: a document pinning only targets both products use is not caught when listed by the wrong product.
Naming (`prd-datahub`) and `strata version show` carry what the field said.

### One resolver (V6)

- `compose_versions(index, names, label=...)` builds the union and its findings. One document comes back
  unchanged, so a one-document deployment logs and reports exactly as before. Several give a
  `ComposedVersionModel` named after the deployment, whose `spec.pins` is the union and which remembers each pin's
  owner (`pin_owner()`, `pin_owners()`) and the documents it was built from (`version_names()`). Both live in
  `models/version_model.py`, so the SBOM collectors (integrations layer) can name an owner too.
- `deployment_version(index, deployment)` is the same for a resolved deployment, findings dropped;
  `value_controller.resolve_version()` wraps it, and every consumer (build, deploy, effective images, image tag
  policy, audit, artifact tokens, the SBOM) goes through one of the two.
- **V14.** Pin logging, `deploy images` and the SBOM name the owning document, never the deployment's name.

### `--pin` (V7)

`--pin` is repeatable on `build run`, `deploy run`/`plan`/`setup`/`check`/`apply`/`destroy`, `deploy images`,
`policy` and `integration`, and what it names replaces the deployment's whole list for that run
(`resolve_deployment(..., version_pin=...)`). Documents in it that pin one target stop the run, as they fail
validation in a written list. Leaving out a document that pins something the deployment uses is a warning,
`version_pin_dropped`: that target would fall back to its own declared value (a remote to its `reference`).

### What the build records, and the deploy check (V8)

`build run` writes two things:

```yaml
# builds/datahub-prd/version.yaml - the pins the build used, an ordinary version document
apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: datahub-prd
spec:
  pins:
    remotes:   { iac: v2.1.0 }
    artifacts: { datahub-api: 1.4.0 }
---
# builds/datahub-prd/resolved.yaml - the build's record (excerpt)
version: [prd-core, prd-datahub]
pinned_by: spec.version          # or --pin
pin_sources:
  remotes/iac: prd-core
  artifacts/datahub-api: prd-datahub
promotion: { workspace: datahub, ring: prd, order: 3, version: [prd-core, prd-datahub] }
```

- `version.yaml` holds only pins on targets the deployment uses, and no `promotion` (a copy put into a solution
  would claim a ring slot). Provenance lives in `resolved.yaml`, which the audit record already hashes, so what a
  deploy ran and from which document is in the record without a new manifest field. A build without a version
  writes no `version.yaml` and removes one an earlier build left.
- `deploy run` (`check_build_version()`) keeps its scope: only a build holding a Helm module is judged, and only
  the `charts` and `artifacts` pins such a build applies, by value against `version.yaml`, restricted to what the
  deployment uses. A version document edited between build and deploy is caught even when its name is the same
  (`build_version_mismatch`). A build without `version.yaml` is judged by the documents' names, as before.

### Rings and promotion (V9–V12)

- **V9.** A deployment's ring is the `spec.promotion` of the first tagged document it lists
  (`first_tagged_version()`, shared by `deploy list` and the promotion view). Shared documents stay untagged and
  never decide a ring. Two listed documents tagged for different rings: `version_ring_conflict`. A deployment in
  no ring while others of its workspace are: information, `version_ring_none`.
- **V10.** A workspace's rollout is read from its deployable deployments: each tagged document they list is a slot,
  and a slot's rows are the pins its deployments run. Every pin of the slot's own tagged document is shown (so a
  one-document deployment shows exactly its document); a pin another document owns is shown where the deployment
  uses its target. Each row has its owner, marked shared when deployments of another workspace list it. The
  ring/order and slot checks run over these slots; a tagged document no deployment lists is
  `promotion_orphan_ring`. `promote status` without a workspace shows every workspace in a rollout, `--ring`
  narrows it.
- **V11.** `promote apply WORKSPACE RING` copies the preceding ring's values into the documents that own each key in
  `RING`, resetting `status` and `reviewed`. Per (key, owner):
  - a document deployments of other workspaces list too is skipped as `shared` unless `--shared` (promoting one
    product never moves another);
  - a document only some of the ring's deployments list (a customer's own) is never written (`own document`);
  - on the source side, only what all source deployments run in common counts; a key the source ring runs only
    from customers' own documents is skipped (`source own documents`);
  - a source key the target does not pin is reported (`not pinned`), not created.

  Every skip is listed; nothing is dropped silently.
- **V12.** In one ring, a key owned by **untagged** (shared) documents at different values is a warning,
  `version_ring_drift`. Product and customer documents are not judged: a customer differing from its product is
  the point of listing its own document.

### Tooling (V13, V16)

- **V13.** `version set NAME ...` names the document, unchanged. `version update NAME` offers only the targets the
  deployments listing `NAME` use and no other of their documents pins (`version_scope()`); a document no
  deployment lists is compared with the whole solution. "Stale" still means a pin whose target no longer exists.
  `version new NAME [--from VERSION]` writes no workspace. `version show NAME` lists a document's pins and the
  deployments that list it.
- **V16.** `deploy list` rows have `versions` (the list, through `extends`); `--version X` matches a deployment
  whose list contains `X`. `path list version --workspace W` lists what `W`'s deployments list.
- **V15.** No lock file: the matrix prevents a mismatch inside a deployment, what ran is in the build output, and
  what will run is computed on demand (`deploy images`, `promote status`).

### Cost

`pinnable_targets()` is the expensive part (it walks and scans every document a deployment reaches); composing is
cheap. Two rules keep it linear and small:

- **A shared document is scanned once per run (`TargetCache`).** Each document's contribution is cached by
  `(kind, name)` for one run; the deployment document itself never is (`--pin` resolves it differently).
- **Each deployment is walked once per run (`VersionMatrix`).** `SolutionContext.resolve()` builds one: every
  deployable deployment's composed version, composition findings, own documents, used targets and ring slot. The
  matrix checks and the promotion checks share it; `promote status` builds one for every workspace it shows.

At 300 deployments (the per-customer scale solution) this took `strata validate`'s check pass from 4.56s to 3.37s
and `pinnable_targets()` from 1.30s to 0.16s. Two counting tests guard it without wall-clock timings: more
deployments of one workspace cost only their own document scans, and with a rollout each deployment is walked
exactly once.

### Where the code is

| Module                                   | What                                                                                       |
| ---------------------------------------- | ------------------------------------------------------------------------------------------ |
| `controllers/version_composition.py`     | `compose_versions()`, `deployment_version()`, `VersionMatrix`, the matrix checks, `pin_dropped_findings()`, `version_scope()`, `unlisted_versions()` |
| `controllers/pinnable_targets.py`        | `pinnable_targets()`, `TargetCache`                                                        |
| `controllers/promotion_controller.py`    | the promotion view, `apply_promotion()`, `rollout_workspaces()`                            |
| `controllers/deployment_resolution.py`   | `first_tagged_version()`, `deploy list` rows                                               |
| `models/version_model.py`                | `ComposedVersionModel`, `pin_owner()`, `version_names()`, the removed-field error          |
| `models/deployment_model.py`             | `VersionNames`, `VersionPins` (what `--pin` passes down), `version_name_list()`            |
| `tests/.../test_version_composition.py`  | the walk-through as tests: matrix findings, `--pin`, build output, promotion, cost         |

### Out of scope

- **A separate infrastructure version per product** when every product root comes from one remote: a shared
  document owns that remote's pin, so the products' infrastructure code moves together in a ring. A pinnable target
  per product root is a separate question, waiting on whether a real solution needs it (`pin-granularity.md`, work
  doc).
- **Overriding one pin of a listed document** without a copy of it: a deployment lists a different document instead.
  Revisit when a real case needs finer grain (same work doc).
- **Run order between products.** Pins express versions, not order.

## Related Decisions

- [ADR-0063](../decisions/0063-a-deployment-lists-several-version-documents.md) — a deployment lists several
  version documents; every pin has one owning document.
- [ADR-0064](../decisions/0064-deprecate-version-spec-workspace.md) — the deployments decide where a version
  document applies (superseded in timing by ADR-0065).
- [ADR-0065](../decisions/0065-remove-version-spec-workspace-in-2-7.md) — `Version.spec.workspace` removed in 2.7.
- [ADR-0019](../decisions/0019-version-pinning.md) — `kind: version`, pins with `reason`, logged application.
- [ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md) — a version reference is an identity.

## History

- **Raised by a `version_workspace_mismatch` report**: a document left without `spec.workspace` was fixed to the
  first deployment's workspace, so one document could not serve two products, and products sharing a remote
  copied its pin into every product document with nothing keeping the copies equal.
- **Composition inside the version document was the first design**: `spec.include` of other version documents with
  `spec.overrides` (a required `reason`) as the only way to change an included value, and a document's workspaces
  as the union of its includes. Replaced by lists on the deployment, where pins take effect: one level of
  composition finds the same conflicts with no nesting, cycles or override stacking, and promotion keeps writing
  one owning document per key. What it gave up, one file that says what a ring runs, is `promote status --ring` and
  the build's `version.yaml`.
- **Rejected: `spec.workspaces: [a, b]` and `spec.workspace: "*"`.** A list makes every product's pin a change to a
  file all listed workspaces run, and a product could not be promoted alone; `"*"` drops the limit altogether.
- **Equal values from two owners are an error, not a warning.** A warning would let today's copied per-product pins
  validate unchanged, but every later bump of one copy would split a deployment; migrating is one move per shared
  key into a shared document.
- **`spec.workspace` went from optional limit to removed, in one release.** As a limit it duplicated what the
  deployments say and went stale silently; `version_document_unused` covers most of it. A deprecation until 3.0 was
  planned (ADR-0064) and dropped (ADR-0065): with two users, both delete the line in the same upgrade.
- **`--pin` replaces the whole list**, so a run's versions read from the command line alone; the cost, leaving a
  document out, is the `version_pin_dropped` warning rather than a "swap one document" syntax.
- **Provenance is in `resolved.yaml`, not `version.yaml` annotations.** Annotations are free-form and unchecked and
  go stale in a copied file; `version.yaml` stays a clean, reusable document without a ring tag.
- **The deploy check kept its Helm-only scope** but compares values: widening it to every build would have made
  edits between build and deploy errors for Terraform-only builds that worked before.
- **Ring drift is judged only for untagged owners**, after a first version warned on every customer document that
  differed from its product document (the very case listing a customer's own document is for).
- **`promote apply` judges per (key, owner) and only what all source deployments run in common**, after a first
  version stopped on any disagreement (including customers' own documents) and silently dropped a key's second
  owner.
- **`version update` keeps "stale" as "target gone"**: a pin no listing deployment uses may be staged on purpose,
  so it is information in `validate` (`version_pin_unused`), not a removal proposal.
- **The cost was in what each deployment uses, not in composing**: profiling 300 deployments showed
  `pinnable_targets()` at 1.30s and composition at 0.02s, which is why the fix is a per-document cache plus one
  computation per deployment per run.
