# Promotion — Work

- Status: draft
- Last updated: 2026-10-06

## Overview

Real, near-term evidence, not hypothetical: `cfg-int-deployment` already has
a handful of real deployments today and is expected to reach roughly 250
customers across 4 rings each (plus however many instances per ring). This
is a real, named adopter coming at real scale — not the "zero real usage"
situation ADR-0019's original census found for `promotion`/`gates`. It's
also why "release train" was the natural framing for the CI/CD discussion
in [version-lifecycle.md](version-lifecycle.md): that's literally how that
repo already operates. The evidentiary gap that remains is narrower: no
second real *rollout* has been built against the specific shape chosen
here yet, so treat the schema below as informed-but-unproven, not as
something a real consumer has already exercised.

v1 built the full ring/progression/wave/strategy/gates machinery
(`ProgressionRingModel`, `PromotionStrategyModel`, `PromotionRecordModel` +
a service) before anyone adopted it — `deployment_model.py`'s own census
found zero real use of `versions`/`promotion`/`gates`, and only **one**
real `kind: version` document ever written (`haven/versions/prd.yaml`).
This doc still does the opposite of that: the smallest schema that answers
"where is version X right now, across every ring?", shipped as a
read-only command first — enforcement only if real demand shows up.

## Current Design / Progress

### Why v1 was confusing — 7 concepts for one idea

| Concept                                     | Problem                                                                                                               |
| ------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `ProgressionRingEnvironmentModel.wave: int` | Two wave systems: intra-ring integer waves...                                                                         |
| `PromotionWaveModel` (named, on strategy)   | ...and separately, named ordered waves on the strategy. Both schedule rollout.                                        |
| `progression` + `strategy` as 2 kinds       | A pointer between them; strategy adds almost nothing once its other fields go.                                        |
| `type: remote\|helm_chart\|image\|module`   | Duplicates what `pins.{images,charts,remotes,artifacts}` already says.                                                |
| `gates.require_progression_order: bool`     | Opt-in enforcement of the thing you just declared. Why declare an order and not enforce it?                           |
| `scope: tenant`                             | A third targeting axis, on top of ring + wave.                                                                        |
| `versions_path` + `@repo/` syntax           | Path indirection, separate from everything else's name-based addressing (ADR-0015 is identity-based, not path-based). |

Plus a whole `promotion_record_model.py` + service just for history. None
of it is ported.

### No new kind — ring/order/wave are metadata on `Version`, not a separate document

An earlier draft of this doc built `kind: promotion` as a dedicated
document owning a list of rings/waves (each with its own version
reference) plus a `Deployment.spec.promotion` field pointing at it, with a
mutual-exclusivity rule against `spec.version`. Revisited under critical
review: once `Version.spec.workspace` existed (it groups every version
document by product already), the dedicated kind's only remaining job was
producing a `promote status` table — which is equally derivable by
tagging the `Version` documents that already exist, letting `Deployment`
always use the one reference mechanism ADR-0019 already built. No second
mode, no mutual-exclusivity validator, no conditional-reference chain for
`ring`/`wave`.

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: dspapi-prd-canary
spec:
  workspace: dspapi
  promotion:
    ring: prd       # which ring this document represents
    order: 3        # sequence position in the rollout (dev=1, qas=2, prd=3)
    wave: canary    # optional — distinguishes documents sharing the same (ring, order)
  pins:
    images:
      dspapi: "1.1.0"
