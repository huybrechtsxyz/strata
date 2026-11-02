# Version Composition - Work

- Status: draft - parked until after 2.0.0-RC3, then re-checked against the released code
- Last updated: 2026-11-02

## Overview

A `kind: version` document today is one flat set of pins, bound to one workspace, and a deployment
names exactly one. That is enough for one product per ring. It breaks down when a ring runs several
products that share something:

- **One release per ring across several workspaces.** cfg-int runs one workspace per product
  (Staging, DataHub, Binocs, Dispatcher after ADR-0017 there). A release of four products in one ring
  is four version documents edited in lockstep.
- **A shared artifact in several versionings.** Anything every product uses (the `iac-int` remote the
  product roots come from, a shared image, the core modules) is pinned once per product document.
  Nothing says those pins must agree, so one ring can silently run two versions of the shared part.
- **A customer that differs from its ring on a platform pin** needs a document repeating every
  platform pin ([remotes-at-scale.md](remotes-at-scale.md) Q6, closed until needed).

This doc designs **version composition**: a version document may include other version documents,
and the effective pin set is their merge. Merging several files into one effective configuration is
a known pattern, and this design takes its rules from the existing ones rather than inventing new
ones (see "Prior art").

Strata's principle is *configure once, reuse*. Here that means: every pin has exactly one owning
document, and every other document that needs it includes that document instead of copying the pin.

## Current Design / Progress

### Requirements

| #   | Requirement                                                                                                                                     | Source                                  |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------- |
| R1  | One document per ring can carry the release of several workspaces                                                                               | cfg-int request                         |
| R2  | A shared artifact is pinned in one place and reused, never copied                                                                               | "configure once, reuse"                 |
| R3  | A shared artifact can never run at two versions inside one ring without an explicit, visible act                                                | the core-in-several-versionings problem |
| R4  | A product can still be promoted through rings on its own                                                                                        | cfg-int ADR-0006                        |
| R5  | A customer can still move on its own cadence (`promote apply --pin`)                                                                            | promotion.md S1-S10                     |
| R6  | A deployment can differ from its ring on one pin without repeating the ring                                                                     | remotes-at-scale.md Q6                  |
| R7  | Every applied pin is logged with the document it came from                                                                                      | ADR-0019 decision 7                     |
| R8  | `reason` on a held pin is never lost, and tooling edits the document that owns a pin                                                            | ADR-0019, version-lifecycle.md          |
| R9  | Existing single-document solutions keep working unchanged                                                                                       | -                                       |
| R10 | What a deployment ran, at which version and from which version document, is readable from the audit record, without digging through git history | -                                       |

### Prior art

| Tool                        | Composition                                 | Same key from two sources     | Taken here                               |
| --------------------------- | ------------------------------------------- | ----------------------------- | ---------------------------------------- |
| Kustomize `resources`       | many bases, unioned                         | error (duplicate resource ID) | yes: includes are a disjoint union       |
| Kustomize `patches`         | explicit change on top of the union         | the patch wins, by intent     | yes: `overrides`, a separate section     |
| Maven BOM `import`          | a release file lists versions of many parts | first import wins, silently   | the BOM idea yes, the silent rule no     |
| Helm `-f a -f b`            | ordered layering                            | the last file wins, silently  | no: last-wins is the drift R3 forbids    |
| strata `Deployment.extends` | one parent, deep merge                      | the child wins                | no: one parent cannot compose N products |

The shape is "Kustomize for pins": composition can never change a value; changing an included value
is a separate, named act.

### Shape

Two new optional fields on `VersionSpecModel`, nothing else changes:

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: prd
spec:
  promotion: { ring: prd, order: 3 }
  include:                  # version documents whose pins this one adds, by name
    - core-2.1
    - staging-1.2
    - datahub-1.4
    - binocs-0.9
    - dispatcher-3.0
  pins: {}                  # pins this document owns (unchanged meaning)
  overrides:                # deliberate changes to an included pin; same categories as pins
    images:
      datahub-api:
        version: acr.io/datahub:1.4.1
        reason: "hotfix 4711, fold into datahub-1.5"