```

All three fields are optional, grouped under one small sub-model
(`VersionPromotionModel`, field name `spec.promotion`) rather than three
loose top-level fields on `VersionSpecModel` — matches how
`DeploymentLayersModel`/`DeploymentLockingModel` already group related
optional fields elsewhere in this schema, and keeps the word "promotion"
alive in the vocabulary as a named sub-concept even though the *kind* is
gone. A single-app `Version` document (Worked Example A below) omits
`spec.promotion` entirely. `Deployment.spec.version` is unchanged from
ADR-0019: a plain name reference, nothing else new.

**What dropping the dedicated kind gives up, and what replaces it**: a
`Promotion` document was more legible for a human trying to answer "what
does this product's whole pipeline look like" from one file, and could
declare a ring before any version document for it existed yet. Both real,
but the first is exactly what `strata promote status` (below) now exists
to answer by *computation* instead of *declaration*, and the second
(declaring a not-yet-real ring) isn't worth protecting given this doc's
own "ship visibility for what exists, not what's aspirational" stance
everywhere else.

**New Phase 2 check, replacing the old ring/wave conditional-reference
pair**: within one workspace, `spec.promotion.(ring, order)` must be
unique unless distinguished by a different `wave` — two documents
claiming the same ring/order slot with nothing to tell them apart is
ambiguous, not a feature.

**Dropped, still unjustified by any evidence** (all still true, none of it
resurrected by this revision):

- `type` — pins already say it (`VersionPinsModel`'s `images`/`charts`/
  `remotes`/`artifacts` categories).
- `strategy` as a separate kind, and `kind: promotion` itself, per above.
- `scope: tenant` — a tenant's rollout position is just which `Version`
  document its deployment references; no third targeting axis needed.
- `require_digests` / digests generally — v2 has no digest field anywhere
  yet (`VersionPinModel` has none); needs the field before any gate could
  exist.
- Promotion records — git history of the version files already is the
  record; no new persisted kind for it.
- **`require`/quorum gating between rings or waves, entirely.** Not even
  an inert, opt-in field. Nothing stops `prd`'s `general` wave from running
  before `canary` finishes; no mechanism watches for it. Real gating needs
  real design first (what counts as "a wave succeeded"? who evaluates it,
  when?) — revisit only if a real rollout needs the gate.

### `Version.spec.workspace` — the exclusivity anchor, unchanged

A `Version` document was otherwise just `{meta.name, spec.pins}` — it
never said which workspace it was *for*. The only way to know was to grep
every `Deployment` that happened to reference it; an accidental
cross-workspace reference would be invisible until someone went looking,
and editing that document would then silently reach a stray deployment
too.

`spec.workspace` is `Annotated[PlatformName, References(PlatformKind.WORKSPACE)]
| None` — optional, Phase 2 validated against every `Deployment` that
actually resolves to this document. First real reference establishes the
expected workspace if the field itself is unset; a declared `spec.workspace`
is authoritative; a mismatch is an error. Same "declare it, then validate
it matches reality" pattern this codebase already uses for
`DeploymentLayersModel.segments` (`semantic_checks.py`'s
`_check_deployment_layers()`).

`version new`/`version update` ([version-lifecycle.md](version-lifecycle.md))
take `--workspace`/`--ring`/`--order`/`--wave` to populate `spec.workspace`
and the nested `spec.promotion` fields at creation.

**Forward note, not designed further here**: `Deployment.spec.workspace` is
singular today, so one deployment means one workspace. If a deployment
ever needs to span multiple workspaces, `spec.workspace` on `Version`
would become a list too — the link-back validation logic doesn't change
shape, just checks membership in a list instead of equality.

### `strata promote status <workspace>` — ship visibility before enforcement

The valuable half of v1 was never the gates — it was answering "where is
1.1.0 right now?" Computed entirely at read time, no new persisted state:
find every `kind: version` document with `spec.workspace == <workspace>`,
group by `spec.promotion.(order, ring)`, list by `wave` within a group,
resolve each pin:

```
$ strata promote status dspapi
ring  order  wave     version       status
dev   1      -        1.3.0-rc2     unverified
qas   2      -        1.2.0         current
prd   3      canary   1.1.0         current
prd   3      general  1.0.0         current   ← behind
```

The `status` column (a pin's own stated rationale — `current`/`held`/
`unverified`, from `VersionPinModel`) and the `← behind` marker (computed
by comparing orders against each other) are independent — a ring can be
both `current` *and* behind; nobody declared a hold, it just hasn't been
promoted yet. This table is the "what does the rollout look like" overview
a dedicated `Promotion` document used to provide declaratively — now it's
computed from whichever `Version` documents actually exist, same shape
either way.

### The "version" column is prospective, not necessarily actual — reopened, not resolved

Flagged earlier in this doc's history, then dropped during a later
consolidation pass — re-added deliberately so it doesn't silently vanish
again. The table above reads the **pin's current value** — what the
*next* `deploy run` for that ring/wave would use — not necessarily what's
*actually running* right now for any specific deployment instance. These
coincide only when every deployment sharing a `Version` document has
already been redeployed since its last edit; staggered rollout (the whole
reason rings/waves exist) is exactly when they diverge — if `general`'s
pin gets bumped, this table shows the new value immediately even if
nobody has run `deploy run` against it yet.

The fix floated earlier — source this column from the per-deployment audit
manifest once wired (`ManifestArtifactsModel.images`/`.repositories`,
[version-lifecycle.md](version-lifecycle.md)'s audit gap), falling back to
the live pin only when nothing's been deployed yet — is still the right
direction. Not designed in detail; genuinely open.

### `strata promote apply <workspace> <ring> [--wave W]` — mechanics settled, implementation not built

Given the lifecycle above, promoting a ring now means one edit in one
`Version` document's pin — rather than N deployment edits — because
deployments never carried their own copy of the reference to begin with.
`strata promote apply` as a mechanical copy-forward (not a judgment call —
transcribing a value already proven out at a lower `order`, the same
distinction that makes `version update`'s key-reconciliation acceptable
where a value-setting command wasn't) is still worth having.

**Mechanics, settled**: find the `Version` document(s) at the target
ring's `order`, find the document(s) at the preceding `order` in the same
workspace, copy forward:

- **Every common pin key**, not just the ones that differ — partial
  copying would leave it ambiguous which keys were actually promoted.
- **Resets `status` to `current` and `reviewed` to today** on the target —
  a `held`/`unverified` status was the *source* ring's own rationale; it
  doesn't automatically still apply at the new ring, and silently carrying
  it forward would misrepresent a fresh promotion as an old, unreviewed
  decision.
- **Requires an explicit `--wave`** when the target ring has more than
  one — errors with the wave list rather than guessing. Canary and
  general are often deliberately at different source points; applying to
  both at once by default would be the wrong default far more often than
  the right one.

Still genuinely open:

- Still **not** enforcement — no gating on wave health, no requirement
  that anything finish first (that stays deferred). It only removes
  copy-paste risk from a step that's otherwise mandatory and manual.
- Ships alongside `promote status`, or right after it?

### Validating that the right "promotions" get created

Dropping `kind: promotion` means nothing stops a user from tagging two
`Version` documents inconsistently — the checks that catch it, split by
whether they need to see more than one document:

**Phase 1 (intra-document, on `VersionPromotionModel` itself, no lookup
needed)**:

- `wave` without `ring` is rejected — a wave of *which* ring?
- `ring` without `order` is rejected — breaks sortability, the entire
  reason `order` exists. No inference from name or file order; the user
  states it explicitly, same "no guessing" rule that killed inferring
  ring from `environments` earlier in this doc's history.

**Phase 2 (cross-document, scanning every `Version` document sharing a
workspace together)**:

- **`ring` ↔ `order` must biject.** The same `order` must always map to
  the same `ring` name, and vice versa, across the whole workspace. This
  is the one that actually catches real typos — `ring: prd, order: 3` in
  one document and `ring: prod, order: 3` in another is a direct
  contradiction (is order 3 "prd" or "prod"?), almost never intentional.
- **`(ring, order, wave)` must be unique** — two documents can't both
  claim the exact same slot.
- **No mixing a waveless document with waved ones at the same `(ring,
  order)`** — a group sharing a ring/order slot is either exactly one
  document with `wave: None`, or every document in it has a distinct,
  non-null wave. Can't have both at once; that's an unresolved "is this
  the only version here, or one of several?" contradiction.

**Worth a warning, not an error**: an orphaned ring/wave — a tagged
`Version` document no `Deployment` actually references. Ambiguous enough
(typo in some deployment's `spec.version`, or a ring deliberately
provisioned ahead of real deployments) to not hard-fail on.

**Deliberately not validated**, consistent with "no enforcement"
everywhere else in this design: gaps in `order` (reserving room for a
future ring is legitimate), the same `wave` name reused across different
rings (`canary` at both `dev` and `prd` is a reasonable convention, not a
conflict), and an untagged `Version` document coexisting in a workspace
that also has tagged ones (opting into rollout visibility is per-document,
not all-or-nothing).

### `PromotionView` — the model strata builds internally, the user never authors

Running any of the above, or `promote status`/`apply`, needs an in-memory
aggregate: every `Version` document sharing a workspace, grouped by
`(ring, order, wave)`, sorted by `order`. That's the same *shape*
`kind: promotion` used to represent — the concept didn't disappear, it
moved from **persisted and user-authored** to **computed and
strata-internal**. The difference that actually matters: a persisted kind
is a second source of truth a human has to keep in sync with reality; a
pure function computed fresh on every call has nothing to go stale,
because nothing is written for it to go stale *from*.

```python
class PromotionWaveView(BaseModel):
    wave: str | None
    version: VersionModel          # the resolved document for this slot

class PromotionRingView(BaseModel):
    ring: str
    order: int
    waves: list[PromotionWaveView] # one entry if untagged/waveless, N if split

class PromotionView(BaseModel):
    workspace: str
    rings: list[PromotionRingView] # sorted by order

def build_promotion_view(workspace: str, index: DocumentIndex) -> tuple[PromotionView, Diagnostics]:
    ...  # groups, sorts, and runs every check above — one place, not scattered