```

### Decisions

- **C1 - `spec.include: list[name]`, each a `References(PlatformKind.VERSION)`.** Order carries no
  meaning, because nothing is resolved by order (C2). Including the same document twice, directly or
  through two paths (two products both including `core-2.1`), counts once: identity is the document,
  not the path.
- **C2 - Includes are a disjoint union.** The effective pins are the document's own `pins` plus every
  included document's effective pins. The same `category/target` owned by two *different* documents
  is reported, naming both documents, and the fix the message names is to own the key in one
  document that both include. "Two owners" includes the document's own `pins` against an include:
  - **Different values: an error, `version_pin_conflict`.** The ring would run two versions (R3).
  - **Equal values: a warning, `version_pin_duplicate`.** Equal means the same `version` and
    `status`; `reason`, `reviewed` and `available` may differ. The origin map lists both owners and
    the effective pin is the one from the owner whose name sorts first, so the result never depends
    on include order (C1). Today
    the ring runs one version, so nothing breaks R3, and per-product documents with copied pins can
    be composed first and de-duplicated later. The cost: the next bump of one copy turns the warning
    into a conflict in that bump's PR, which the warning announces in advance.
- **C3 - `spec.overrides` is the only way to change an included value.** Same categories and pin
  shape as `pins`, with two extra rules: `reason` is required (the override is the thing a reviewer
  must understand), and the key must exist in the included set (an override of nothing is an error,
  `version_override_unused`; use `pins`). A key in both `pins` and `overrides` is an error. A key in
  `pins` that is also included follows C2, and the conflict message names `overrides` as the way to
  differ on purpose. So `pins` keeps meaning "keys this document owns", and `overrides` is the only
  section that may change a value someone else owns.
  An override also **resolves a conflict** among its includes: a ring that must run two products
  built against different cores can override the shared key, with a `reason`. That is the
  "explicit, visible act" R3 allows; without it the conflict stays an error.
- **C4 - Overrides apply only at the document that declares them.** A document's overrides change
  its own effective set, and the overriding document becomes the key's owner there; an including
  document sees the overridden value as that document's effective pin. Nested overrides stack: an
  outer document may override a key an inner one already overrode (the outer wins, by intent).
  Sibling overrides (two documents, neither including the other, both overriding one key) follow C2
  like any other pair of owners.
- **C5 - Nesting is allowed; a cycle is an error.** `version_include_cycle`, reported with the chain,
  same treatment as the `extends` chain of deployments. A product release may include the core it
  was built against, so composing two products built against different cores is a C2 conflict:
  "these two were not built together" becomes a validation error instead of a running ring.
- **C6 - One resolver, every consumer.** `resolve_effective_version(index, name) -> (VersionModel,
  origins, Diagnostics)` returns a `VersionModel` whose `spec.pins` is the merged set, plus a map
  `category/target -> owning document`. Every site that today does `index.get(PlatformKind.VERSION,
  ...)` and reads `.spec.pins` switches to it, so the overlays (`build_controller`,
  `workload_controller`, `value_controller`, `effective_images`, `image_tag_policy`, `audit_run`)
  need no change beyond the lookup. A document without `include`/`overrides` resolves to itself (R9).
- **C7 - `log_pin_applied` reports the owning document** from the origin map, and adds the
  composing document when they differ (`version=datahub-1.4 via=prd`). Overrides log as
  `source=override` (R7).
- **C8 - Workspace binding becomes a set.** `spec.workspace` stays a single optional name and still
  binds *that document*. A document's effective workspaces are its own plus those of everything it
  includes. A deployment must run one of its version document's effective workspaces; a document
  whose effective set is empty (a pure core or library document) binds nothing, as an unset
  `spec.workspace` does today. The existing first-referrer rule is unchanged for a document with no
  includes. `build_promotion_view()` groups by the effective set too, so a ring document with no
  `spec.workspace` of its own still appears in `promote status` for every workspace it includes.
- **C9 - Promotion tags are never inherited.** `spec.promotion` describes the document it is written
  on. A product release file is ring-independent and normally untagged; the ring document carries
  `ring/order`. So promotion slots (`(ring, order, wave)` uniqueness) are counted only on documents
  that declare them, and a customer document that includes the `prd` ring document does not claim
  the `prd` slot.
- **C10 - Pins are not scoped per workspace.** Considered: a pin applies only to deployments of its
  owning document's workspace, so DataHub and Binocs could pin `remotes.iac-int` to different refs in
  one ring. Rejected: that is precisely a shared artifact at two versions in one ring (R3). A
  product that must version independently needs its own pinnable target (Open question 1).
- **C11 - Tooling writes to the owner.** `version set <doc> <category> <target>` edits the section
  that holds the key in `<doc>` (`pins` or `overrides`). On a key `<doc>` only includes it is an error
  naming the owning document and both alternatives: an override in `<doc>`, or a new release file.
  `version update` judges *missing* keys against the effective set (a key an include already pins is
  not missing) and *stale* keys against the document's own `pins` and `overrides`. `check_version_pins()`
  checks `overrides` like `pins`. `version new --include A --include B` scaffolds a composing document
  (R8).
- **C12 - Promotion reads effective pins and writes own sections.** `promote status` shows the
  effective pins with their owner per row. `promote apply` copies only what the target owns: its own
  `pins` (as today, including `--pin` selection) and, from Phase 6, its `include` entries. It never
  writes an included key into `pins` (that would be a C2 duplicate) and never copies `overrides`: an
  override is the source ring's own decision with its own `reason`, as a hold is (promotion.md S4).
  `apply` lists the source overrides it did not copy, so nothing is dropped silently.
- **C13 - The build writes the effective version as its own artifact.** `build run` writes
  `versions.json` next to `resolved.yaml` and `sbom.json`: the version document the deployment named
  (or `--pin` chose), its ring, and every effective pin with its value and owning document
  (`remotes/iac-int: v2.1.0 from core-2.1`). `_manifest.json` references it by path and hash, the
  pattern it already uses for `resolved.yaml` and `sbom.json`, and keeps its own effective
  repositories, images and charts. A build-time artifact rather than a `_` audit record, because the
  pins are applied at build time and a build without a deploy should carry it too. "What did
  `c0062-prd` run, and which release file put it there" is then one file read (R10), and two runs
  diff as two files. Before a deploy, `version show NAME --effective` and `promote status` answer
  the same question for what *will* run.
- **C14 - No lock file.** Considered: a generated, committed `<ring>.lock.yaml` holding the effective
  pins, with `validate` failing when it is out of date, so a change to an included file shows its
  effect on every ring in the PR. Rejected: version mismatch is already prevented by C2, what ran is
  in the audit record (C13), and what will run is computed on demand; a generated file adds merge
  conflicts and a regeneration step for a review aid. v1's lock (pointer, `previous`, tamper hash)
  stays unported for the reasons in ADR-0019.
- **C15 - Promotion commands work per ring, not only per workspace.** Today `promote status
  WORKSPACE` and `promote apply WORKSPACE RING` only see version documents whose own `spec.workspace`
  equals the argument. A ring document that composes several products has no workspace of its own,
  so three things change:
  - **Finding the ring document.** `promote status`, `promote apply` and `validate_promotions()`
    match on the effective workspace set (C8), not on the declared `spec.workspace` alone, so `prd`
    appears for `datahub` because it includes `datahub-1.4`.
  - **A ring-wide view.** `WORKSPACE` becomes optional on `promote status`, with a repeatable
    `--ring`, so "what is in `prd`" is one command instead of one per product. Every row gains an
    `owner` column. Without a workspace and without `--ring` it shows every ring.

    ```
    $ strata promote status --ring prd
      ring  order  target              version               owner
      prd   3      remotes/iac-int     v2.1.0                core-2.1
      prd   3      images/staging-api  acr.io/staging:1.2.0  staging-1.2
      prd   3      images/datahub-api  acr.io/datahub:1.4.1  prd (override of datahub-1.4)
      prd   3      images/binocs-api   acr.io/binocs:0.9.0   binocs-0.9
    ```

  - **The workspace filter.** With a workspace, a row is shown when its owner is bound to that
    workspace or is bound to none (`core-2.1`: DataHub runs on it too). Another product's pins in the
    same ring document are left out.
  - **`promote apply` by ring.** On a ring document not bound to one workspace, the workspace
    argument means nothing: you promote `prd`, not "DataHub's `prd`". `apply` accepts the ring
    alone there, and promoting one product is the include selector of Phase 6
    (`promote apply prd --include 'datahub-*'`). The CLI shape is a loose end (Open question 6).

### What R1-R10 look like

**cfg-int, one ring release across four workspaces (R1, R2, R3).**

```yaml
# versions/core/core-2.1.yaml - the shared part, no workspace, owned once
spec:
  pins:
    remotes: { iac-int: v2.1.0 }

# versions/products/datahub-1.4.yaml - built and tested against core-2.1
spec:
  workspace: datahub
  include: [core-2.1]
  pins:
    images: { datahub-api: acr.io/datahub:1.4.0 }

# versions/rings/prd.yaml - what deployments name
spec:
  promotion: { ring: prd, order: 3 }
  include: [staging-1.2, datahub-1.4, binocs-0.9, dispatcher-3.0]
```

`iac-int` is pinned once. If `binocs-0.9` was built against `core-2.0`, the `prd` document fails
validation with `version_pin_conflict: remotes/iac-int owned by core-2.1 (via datahub-1.4) and
core-2.0 (via binocs-0.9)`.

**Product promotion (R4).** Release files are named by release and never edited after they are
included in a ring. Promoting DataHub to `prd` is one line in `rings/prd.yaml`: `datahub-1.3` becomes
`datahub-1.4`. The same file moves through `dev`, `qas`, `prd`, so a release is configured once.
(Per-ring mutable product files, `datahub-prd`, also work with the model; they give up the
"same file in every ring" property.)

**Customer cadence (R5).** Unchanged: per-customer `assets` pins live in the ring document's own
`pins` and `promote apply --pin` still copies them.

**Customer differs on a platform pin (R6).**

```yaml
# versions/customers/c0224-prd.yaml
spec:
  include: [prd]
  overrides:
    images:
      datahub-api: { version: acr.io/datahub:1.4.2, reason: "c0224 needs fix 4711 before the ring" }