```

**One builder, three callers**: `promote status` renders it as a table,
`promote apply` reads the target ring's view plus the preceding `order`'s
view and diffs their pins, and Phase 2 validation just runs the builder
and surfaces its `Diagnostics`. If ring/wave gating is ever actually
built (still explicitly deferred, no timeline), this builder is exactly
where it would attach — a pure function is a perfectly good hook for that
later; it doesn't require having kept a persisted kind around "just in
case."

### Surfacing the resolved view back to the user

Two distinct asks, two different scopes — worth keeping separate rather
than one new artifact trying to do both:

**1. "Did my deployment land where I meant it to?" — `resolved.yaml`, one
new optional section, deployment-scoped.** `build_controller.
write_resolved_manifest()` already writes `resolved.yaml` per deployment
(`{variables, features, secrets, properties, custom}`) — already hashed
and referenced in the audit manifest as `artifacts.platform`. Adding a
`promotion` section reuses that existing, already-audited artifact rather
than inventing a new one, and matches its existing "this deployment's own
slice" scope rather than fighting it:

```yaml
# resolved.yaml — existing file, one new optional section
variables: {...}
features: {...}
secrets: {...}
properties: {...}
custom: {...}
promotion:                     # only present when spec.version resolved through a tagged document
  workspace: dspapi
  ring: prd
  order: 3
  wave: canary
  version: dspapi-prd-canary    # which Version document this deployment actually resolved to
```

Catches a different mistake than the Phase 2 checks above: those catch
*contradictions between documents* (typo'd ring names, broken bijections);
this catches "the deployment I just built landed somewhere other than
where *I* meant it to" — a human-intent check, not a document-consistency
one. `resolved.yaml`'s documented shape
(`docs/design/build-time-value-categories.md`) needs updating to include
this new optional key.

**2. "What does the whole pipeline look like, as of right now?" —
`strata promote view <workspace> [--output <path>]`, a separate command,
deliberately not part of the audit trail.** `build run` operates on one
deployment; computing and writing the entire workspace's `PromotionView`
as a side effect of building one deployment would be scope leakage
(building `dspapi-c0224-prd` shouldn't have a side effect touching
information about every *other* tenant's deployments too) — so this isn't
a `build run`/`deploy run` side effect at all. It's its own command,
invoked deliberately:

```
$ strata promote view dspapi --output promotion-view.yaml
```

Reuses the exact same `build_promotion_view()` from above — same data
`promote status` renders as a table, here emitted as the full structured
`PromotionView` (every ring/wave, every resolved version) in YAML/JSON,
stdout by default. **Explicitly not wired into `audit_run.py` or
`_manifest.json`** — no automatic hashing, no automatic reference, not
triggered by `deploy run` or anything else. Keeping it a separate,
explicitly-invoked command rather than folding it into the audit trail
makes that boundary unambiguous: this is a point-in-time export a human
or a CI step asks for on purpose, not a compliance artifact strata
generates on its own initiative. A separate verb from `promote status`
rather than a flag on it, too — `status`'s table format is for a human
glancing at a terminal and can change freely; `view`'s structured output
is meant to be a stabler, tool-consumable export, worth letting evolve on
its own.

**Naming matters here, deliberately**: the file itself is called
`promotion-view.yaml`, not `promotions.yaml` — the `-view` makes clear
it's a computed snapshot, never a source a user edits, so nothing confuses
it with a resurrected `kind: promotion` source document sitting in the
solution tree. For the same reason this file should *never* be committed
to the repo as checked-in, regenerated-on-demand output — that's exactly
the "second source of truth that can go stale" problem this whole
redesign eliminated. If a durable historical record ever matters later
(not designed here), that's a reason to run `promote view` as a step
inside a release pipeline and archive *that* output wherever the pipeline
already archives its own artifacts — still not strata's own audit trail.

### Worked example A — single app, no ring/order/wave at all

The common/default case — one app, one version document, no rollout
pipeline. Matches the one real production example.

```yaml
# workspaces/hearth.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: hearth
spec:
  provisioners:
    - name: hearth-helm
      tool: helm
      source: { remote: infra, source_path: charts/hearth }
  execution:
    - name: deploy-app
      provisioner: hearth-helm
      targets: [hearth]
```

```yaml
# versions/hearth-prd.yaml — no ring/order/wave; this product has no multi-ring pipeline
apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: hearth-prd
spec:
  workspace: hearth
  pins:
    charts:
      hearth: 1.1.0
```

```yaml
# deployments/hearth-prd.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: hearth-prd
spec:
  workspace: hearth
  environments: [prd]
  version: hearth-prd
```

An upgrade is a hand-edit to `versions/hearth-prd.yaml`'s `charts.hearth`
value — nothing else changes.

### Worked example B — multi-ring, with real wave divergence

`dspapi` across three rings, with `prd` split into a canary wave
(genuinely ahead) and a general wave — the scenario ring/wave tagging
exists for. Every version reference lives on the `Version` document
itself; deployments just reference one by name, same as example A.

```yaml
# versions/dspapi-dev.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: dspapi-dev
spec:
  workspace: dspapi
  promotion:
    ring: dev
    order: 1
  pins:
    images:
      dspapi: "1.3.0-rc2"

# versions/dspapi-qas.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: dspapi-qas
spec:
  workspace: dspapi
  promotion:
    ring: qas
    order: 2
  pins:
    images:
      dspapi: "1.2.0"

# versions/dspapi-prd-canary.yaml — canary genuinely runs ahead of general
apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: dspapi-prd-canary
spec:
  workspace: dspapi
  promotion:
    ring: prd
    order: 3
    wave: canary
  pins:
    images:
      dspapi: "1.1.0"

# versions/dspapi-prd.yaml — general's shared file, same ring/order, different wave
apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: dspapi-prd
spec:
  workspace: dspapi
  promotion:
    ring: prd
    order: 3
    wave: general
  pins:
    images:
      dspapi: "1.0.0"
```

```yaml
# deployments/dspapi-dev.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: dspapi-dev
spec:
  workspace: dspapi
  environments: [dev]
  version: dspapi-dev

# deployments/dspapi-qas.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: dspapi-qas
spec:
  workspace: dspapi
  environments: [qas]
  version: dspapi-qas

# deployments/dspapi-c0224-prd.yaml — the sandbox tenant, canary wave
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: dspapi-c0224-prd
spec:
  workspace: dspapi
  environments: [prd]
  tenant: c0224
  version: dspapi-prd-canary

# deployments/dspapi-c0062-prd.yaml — an ordinary tenant, general wave
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: dspapi-c0062-prd
spec:
  workspace: dspapi
  environments: [prd]
  tenant: c0062
  version: dspapi-prd