```

**What ran (R10).** `build run` for `c0062-prd` writes `versions.json` next to `resolved.yaml` and
`sbom.json` (C13):

```json
{
  "deployment": "c0062-prd",
  "version": "prd",
  "ring": "prd",
  "pinned_by": "spec.version",
  "pins": [
    { "target": "remotes/iac-int",    "value": "v2.1.0",               "owner": "core-2.1",  "via": ["datahub-1.4", "binocs-0.9"] },
    { "target": "images/datahub-api", "value": "acr.io/datahub:1.4.1", "owner": "prd",       "override_of": "datahub-1.4", "reason": "hotfix 4711" },
    { "target": "images/binocs-api",  "value": "acr.io/binocs:0.9.0",  "owner": "binocs-0.9" },
    { "target": "assets/c0062-api",   "value": "v1.5.0",               "owner": "prd" }
  ]
}
```

`pinned_by` is `--pin` when the flag chose the version document. `_manifest.json` references the
file by path and hash.

### What composition does not solve

- **One remote carrying several products.** If every product root is fetched from `iac-int`, the
  product files cannot each pin it (C2), so infrastructure code for all products in a ring moves
  together while app images and artifacts move per product. That is the honest outcome of R3.
  Truly independent infrastructure code per product needs a pinnable target per product (Open
  question 1).
- **Run order between products** (Staging before its consumers). Pins express versions, not order.

### Implementation Plan

- [ ] **Phase 1 - Model.** `include` and `overrides` on `VersionSpecModel`; `OverridePinModel`
  (`VersionPinModel` with `reason` required); intra-document rules: no self-include, no key in both
  `pins` and `overrides`. Schema export, `docs/config/version.md`.
- [ ] **Phase 2 - Resolver.** `resolve_effective_version()` in a new `version_composition.py`
  (controllers): merge, diamond de-duplication, `version_pin_conflict`, `version_pin_duplicate`,
  `version_include_cycle`, `version_override_unused`, the origin map. Run over every version
  document from `semantic_checks`.
- [ ] **Phase 3 - Consumers.** Switch every version lookup to the resolver (C6), origin in
  `log_pin_applied` (C7), `resolved.yaml` records the composing and owning documents, `build run`
  writes `versions.json` and the audit manifest references it (C13).
- [ ] **Phase 4 - Workspace and promotion.** Effective workspace set (C8); tags not inherited (C9);
  `promote status` with optional `WORKSPACE`, `--ring` and an `owner` column, the workspace filter
  by owner, `validate_promotions()` over effective sets (C15).
- [ ] **Phase 5 - Tooling.** `version show NAME --effective` (merged pins, owner per pin), owner
  routing in `version set`/`update` (C11), `version new --include`.
- [ ] **Phase 6 - Promotion of includes.** `promote apply` copies include entries forward, selectable
  like pins (`--include 'datahub-*'`), with the same `--dry-run`.
- [ ] **Phase 7 - Example.** `config/versions/` split into a core, a product release and a ring
  document, dogfooded end to end.

## Related Decisions

- [ADR-0019](../decisions/0019-version-pinning.md) - `kind: version`, pins with `reason`, logged application.
- [ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md) - includes are identity references, not paths.

## Remaining Work / Open Questions

1. **A pinnable target per product root.** For products whose infrastructure code must move on its
   own: a remote per product root (same URL declared several times, against configure-once), or a
   pin keyed by remote and path with product-scoped tags (`datahub/v1.4`). Needed only if cfg-int
   wants that independence; ask them.
2. **Immutability of release files.** Should strata warn when a document included by a tagged ring
   changes after it was first included (needs git or a hash), or is that review policy only?
3. ~~**Equal values from two owners (C2).**~~ Decided: a warning when equal, an error when different.
   See C2.
4. ~~**`promote status` without a workspace.**~~ Decided: C15.
5. **Unused release files.** Not version-specific: a generic `strata validate` warning,
   `document_unreferenced`, for any document not reachable from a deployment through the reference
   graph (`build_reference_graph()` already has every edge). Reachability, not "zero inbound edges",
   so a release file included only by an unused ring document is reported too. Open: which kinds
   are roots or are bound by type rather than name (exempt), and version documents used only
   through `--pin` in CI, which strata cannot see.
6. **`promote apply` CLI shape.** `promote apply WORKSPACE RING` has two positional arguments, so
   "ring alone" (C15) cannot simply drop the first one. Options: a `--ring` form beside the
   positional one, a separate verb, or reading one argument as the ring when it names a ring document
   not bound to a workspace. Decide before 2.0.0 is final if the CLI is frozen there, so composition
   can land later as an additive change.

### Before resuming (after RC3)

Composition is meant to land after 2.0.0 as an additive change: two optional fields, a new build
artifact, optional CLI arguments. Loose ends to settle first:

- **Re-check the call sites in C6** against the RC3 code: every place that looks up a version
  document by name and reads `.spec.pins`.
- **Open question 1 needs cfg-int's answer** (do product roots need their own infrastructure
  version?). Ask them now so the answer is there when work resumes.
- **Open question 6** if the CLI is frozen at 2.0.0.
- **Open question 5** (unused documents) is not version-specific; it moves to its own work doc if
  it is picked up.

## Changelog

- 2026-11-01: First draft, from the cfg-int request for one release document per ring across
  workspaces and the shared-core problem it exposes.
- 2026-11-01: C2 decided: two owners with equal values are a warning (`version_pin_duplicate`), with
  different values an error (`version_pin_conflict`). Both keep R3; the warning lets today's copied
  per-product pins be composed before they are de-duplicated. Rejected: an error for equal values
  too, which forces extracting a core document before anything validates.
- 2026-11-01: Consistency pass. C2 covers own `pins` against an include (C3 had made that an error
  even for equal values) and picks the effective pin of equal duplicates by owner name. C3: an
  override may resolve a conflict, the explicit act R3 allows. C4: nested overrides stack, only
  sibling overrides conflict. C8: promotion groups by the effective workspace set (a ring document
  without its own workspace vanished from `promote status`). C11: `version update` judges missing
  keys against the effective set. New C12: `promote apply` writes only own sections and never copies
  overrides.
- 2026-11-01: Lock file rejected (C14): C2 prevents mismatches and the audit record shows what ran.
  Added R10 and C13: the audit manifest records the version document and every effective pin with
  its owner, so what was deployed is read from the record, not reconstructed from git history.
- 2026-11-01: C13 moved from a section in `_manifest.json` to a `versions.json` build artifact the
  manifest references by hash, like `sbom.json`: it exists for a build without a deploy, and two runs
  compare as two files.
- 2026-11-02: C15 decided (promotion commands per ring, owner column, workspace filter by owner),
  closing open question 4; `promote apply`'s CLI shape is open question 6. Parked until after
  2.0.0-RC3, with the loose ends listed under "Before resuming". promotion.md's forward note and
  remotes-at-scale.md Q6 now point here.