```

```
$ strata promote status dspapi
ring  order  wave     version       status
dev   1      -        1.3.0-rc2     unverified
qas   2      -        1.2.0         current
prd   3      canary   1.1.0         current
prd   3      general  1.0.0         current   ← behind
```

Canary (`1.1.0`) genuinely runs ahead of general (`1.0.0`) — an actual
version difference, not deploy-order theater, because each wave is its own
document. Promoting `general` by hand (a direct edit to
`versions/dspapi-prd.yaml`'s `pins.images.dspapi`) can set it to whatever
value an operator has actually decided on — including matching `canary`'s
own `1.1.0`, if that is the judgment call being made. **`strata promote
apply dspapi prd --wave general`** (Phase 5) is a narrower, purely
mechanical version of that same edit: it does not read `canary` at all —
waves carry no ordering field of their own (only `(ring, order)` is
sortable; a `wave` name is just a disambiguating label, "no inference
from name or file order"), so one wave cannot generically be "the"
designated predecessor of a sibling at the same `(ring, order)`. It
instead copies forward from the preceding **order** (`qas`, `1.2.0` here)
— the one source every wave at `prd` can unambiguously agree shares a
value with. Reaching `canary`'s own `1.1.0` specifically is still exactly
one hand-edit away, same as before `apply` existed; `apply` automates the
*other* real promotion this pipeline needs just as often — catching a
ring up to what already proved out one order back — without inventing a
second, wave-level ordering concept this schema deliberately does not
have. Zero deployment documents change either way, and it applies to
every tenant referencing that one file at once. Adding customer #251 to
`general` is one new deployment document pointing at the existing
`dspapi-prd` version document — nothing else changes.

## Implementation Plan

Phased, one focused change at a time, full check suite
(`mypy src/strata`, `ruff check src/ tests/`, `pytest -q`, `lint-imports`)
clean before moving to the next phase — same discipline as every other
feature in this repo. **Phases 1-3 are the MVP** — ship-visibility, no
enforcement, matches this whole design's own "ship the smallest thing
first" stance. Phases 4-6 are real, valuable, but not required for the
MVP to be useful.

Depends on [version-lifecycle.md](version-lifecycle.md)'s own Phase 1
(`Version.spec.workspace` + its exclusivity check) landing first —
everything here assumes that field and check already exist, since the
blast-radius guarantee matters regardless of whether a document carries
ring/order/wave tags at all. **✅ Shipped 2026-10-06** — unblocked, this
doc's own Phase 1 can start.

### Phase 1 (MVP) — ✅ DONE (2026-10-06) — `VersionPromotionModel` schema

- Add `VersionSpecModel.promotion: VersionPromotionModel | None` —
  `{ring: str | None, order: int | None, wave: str | None}`.
- Phase 1 validators: `wave` without `ring` rejected; `ring` without
  `order` rejected (see "Validating that the right 'promotions' get
  created" above for the exact rules).
- No CLI changes yet — schema only.
- **Done when**: new model tests cover both rejections, full check suite
  clean.
- **Shipped as**: `VersionPromotionModel` added to
  [version_model.py](../../src/strata/models/version_model.py) — `ring`/
  `wave` are `str | None` (`min_length=1`, so an empty string is rejected
  the same way an unset field is represented as absent, not `""`),
  `order: int | None` deliberately left unbounded (no `ge=`/`le=` —
  nothing in the design calls for one, and inventing a range would be
  guessing a constraint nobody asked for). Two `model_validator(mode=
  "after")` methods enforce the two Phase 1 rules independently (wave
  requires ring; ring requires order), confirmed by a dedicated test that
  `wave` set alongside `order` but no `ring` still fails on the
  wave-requires-ring rule specifically — the two validators don't mask
  each other. `VersionSpecModel.promotion` defaults to `None` and is
  never constructed with an empty placeholder — a single-app product's
  `kind: version` document omits it entirely (confirmed by a test
  asserting `model.spec.promotion is None` on a plain document, not
  `VersionPromotionModel()`). Also corrected a stale note in this
  model's own module docstring, left over from ADR-0019's original
  "`spec.ring` dropped, ring/promotion attaches *around* this model" plan
  — that plan changed (this revision attaches ring *inside* the model,
  as `spec.promotion.ring`), so the docstring now says so instead of
  contradicting the code next to it. Tests:
  [test_models_version.py](../../tests/strata/models/test_models_version.py)
  (8 new — both rejections, the independence check, the omitted-by-
  default case, extra-field rejection, empty-string rejection). Full
  check suite clean, 2107 tests (8 new; the much larger jump from 2050 is
  unrelated parallel work already in this workspace, not this phase).

### Phase 2 (MVP) — ✅ DONE (2026-10-06) — `build_promotion_view()` + Phase 2 validation

- New function (new module, e.g. `strata/controllers/promotion_controller.py`
  to match `graph_controller.py`'s precedent) building `PromotionView` from
  every `Version` document sharing a workspace.
- Implements the three Phase 2 checks: ring↔order bijection,
  `(ring, order, wave)` uniqueness, no mixing waved/waveless at one slot.
- Implements the orphan-ring/wave warning (no `Deployment` references it).
- Wired into `strata validate`'s existing Phase 2 pass, same place
  `check_version_pins()` already runs.
- **Done when**: unit tests cover every rule in "Validating that the right
  'promotions' get created" above, both the pass and the fail case for
  each; `strata validate` surfaces a real violation end-to-end against a
  small fixture solution.
- **Shipped as**: `PromotionWaveView`/`PromotionRingView`/`PromotionView`
  (plain pydantic `BaseModel`, not `PlatformBaseModel` — an internal,
  computed aggregate strata builds, never user-authored) plus
  `build_promotion_view(workspace, index, resolved=None)` and
  `validate_promotions(index, resolved)` in new
  [promotion_controller.py](../../src/strata/controllers/promotion_controller.py).
  Only `Version` documents with BOTH a *declared* `spec.workspace` and a
  tagged `spec.promotion.ring` participate — an untagged document (worked
  example A) or one with no declared workspace is excluded entirely ("skip
  rather than guess", not inferred the way `_check_version_workspace()`
  infers one for its own, narrower purpose). All three Phase 2 checks run
  as designed: ring↔order bijection (checked both directions — a ring
  claiming >1 order, and an order claimed by >1 ring — since a typo could
  break the bijection from either side), `(ring, order, wave)` uniqueness
  (a duplicate keeps its first-seen occupant and reports an error, the
  same degrade-gracefully pattern a dangling reference uses), and no
  mixing a waveless document with waved ones at one slot. The orphan-ring
  warning threads `resolve_deployment_chains()`'s own resolved-deployments
  dict through (same reason `_check_version_workspace()` does, so a
  deployment that only gets `spec.version` through `extends` isn't
  misreported as a non-referrer), falling back to each deployment's raw
  document when `resolved` is `None` (for a future standalone caller that
  has not run extends-resolution). Wired into
  [solution_context.py](../../src/strata/controllers/solution_context.py)'s
  `resolve()` as a 5th pass, right after `check_version_pins()`, reusing
  the same `resolved_deployments` already computed for pass 3. `validate_
  promotions()` discovers every distinct, declared workspace among tagged
  documents and runs the builder once per workspace, merging findings —
  so `strata validate` catches a violation without the caller needing to
  know workspace names in advance. Tests:
  [test_promotion_controller.py](../../tests/strata/controllers/test_promotion_controller.py)
  (10 new — both the untagged/no-workspace exclusion cases, the full
  worked-example-B-shaped grouping/sorting shape, both bijection
  directions, both duplicate-slot cases (waveless and waved), the mixed-
  waveless case, and both orphan-ring cases (warned and not)) — every one
  exercises the real `strata validate` path (`open_solution(...).resolve()`),
  not the builder in isolation, matching this phase's own "end-to-end
  against a small fixture solution" done-when criterion. Full check suite
  clean, 2117 tests (10 new).

### Phase 3 (MVP) — ✅ DONE (2026-10-06) — `strata promote status <workspace>`

- New `promote` command group, `status` subcommand.
- Renders `PromotionView` as the table shown throughout this doc —
  `ring`/`order`/`wave`/`version`/`status` columns, `← behind` marker
  computed by comparing `order`s.
- `--output json` envelope, matching every other command's shape.
- **Done when**: CLI tests cover the worked-example-B-shaped fixture
  (multi-ring, wave divergence) and the worked-example-A-shaped fixture
  (no tags at all — empty/trivial output, not an error).
- **Shipped as**: `strata promote status WORKSPACE` in new
  [promote_command.py](../../src/strata/commands/promote_command.py), read-
  only and Phase 1 (schema) only — same posture as `graph`/`path`, never
  `.require_valid()` — and **always exits 0** (a promotion finding is
  shown, not failed on; enforcement already lives in `strata validate`'s
  Phase 2 wiring, shipped last phase). Two real design decisions made
  during implementation, beyond this doc's own illustrative table:
  - **A row is per-*pin*, not per-document** — the table gained a `target`
    column (`category/name`, e.g. `images/dspapi`) beyond the doc's
    illustrative 5-column example. Necessary, not a scope-creep: the one
    real production `kind: version` document this whole model was
    designed around (`version_model.py`'s own docstring) carries **14**
    pins at once — collapsing a multi-pin document into one row would
    have to silently pick one pin's value and discard the rest. A wave
    with zero pins declared yet still gets one placeholder row (`target`/
    `version`/`status` all `-`), so a ring doesn't silently vanish from
    the table before anyone's pinned anything to it.
  - **`← behind` is computed within one `(ring, order)` group, per pin
    target, comparing sibling waves against each other** — not "across
    orders" as the doc's own prose literally said. Worked example B's own
    "canary runs ahead of general" only makes sense this way: both share
    `(ring=prd, order=3)`; comparing across different orders instead
    would not single out `general` the way the example shows. Uses
    `packaging.version.Version` (already a real dependency, previously
    only used for `SpecifierSet` assertions) to parse and compare;
    computed **only** when a target's value is declared by more than one
    sibling wave **and every one of those values parses as a `Version`**
    — a git ref, commit SHA, or floating tag cannot be safely ordered,
    and this doc's own "skip rather than guess" rule applies here exactly
    as everywhere else: no marker, not a guessed one, when values aren't
    comparable.
  Tests:
  [test_promotion_controller.py](../../tests/strata/controllers/test_promotion_controller.py)
  (5 new, for the new `build_status_rows()` — the placeholder row, one
  row per pin on a multi-pin document, the behind marker firing/not-
  firing, and the non-semver skip) and
  [test_commands_promote.py](../../tests/strata/commands/test_commands_promote.py)
  (7 new — both worked-example shapes, JSON output, a tagged ring with no
  pins yet, and a real Phase 2 violation shown without failing the
  command). Full check suite clean, 2129 tests (12 new).

### Phase 4 — ✅ DONE (2026-10-06) — `resolved.yaml`'s new `promotion` section

- `build_controller.write_resolved_manifest()` gains an optional
  `promotion` key, populated only when the deployment's `spec.version`
  resolved to a document carrying `spec.promotion`.
- Update `docs/design/build-time-value-categories.md`'s documented shape
  to match.
- **Done when**: a `build run` against a tagged `Version` document
  produces the new section with the right values; untagged documents
  produce no section at all (not a null/empty one).
- **Shipped as**: `ResolvedWorkspaceGraph` (`strata.integrations.
  resolved_context`) gains a new `version: VersionModel | None` field —
  `build_controller.build_run()` already computed this value
  (`resolve_version(context, deployment)`, already reflecting `--pin`) for
  the remote-pin overlay, so Phase 4 only had to thread the same value one
  step further rather than re-resolve it. New `_dump_promotion(graph)` in
  [build_controller.py](../../src/strata/controllers/build_controller.py)
  reads `graph.version.spec.promotion` directly — **not**
  `build_promotion_view()` — a deliberate choice: this section answers
  "did *my* deployment land where *I* meant it to" (a human-intent check
  against the one document this build actually used), a different
  question than Phase 2's "is every `Version` document in the workspace
  internally consistent with every other one" (`validate_promotions()`,
  already wired into `strata validate`) — no cross-document grouping is
  needed to answer the deployment-scoped question, so none is done.
  `workspace` in the written section comes from the **deployment's own**
  `spec.workspace`, not `version.spec.workspace` — this section is
  deployment-scoped by design (the doc's own "did *my* deployment..."
  framing), and the deployment's own declared value is the one a human
  actually wrote down. `wave` is omitted entirely (not `null`) when unset,
  matching every other omitted-rather-than-null field already in this
  file (`_dump_refs()`'s `description`/`value`). Tests: 4 new in
  [test_build_controller.py](../../tests/strata/controllers/test_build_controller.py)
  (a tagged document with a wave, one without a wave confirming omission,
  an untagged document confirming no section at all, and a deployment
  with no `spec.version` at all confirming the same). Manually smoke-
  tested against the real `config/` dogfood solution (`prd-deployment`,
  whose real `versions/prd.yaml` is untagged) — confirmed no `promotion`
  key appears, as expected. Full check suite clean, 2133 tests (4 new).

### Phase 5 — ✅ DONE (2026-10-06) — `strata promote apply <workspace> <ring> [--wave W]`

- Implements the settled mechanics: find target ring's `order`, find the
  preceding `order` in the same workspace, copy every common pin key,
  reset `status`/`reviewed`, require `--wave` when the ring has more than
  one.
- Reuses Phase 2's `build_promotion_view()` to find both rings' documents
  — no separate lookup logic.
- **Done when**: CLI tests cover the single-wave case, the multi-wave
  case requiring `--wave`, and the multi-wave case erroring without it.
- **Shipped as**: `apply_promotion(workspace, ring, index, *, wave=None)`
  in [promotion_controller.py](../../src/strata/controllers/promotion_controller.py),
  exposed as `strata promote apply WORKSPACE RING [--wave W]` in
  [promote_command.py](../../src/strata/commands/promote_command.py).
  **A real design ambiguity surfaced during implementation, resolved and
  documented in place** (Worked Example B's own prose above): "the
  preceding order" and "canary's already-proven sibling value" are two
  different sources once a ring is split, and the doc's own illustrative
  narrative ("promoting `general` to `1.1.0`", `canary`'s value) had
  quietly assumed the latter while the "Mechanics, settled" bullets
  describe the former. Resolved in favour of **the preceding order,
  uniformly, regardless of source wave** — the only one of the two that
  is generically well-defined: `wave` carries no ordering field at all
  (Phase 1's own "no inference from name or file order" rule), so nothing
  in the schema could tell `apply` that `canary` precedes `general`
  specifically, as opposed to some third/fourth wave sharing that same
  `(ring, order)`. Worked Example B's prose corrected to match (now reads
  the actual shipped number, `1.2.0`, with the reasoning spelled out
  inline) rather than leave code and doc silently disagreeing. Mechanics
  precisely as settled otherwise: **every common pin key** (the
  intersection of both documents' declared keys — never creates a key
  only the source has; that remains `version update`'s job), **always**
  resets `status` to `current` and `reviewed` to today on the copied
  key (dropping any old `reason` implicitly — a fresh promotion retires
  the prior hold's rationale), **never** touches `available` (orthogonal
  upstream-tracking bookkeeping the doc never mentions resetting). Source
  selection is asymmetric by design: the **target** wave must match
  `--wave` strictly (required when the ring has more than one, rejected
  if given but not found); the **source** ring only needs a matching wave
  name when *it* is itself split — a single-occupant preceding ring
  (the common case, e.g. waveless `qas` feeding both of `prd`'s waves)
  applies regardless of the target's own wave name. **One safety margin
  beyond the doc's literal scope**: refuses outright (`UsageError`) when
  `build_promotion_view()` itself reports a Phase 2 error for the
  workspace — `promote status` only *displays* a possibly-ambiguous view,
  but `apply` *writes a file*, and silently resolving an ambiguous slot
  to its first-seen occupant (the view's own degrade-gracefully behaviour)
  is the wrong default for a mutating command. Zero common pin keys is
  a real, reported outcome (`copied: []`), not an error — nothing to
  promote is a legitimate state, not a failure. Reuses `version_controller.
  py`'s exact `ruamel.yaml` round-trip technique (a fresh, intentionally
  duplicated 6-line `_round_trip_yaml()` rather than a cross-module import
  of a private helper) for the same reason: comments, key ordering and
  quote style on every *untouched* pin in the file must survive. Manually
  smoke-tested end-to-end against a scratch solution modelled on Worked
  Example B before writing automated tests — confirmed the exact
  mechanics above (comments/quotes preserved on an untouched sibling
  `charts` pin, `reason` dropped, `reviewed` set to the real run date) and
  every error path (missing `--wave`, unknown wave, unknown ring, no
  preceding order). Tests: 10 new in
  [test_promotion_controller.py](../../tests/strata/controllers/test_promotion_controller.py)
  and 8 new in
  [test_commands_promote.py](../../tests/strata/commands/test_commands_promote.py).
  Full check suite clean, 2151 tests (18 new).

### Phase 6 — ✅ DONE (2026-10-06) — `strata promote view <workspace> [--output <path>]`

- Separate command, reuses Phase 2's builder, emits the full structured
  `PromotionView` (YAML/JSON), stdout by default.
- Explicitly **not** wired into `audit_run.py`/`_manifest.json` — no
  changes to the audit trail at all in this phase.
- **Done when**: output round-trips (what `promote view` emits parses back
  into a `PromotionView`), and a grep of `audit_run.py` confirms it's
  never called from there.
- **Shipped as**: `strata promote view WORKSPACE [--output-path PATH]` in
  [promote_command.py](../../src/strata/commands/promote_command.py).
  **One real naming conflict surfaced and resolved during implementation**:
  the doc's own literal invocation (`strata promote view dspapi --output
  promotion-view.yaml`) reuses `--output` as a *file path* — but every
  other command in this CLI (including `promote status`/`apply`, already
  shipped) already gives `-o/--output` a different, established meaning
  (`console`/`json` rendering, `STRATA_OUTPUT`-env-var-aware). Reusing the
  same flag name for a path here would have silently broken that
  convention for this one command and surprised anyone used to it
  elsewhere. Resolved by keeping `-o/--output {console,json}` exactly as
  everywhere else, and introducing a deliberately differently-named
  `--output-path PATH` for "write here instead of stdout" — functionally
  identical to the doc's own intent, just not spelled the way its one-line
  example literally showed it. `-o console` (default) renders the bare
  `PromotionView` as YAML directly to stdout (matching the doc's own
  `promotion-view.yaml` naming) — not wrapped in the usual header/footer
  content, though `command_run()`'s own chrome still prints around it the
  same as any other console-mode command (use `--quiet`, already a
  standard flag on every command, for byte-clean output — no new
  mechanism needed). `-o json` puts the view in the standard envelope's
  `data` field, so `STRATA_OUTPUT=json` (the documented CI convention)
  keeps working here exactly like everywhere else. `--output-path` writes
  the *bare* rendered content (YAML or JSON, matching `-o`) to a file
  instead of stdout — and when given, stdout's own `data`/output becomes
  a small `{written_to, rings}` confirmation rather than a second,
  duplicate copy of what could be a large payload (one real document per
  ring/wave, each one a full `VersionModel`). Confirmed exit-code parity
  with `status`: always `0` — a promotion inconsistency (Phase 2) is
  surfaced via the normal diagnostics block, never failed on; enforcement
  stays `strata validate`'s job. Manually smoke-tested all four
  console/quiet/json/`--output-path` combinations against a scratch
  Worked-Example-B-shaped solution before writing automated tests —
  confirmed the YAML/JSON both round-trip through `PromotionView.
  model_validate()`. Confirmed by grep: `audit_run.py` contains zero
  references to `promotion_controller`/`promote_command`/
  `build_promotion_view` (also asserted as a test, not just a one-off
  check). Tests: 8 new in
  [test_commands_promote.py](../../tests/strata/commands/test_commands_promote.py)
  (console/json round-trip, the untagged-workspace empty-view case,
  both `--output-path` combinations, the promotion-violation-shown-but-
  not-failed case, outside-a-solution, and the audit-trail-isolation
  grep). Full check suite clean, 2159 tests (8 new).

  **The Implementation Plan's all six phases are now shipped** — the MVP
  (Phases 1-3) plus the three "valuable but not blocking" phases (4-6).

## Related Decisions

- [ADR-0019](../decisions/0019-version-pinning.md) — dropped `spec.ring`
  from `kind: version` the first time (it duplicated `meta.name` in the
  one real document) and named ring/promotion as the planned follow-up
  this doc delivers; this revision reintroduces `ring` deliberately
  differently — as one tag among three (`ring`/`order`/`wave`), not a
  resurrected duplicate of identity.
- [path-conventions.md](path-conventions.md) / `semantic_checks.py`'s
  `_check_deployment_layers()` — the "declare it, then validate it matches
  reality" pattern `Version.spec.workspace` reuses.
- [v2-schema-overview.md](../design/v2-schema-overview.md) — `deployment`
  row's "Remaining Work" note (`gates`/`promotion` remain undesigned) is
  the gap this doc closes; update that table once this ships.
- [version-lifecycle.md](version-lifecycle.md) — `version new`/`update`/
  `set`, `--pin`, the file-location lookup command, CI/CD automation, and
  the audit-provenance gap this doc's "version column" problem depends on.
  All of that content used to live in this doc; split out because none of
  it is actually promotion-specific — it's equally true for a product with
  no ring/order/wave at all.

## Remaining Work / Open Questions

- **Closed, accepted**: no second real rollout exists to validate
  `ring`/`order`/`wave`'s shape against yet. Not treated as blocking —
  proceeding on the same informed-but-unproven basis as the rest of this
  doc, not waiting on it.
- `strata promote apply`'s mechanics are settled and ✅ shipped 2026-10-06
  (Phase 5) — including a resolved design ambiguity (preceding-order vs.
  sibling-wave source; see Phase 5's own "Shipped as" entry).
- **The "version" column is prospective, not actual** — depends on
  [version-lifecycle.md](version-lifecycle.md)'s audit-manifest wiring to
  fix properly; currently just a known, accepted limitation. Don't let
  this get silently dropped again in a future consolidation pass.
- Ring-to-ring and wave-to-wave sequencing enforcement is not designed at
  all — not even as an inert/opt-in field. Deliberately deferred; revisit
  only if a real rollout needs the gate, and only then decide what "a wave
  succeeded" even means.
- `build_promotion_view()` and its validation rules (ring↔order bijection,
  `(ring, order, wave)` uniqueness, no mixing waved/waveless at one slot,
  the orphan-ring warning) — ✅ shipped 2026-10-06 (Phase 2).
- `resolved.yaml`'s new optional `promotion` section — ✅ shipped
  2026-10-06 (Phase 4). `docs/design/build-time-value-categories.md`'s
  documented shape updated to match.
- `strata promote view <workspace> [--output <path>]` — ✅ shipped
  2026-10-06 (Phase 6) as `strata promote view WORKSPACE [--output-path
  PATH]` (see Phase 6's own "Shipped as" entry for the `--output` naming
  conflict this resolved) — a separate command from `promote status`,
  deliberately not wired into `audit_run.py`/`_manifest.json` so its
  boundary from the audit trail stays unambiguous (confirmed by grep,
  also asserted as a test).

## Changelog

- 2026-10-06: Removed `kind: promotion` and `Deployment.spec.promotion`
  entirely, following a critical review that found the dedicated kind's
  only remaining job — once `Version.spec.workspace` existed — was
  producing a `promote status` table equally derivable by tagging
  `Version` documents directly (`ring`/`order`/`wave`) and keeping
  `Deployment` on the one resolution mechanism ADR-0019 already built.
  Removed the three-mode mutual-exclusivity validator and the ring/wave
  conditional-reference chain along with it — replaced by one new,
  simpler check (`(ring, order)` unique per workspace unless `wave`
  differs). Re-opened the "version column is prospective, not actual"
  finding that had been silently dropped in an earlier consolidation.
  Split everything that wasn't actually promotion-specific (`version new`/
  `update`/`set`, `--pin`, the file-lookup command, CI/CD/Dependabot
  automation, the audit-provenance gap) into
  [version-lifecycle.md](version-lifecycle.md). Updated the Overview with
  real evidence: `cfg-int-deployment` is already live and growing toward
  ~250 customers × 4 rings — the motivating need is no longer purely
  hypothetical, even though this specific schema remains unproven against
  a second real rollout.
- 2026-10-06: Fixed an inconsistency between this doc's two examples (one
  nested `ring`/`order`/`wave` under `spec.promotion`, the other left them
  flat on `spec` — an artifact of in-progress exploration, not a real
  design split). Standardized on the nested shape
  (`VersionPromotionModel`, field `spec.promotion`) — groups the three
  related fields as one sub-model, matching how `DeploymentLayersModel`/
  `DeploymentLockingModel` already group related optional fields
  elsewhere, and keeps "promotion" alive in the vocabulary as a named
  sub-concept even though the dedicated kind is gone. Updated Worked
  Example B and all surrounding prose to match.
- 2026-10-06: Settled `strata promote apply`'s mechanics — copy every
  common pin key (not just differing ones), reset `status`/`reviewed` on
  the target (a source ring's hold/rationale doesn't automatically still
  apply at the new ring), and require an explicit `--wave` whenever the
  target ring has more than one (no guessing which wave to touch). Closed
  the "no second real rollout" item as an accepted, non-blocking
  limitation rather than an open question.
- 2026-10-06: Designed the validation layer properly (Phase 1 intra-
  document rules plus Phase 2 cross-document ones — ring↔order bijection,
  `(ring, order, wave)` uniqueness, no mixing waved/waveless at one slot,
  orphan-ring as a warning). Made explicit that all of it — plus `promote
  status`/`apply` — needs an in-memory `PromotionView` strata builds
  internally; named it and gave it a concrete shape, and clarified why
  this isn't a reintroduction of `kind: promotion`'s problems (nothing
  persisted, nothing for a human to keep in sync, one pure function reused
  by three callers instead of logic scattered across them). Designed
  surfacing the resolved view back to the user as two deliberately
  separate things at two different scopes: a new optional `promotion`
  section in the existing, already-audited `resolved.yaml`
  (deployment-scoped, answers "did my deployment land where I meant it
  to") and an optional, separate `promotion-view.yaml` audit-trail
  snapshot (workspace-scoped, belongs in `deploy run`'s audit trail, not
  `build run` — building one deployment shouldn't have a side effect
  touching every other tenant's). Deliberately named the latter
  `-view.yaml`, not `promotions.yaml`, and ruled out ever committing it to
  the repo — both guard against it being mistaken for a resurrected source
  document.
- 2026-10-06: Reconsidered the workspace-wide snapshot — moved it out of
  `deploy run`'s audit trail entirely and behind its own command,
  `strata promote view <workspace> [--output <path>]`, deliberately not
  wired into `audit_run.py`/`_manifest.json`. Being a separate,
  explicitly-invoked verb (not a flag on `promote status`, not a side
  effect of any build/deploy command) makes the boundary from the audit
  trail unambiguous: a point-in-time export someone asks for on purpose,
  not a compliance artifact strata generates on its own initiative.
- 2026-10-06: Added a full Implementation Plan — six phases, 1-3 marked as
  the MVP (schema, `build_promotion_view()` + validation, `promote
  status`), 4-6 as valuable-but-not-blocking (`resolved.yaml` section,
  `promote apply`, `promote view`). Explicit dependency on
  [version-lifecycle.md](version-lifecycle.md)'s own Phase 1
  (`Version.spec.workspace` + exclusivity) landing first.
- 2026-10-06: [version-lifecycle.md](version-lifecycle.md)'s Phase 1
  shipped (`VersionSpecModel.workspace` + `_check_version_workspace()`) —
  this doc's own Phase 1 (`VersionPromotionModel` schema) is now
  unblocked and can start.
- 2026-10-06: **Shipped Phase 1** — `VersionPromotionModel`
  (`ring`/`order`/`wave`, both Phase 1 intra-document rules enforced).
  Corrected a stale cross-reference in `version_model.py`'s own module
  docstring (ADR-0019's original "ring attaches around this model" plan,
  superseded by this doc's critical-review revision that attaches it
  *inside* the model instead) rather than leaving code and prose
  disagreeing. 8 new tests; full check suite clean. Phase 2
  (`build_promotion_view()` + cross-document validation) is next.
- 2026-10-06: **Shipped Phase 2** — `build_promotion_view()` +
  `validate_promotions()` in new `promotion_controller.py`, implementing
  all three Phase 2 checks (ring↔order bijection, `(ring, order, wave)`
  uniqueness, no mixing waved/waveless at one slot) plus the orphan-ring
  warning, wired into `strata validate`'s `SolutionContext.resolve()` as a
  5th pass alongside `check_version_pins()`. `PromotionWaveView`/
  `PromotionRingView`/`PromotionView` built as plain pydantic models, not
  `PlatformBaseModel` — matching this doc's own "the user never authors
  this" design note. 10 new tests, every one end-to-end through
  `open_solution(...).resolve()` rather than the builder in isolation.
  Full check suite clean, 2117 tests. Phase 3 (`strata promote status`)
  is next.
- 2026-10-06: **Shipped Phase 3** — `strata promote status <workspace>`,
  read-only, always exits 0 (enforcement is `strata validate`'s job).
  Two decisions made beyond the doc's own illustrative table: a row is
  per-*pin* (new `target` column), since the one real production
  `kind: version` document this schema was designed around pins 14
  targets at once; and `← behind` compares sibling waves within one
  `(ring, order)` group (not "across orders"), using
  `packaging.version.Version`, only when every sibling's value actually
  parses as one. 12 new tests. Full check suite clean, 2129 tests. Phase
  4 (`resolved.yaml`'s `promotion` section) is next.
- 2026-10-06: **Shipped Phase 4** — `resolved.yaml`'s optional `promotion`
  section, populated from `ResolvedWorkspaceGraph`'s new `version` field
  (the same already-pin-aware value `build_run()` computed for the
  `remotes` overlay, simply threaded one step further). Reads
  `graph.version.spec.promotion` directly, not `build_promotion_view()`
  — a deliberate scope split: this section answers a deployment-scoped,
  human-intent question ("did *my* deployment land where I meant it to"),
  not the cross-document consistency question Phase 2 already owns.
  `workspace` in the section is the deployment's own `spec.workspace`, not
  the `Version` document's. 4 new tests, plus a manual smoke test against
  the real `config/` dogfood solution (untagged — confirmed no section
  written). Full check suite clean, 2133 tests. Phase 5
  (`strata promote apply`) is next.
- 2026-10-06: **Shipped Phase 5** — `strata promote apply <workspace>
  <ring> [--wave W]`. Surfaced and resolved a real ambiguity in this very
  doc: Worked Example B's own prose had quietly assumed `general` would
  promote from its sibling `canary` wave's already-proven value, while
  the "Mechanics, settled" bullets describe copying from the preceding
  *order* instead — two different sources once a ring is split. Resolved
  in favour of the preceding order, uniformly: `wave` carries no ordering
  field (Phase 1's own "no inference from name or file order" rule), so
  nothing could tell `apply` that `canary` precedes `general` specifically
  rather than some third sibling. Corrected the worked example's own
  prose to match the shipped number rather than leave doc and code
  disagreeing. One safety margin added beyond the doc's literal scope:
  refuses outright when the workspace has an unresolved Phase 2 error —
  `apply` writes a file, so silently resolving an ambiguous slot to its
  first-seen occupant (`promote status`'s own acceptable degrade-
  gracefully behaviour) would be the wrong default for a mutation. 18 new
  tests, plus a manual end-to-end smoke test confirming comments/quotes
  survive on an untouched sibling pin. Full check suite clean, 2151
  tests. Phase 6 (`strata promote view`) is next.
- 2026-10-06: **Shipped Phase 6** — `strata promote view WORKSPACE
  [--output-path PATH]`, the last of all six phases. Surfaced and
  resolved a second real design conflict in this doc: its own literal
  invocation example reused `--output` as a file-path flag, but every
  other command in this CLI (including this doc's own `status`/`apply`)
  already gives `-o/--output` an established, different meaning
  (console/json rendering, `STRATA_OUTPUT`-env-var-aware). Kept the
  established flag exactly as-is everywhere, and added a deliberately
  differently-named `--output-path PATH` for "write here instead of
  stdout" instead — same functional intent, no silent breakage of an
  existing, documented convention. `-o console` (default) renders the
  bare `PromotionView` as YAML, matching the doc's own `promotion-
  view.yaml` naming; `-o json` puts it in the standard envelope, so
  `STRATA_OUTPUT=json` keeps working here too; `--output-path` writes the
  same bare content to a file, with stdout then carrying only a small
  `{written_to, rings}` confirmation rather than a duplicate copy of a
  potentially large payload. Confirmed both the YAML and JSON forms
  round-trip through `PromotionView.model_validate()`, and confirmed (by
  grep, and as a real test) that `audit_run.py` references nothing from
  this feature at all. 8 new tests. Full check suite clean, 2159 tests.
  **All six phases of this doc's Implementation Plan are now shipped.**
- 2026-10-06: **Code review of all six shipped phases.** One real bug
  found and fixed: `apply_promotion()`'s `_write_copied_pins()` wrote the
  surgical `ruamel.yaml` edit without re-parsing the result through
  `VersionModel` afterward — the exact safety net
  `version_controller.set_version_pin()` already established for the
  same category of edit ("a guarantee the write produced a still-valid
  document, not a second read the caller has to remember to do").
  `apply_promotion()`'s own docstring already claimed to reuse that
  module's technique "for the same reason" but had silently dropped this
  half of it; added the missing re-validation call. Two real test-
  coverage gaps found and closed: `_select_source_wave()`'s own strict
  branch (the *preceding* ring itself split, with none of its waves
  matching the target's) and `_select_target_wave()`'s single-occupant
  mismatch branch (a `--wave` given that doesn't match a ring's one,
  waveless-or-not, document) were both real, reachable code paths with
  zero test coverage — both now covered. Everything else reviewed clean:
  traced every `order`/`wave` truthy-vs-`is not None` check (`wave`'s
  schema-guaranteed non-emptiness makes both forms equivalent everywhere
  they're used, confirmed rather than assumed), confirmed `deploy_
  controller.py`'s own, separate `build_resolved_workspace_graph()` call
  site correctly has no `version` to thread at all (it never resolves one
  and never writes `resolved.yaml` — Phase 4's gap was build-run-scoped
  by design, not a second call site silently missed), and confirmed
  `promote view --output-path`'s overwrite-without-confirmation behaviour
  is correct as designed (a regenerate-on-demand export, not an authored
  document — `scaffold_version()`'s own "refuse to overwrite" precedent
  does not apply here, since repeat runs into the same path are the
  intended CI use case, not an accident to guard against). 2 new tests.
  Full check suite clean, 2161 tests.