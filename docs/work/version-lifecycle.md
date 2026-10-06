# Version Lifecycle — Work

- Status: draft
- Last updated: 2026-10-06

## Overview

Split out of [promotion.md](promotion.md) after a critical review found
most of its content wasn't actually promotion-specific — `version new`/
`update`/`set`, the `--pin` flag, the file-location lookup command, CI/CD
automation (release trains and third-party dependency bumps), and the
audit-provenance gap are all exactly as true for a single app with no
ring/order/wave at all as they are for a multi-ring rollout. This doc is
the home for `kind: version`'s lifecycle and tooling generally; `promotion.md`
is just the ring/order/wave tagging and the `promote status`/`apply`
commands built on top of it.

Real, near-term context: `cfg-int-deployment` already has real deployments
and is growing toward ~250 customers × 4 rings — it's why "release train"
below is grounded in how that repo actually operates, not a hypothetical.

## Current Design / Progress

### `kind: version` recap

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: dspapi-prd
spec:
  workspace: dspapi
  pins:
    images:
      dspapi: "1.1.0"
```

`spec.workspace` (Phase 2 validated against every real referrer) bounds the
blast radius of editing this file — see [promotion.md](promotion.md)'s own
section on it, unchanged here. `spec.pins`'s shape (`status`/`reason`/
`available`/`reviewed` per pin) is ADR-0019's, unchanged.

### Hand-editing is the whole interface, and that's fine for anyone, not just a design intent for humans

`kind: version` is *"the single file an operator edits to perform an
upgrade"* (ADR-0019) — hand-editing a pin's `version`/`status`/`reason`
directly is the baseline interface, and there's no separate "for humans
only" rule layered on top of it: if a human wants to use `strata version
set` (below) instead of opening an editor, that's fine too. The actual
thing worth protecting isn't *who* calls a mutation path, it's that the
mutation path can't silently destroy rationale or defeat a deliberate
hold — `version set`'s guardrails (never touches `reason`/`reviewed`,
refuses to bump a `held`/`unverified` pin without `--force`) are what make
that safe for any caller, not a restriction on who's allowed to use it.

What's always been fine to build manages the file's *structure*, never a
pin's *value* on its own:

- **`strata version new <name> --workspace <ws> [--ring R --order N --wave W]`**
  — scaffolds a new `kind: version` document (nothing does this today —
  confirmed, no `kind: version` template exists under
  `src/strata/templates/`, unlike every other kind `sln init` scaffolds).
  A `--from <existing-version>` option would clone an existing document's
  pin *keys* (not values/reasons) as a starting point.
- **`strata version update <name>`** — reconciles `spec.pins`' *keys*
  against the solution's real inventory of pinnable targets (every
  `ModuleServiceModel.image`/`SourceModel.chart_version`/
  `SolutionRemoteModel` with `fetch: strata`/`ArtifactModel`) — the same
  census `check_version_pins()` already runs for existence-checking, just
  inverted: report targets with **no** pin entry yet (candidates to add),
  and pins referencing a target that **no longer exists** (ADR-0019's own
  words: *"a stale pin for a deleted module is the likeliest real
  failure"*) as candidates to remove. A proposal, not an auto-apply — the
  operator still decides and edits by hand.

**The one hard rule `update` must never break**: it may add or remove pin
*keys*; it must never touch an existing pin's `version`/`status`/`reason`/
`reviewed`. Same principle ADR-0019 already established for comments-vs-
schema (a machine rewrite must not destroy rationale) — `update` is new
surface for the same failure mode, just at the key level instead of the
value level.

**Artifacts vs. version pins — already resolved, confirmed in code**:
`value_controller.resolve_artifact_field()` checks the `artifacts` pin
**first**, falling back to the artifact's own declared `image_tag` only
when unset — the pin wins, unconditionally, by construction, already.
Where this bites `update`: the first time it adds a pin key for a
previously-unpinned target, it must seed that key with the target's
*current* effective value (so adding the key changes nothing), and must
**never** resync that value on a later run — that would silently convert a
deliberate override back into a mirror of upstream.

### `strata version set` — a narrow mutation primitive, real work underneath

```
strata version set <name> <category> <target> [<value>] [--available VALUE] [--force]
```

Earns its existence by doing real work an ad hoc edit (or a naive script)
would get wrong, not by being a convenience wrapper:

- **Resolution** reuses `strata path get`'s own logic internally — zero
  duplicated discovery code, even within strata itself (see below).
- **Write is surgical and comment-preserving — `ruamel.yaml`'s round-trip
  mode, not a hand-rolled regex/line replace.** Settled: a regex has to
  handle every valid YAML shape the pin's own scalar can take (quoted vs.
  unquoted, the shorthand-string vs. structured-dict pin forms) correctly,
  or it silently corrupts something; a purpose-built round-trip parser
  already solves exactly this problem. Touches only the targeted pin's
  `version`/`available`, leaves every other byte of the file untouched
  (comments, ordering, unrelated pins). This is the one file in the whole
  system meant to carry meaningful hand-written annotations — every other
  machine-written artifact (`resolved.yaml`, `sbom.json`) is purely
  generated with no commentary expected, so the plain `yaml.safe_dump`
  those use elsewhere is fine there and nowhere else.
- **Never touches `reason` or `reviewed`** — `reviewed` specifically means
  *"a human last reviewed this"*; a bot (or a human running this instead
  of hand-editing) stamping it would misrepresent what happened. Same
  "touch these fields, never those" shape as `update`'s rule above, just
  inverted (update touches keys not values; `set` touches these specific
  values, never those other fields).
- **Refuses to change `version` on a `held`/`unverified` pin without
  `--force`** — those statuses exist specifically to signal "don't blindly
  bump this." Without the refusal, automation (or an unthinking human)
  could silently defeat a deliberate hold, the same failure class `update`
  is already protected against for pin *keys*. `--force` overrides the
  refusal but still doesn't touch `status`/`reason` — resolving the hold
  itself stays a separate, deliberate edit.
- **`--available`-only mode (no `value`)** is always allowed regardless of
  `status` — purely informational, never overrides anything.
- **No git anywhere in this command** — no clone, no commit, no branch, no
  PR. Its job ends at "the file on disk now has the right content";
  everything git/PR-shaped is the calling pipeline's job (see CI/CD below).

**Naming collision worth flagging on implementation**: `version` is
currently a single bare command (`strata version` prints the CLI's own
version, `cli.py`'s `version_command()`) — nesting `new`/`update`/`set`
under it means `version` becomes a Click *group*, and the no-subcommand
case needs to keep printing the CLI version (`invoke_without_command=True`
plus explicit handling), not silently break.

### `strata path` — a dedicated group, decided

Settled as its own top-level group, not nested under `sln` or `graph` —
with the bar that a dedicated group needs *more than one* real command to
justify existing, not just this one lookup:

- **`strata path get <kind> <name>`** — resolves `(kind, name)` to its
  file path using strata's own document index — the exact same resolution
  every other command already relies on, and the same resolution `version
  set` needs internally (one mechanism, not two, even inside strata).
  Output a bare path on stdout by default; `--output json` for
  `{kind, name, path, exists}`.
- **`strata path list <kind> [--workspace X]`** — the plural form: every
  matching document's name and path, not just one. Rounds out the group
  with a second, genuinely distinct command that's still squarely about
  paths (one lookup vs. an enumeration), not a stretch to justify the
  name.

Neither is version-specific — useful to any external system that needs to
locate a strata-managed file without reimplementing discovery (`.strata/`
exclusion, path conventions, remotes — all real, easy to get subtly wrong
outside strata).

**Considered and left out of this group**: a `show`/`get`-the-*content*
command (dump a document's resolved YAML/model, not its path) was raised
as a possible third sibling, but it doesn't fit `path`'s own name — showing
content isn't "a path" in any sense, plural or singular. Worth designing
separately if a real need for it shows up; not part of this group.

### CI/CD of apps — two different automation shapes, both ending in a PR

"An app's CI/CD creates a new version — how does that get in here?" splits
into two genuinely different scenarios, surfaced by asking specifically
about Dependabot — and this is the part of the design most directly
validated by `cfg-int-deployment`'s real, already-in-use release-train
workflow, not a hypothetical.

**Real Dependabot can't touch `kind: version` at all** — it only parses
file shapes it has a built-in ecosystem for (`Dockerfile`,
`docker-compose.yml`, `Chart.yaml`, Terraform, GitHub Actions, etc.); a
custom strata schema isn't one of them. Configuring `.github/dependabot.yml`
to watch `versions/` would do nothing — not misbehave, just be unable to
see it.

**1. Our own app's release train** — CI builds `dspapi:1.2.0` and already
knows the exact tag with total certainty; it's the publisher, not a
consumer, so there's no "is there something newer" question to poll for at
all. The ephemeral `--pin` flag (below) covers the common "just deploy
what I built" case with zero persisted mutation. Making that the new
*committed* default is `strata version set dspapi-dev images dspapi 1.2.0`
run by the pipeline, then ordinary git: commit, push a branch, open a PR —
strata never touches git.

**2. A third-party dependency has a new release upstream** (postgres,
caddy, a Helm chart, a Terraform provider) — the actual Dependabot-shaped
problem: periodic polling to detect drift we didn't cause. A custom bot
(not literal Dependabot, since it can't parse this schema) queries the
real upstream registry/repo index — pure mechanical polling, no AI — and
runs `strata version set <name> <category> <target> --available <latest>`
to record what's available, every time, regardless of `status`. Whether it
also proposes bumping `version` itself is a policy choice (auto-propose
patch/minor, always require a human for majors), expressed as a PR a human
still reviews — the model's own `VersionPinModel.available` docstring
already anticipated this: *"Refresh tooling may rewrite this field freely;
it must never rewrite `reason`"* — ADR-0019 designed for this bot before
this doc ever existed.

**Decided: strata itself does not build or ship this bot.** Real registry
polling means separate integration surface per ecosystem (Docker Hub,
GHCR, ACR, a Helm repo's `index.yaml`, the Terraform registry API, ...) —
maintenance burden with no proportional payoff when `strata path` +
`version set` already give any repo everything it needs to build its own
small script. Explicitly out of scope, not an open question to revisit.

**Division of labor, either scenario**: `strata path` (`get`/`list`) +
`version set` do everything document-shaped (find the file, mutate exactly one
field, safely) and stay inside strata, where the real domain logic already
lives and doesn't get duplicated. Clone/branch/commit/push/PR stay outside
strata entirely, done by whatever git/PR tooling fits the calling platform
(GitHub Actions, Azure DevOps, a shell script with `gh`) — not strata's
concern, and deliberately not forced into one shape across every CI
platform. At `cfg-int-deployment`'s real scale (~250 customers × 4 rings),
neither scenario needs a human-facing "set a pin's value" command invented
just for this — both are a bot (or a human, equally) editing a file and
opening a PR, reviewed like any other change.

### `--pin` — reviving ADR-0019's own design for the release train

A release train (a new build every two weeks, `cfg-int-deployment`'s real
cadence) needs *something* to mechanically say "use what was just built" —
not a judgment call, but it doesn't need a new command either. ADR-0019
already designed the right shape, and it was never built:

> *"a plain CLI flag on the future `build run`/`deploy run` — `strata
> deploy run app --pin prd` — resolving a name against `kind: version` and
> overriding `Deployment.spec.version` for that invocation only... trivially
> deprecatable."*

`strata deploy run dspapi-dev --pin dspapi-1.2.0`, run by CI every two
weeks, with **zero persisted mutation** — an ephemeral, per-invocation
override, not a write. Making an override *permanent* (committing a new
default into `Deployment.spec.version`, or editing the referenced
document's own pin) stays a separate, deliberate, rarer act.

**Decided: `--pin` belongs on `build run`, primarily** — not just `deploy
run`. This has a real technical answer, not just a preference: `images`/
`charts`/`remotes` are baked in at **build time only** (see the selection
table below) — if `--pin` only existed on `deploy run`, it could only ever
affect the `artifacts` category (the one that resolves live). For 3 of
the 4 pin categories, `--pin` has to live on `build run` to do anything at
all. Build time is also where "what is going to be deployed" gets fixed —
which is exactly why a verification step belongs right after it: once
`build run --pin X` has run, confirming the pin actually took effect
shouldn't require trusting the invocation blindly. That's the same need
as the audit gap below (the SBOM's purls, once
`ManifestArtifactsModel.images`/`.repositories` are wired, already record
the *effective* version of everything that got rendered) — not a new,
separate validation mechanism, just read against *that* instead of
re-trusting the `--pin` invocation after the fact.

### Where `version` is actually selected and applied

```
Deployment.spec.version                               (a name, e.g. "dispatcher-prd")
  -> value_controller.resolve_version(context, deployment)    # index lookup by PlatformKind.VERSION
  -> VersionModel
  -> VersionService.from_model(version)
  -> .resolve(category, target_name)                    # called once per target, at the site that owns that category
```

Real consumer sites per category, confirmed by reading each directly:

| Category    | Real call site                                                                              | What it overlays                |
| ----------- | ------------------------------------------------------------------------------------------- | ------------------------------- |
| `artifacts` | `value_controller.resolve_artifact_field()` (`image_tag` field only)                        | `ArtifactSpecModel.image_tag`   |
| `remotes`   | `build_controller.py`'s remote-overlay step (runs before `source_sync.py` fetches anything) | `SolutionRemoteModel.reference` |
| `images`    | `compose.py`/`helm.py` service-rendering (build time)                                       | `ModuleServiceModel.image`      |
| `charts`    | `helm.py`'s `_render_meta()` (build time, writes `meta.yaml`'s `chartVersion`)              | `SourceModel.chart_version`     |

Every application is logged via `log_pin_applied()` (ADR-0019 decision 7)
— but only to the run's own log output, nowhere an audit could re-read it
later. That's the gap below.

### The audit gap, and why `spec.hash` isn't the fix

Looked at this from two angles — a DevOps architect ("extra commands for a
file I'm supposed to hand-edit?") and an auditor ("versions are applied in
the manifest, so I'm fine") — and both caught something real:

- **DevOps**: giving `VersionSpecModel.hash` a job (detect a hand-edit
  since the file was last machine-written) assumes a world where tooling
  is the normal writer and a hand-edit is the exception to catch. `kind:
  version` has no tool-driven writer by design — hand-editing is the only
  baseline workflow, so there's no "out of band" edit to detect. Dropped.
- **Auditor**: "the manifest" was the wrong file. `resolved.yaml`
  (`build_controller.write_resolved_manifest()`) only ever contains
  `variables`/`features`/`secrets`/`properties`/`custom` — zero version
  information. The SBOM (`sbom.json`, written unconditionally on every real
  build) does carry resolved image/chart versions as purls, but its
  reference in the audit manifest is optional, and it has no coverage for
  `remotes` at all — matching ADR-0019's own flagged, still-open gap:
  *"solution-repo provenance... has no home here... belongs in a build
  record."*

That "build record" already exists — `DeploymentManifestModel`
(`audit_manifest_model.py`), the one substantive persisted record
`audit_run.py` writes per deploy — and it already has two fields shaped
exactly for this, **never constructed anywhere** (grepped every call site):

```python
class ManifestRepositoryModel(PlatformBaseModel):
    url: str | None       # Git remote URL
    ref: str | None       # Requested git ref (tag, branch, or commit)
    commit: str | None    # Resolved full commit SHA

class ManifestImageModel(PlatformBaseModel):
    name: str              # Service or component name
    image: str              # Full image reference including tag
    digest: str | None     # Image content digest (sha256:...), when available
```

**The fix is to wire these, not add a new file or a new hash field:**

- Populate `ManifestRepositoryModel` where the `remotes` overlay already
  computes the effective ref — the data exists in memory there already,
  just never collected.
- Populate `ManifestImageModel` at the `images`/`artifacts` overlay points,
  the same way.
- `charts` has no equivalent slot yet — needs a small new model
  (`ManifestChartModel{name, chart, version}`, or widen `ManifestImageModel`).
- Record the **effective** value for every actually-deployed target, not
  only pinned ones — a raw copy of `kind: version` would only show
  overrides, silently omitting everything using its own declared default.

This also settles `spec.hash` properly rather than deferring it:
`_manifest.json` is already pushed to a git audit sink (`audit_run.py`'s
sink dispatch) — its own commit history is the tamper-evidence, same
reasoning as dropping promotion records in [promotion.md](promotion.md). A
hash field on the *source* `kind: version` document would protect the one
file in the system designed to be hand-edited; the manifest's own history
already protects the output. **Drop `spec.hash`'s tamper-detection framing
entirely — not deferred, not needed.**

This is also the fix for [promotion.md](promotion.md)'s own open "the
version column is prospective, not actual" problem — once wired, `promote
status` can source a deployment's shown version from its own last audit
manifest instead of the live pin.

## Implementation Plan

Phased, one focused change at a time, full check suite
(`mypy src/strata`, `ruff check src/ tests/`, `pytest -q`, `lint-imports`)
clean before moving to the next phase. **Phase 1 blocks
[promotion.md](promotion.md) entirely** — its whole design assumes
`Version.spec.workspace` and the exclusivity check already exist. Phases
2-4 (`path`, `version new`/`update`/`set`) are needed for the CI/CD
scenarios but not for promotion's own MVP. Phases 5-6 are independently
valuable, lower urgency.

### Phase 0 — ✅ DONE (2026-10-06) — cleanup

- Remove `VersionSpecModel.hash` from `src/strata/models/version_model.py`
  — confirmed dead (grepped every call site, zero construction anywhere),
  and this doc's own "The audit gap, and why `spec.hash` isn't the fix"
  section settled it's not needed, not merely deferred. Trivial, do it
  first so nothing downstream has to account for a field being phased
  out mid-effort.
- **Done when**: field removed, no remaining references, full check suite
  clean.
- **Shipped as**: field removed from
  [version_model.py](../../src/strata/models/version_model.py) (`extra:
  forbid` now rejects it rather than accepting it, same as the already-
  dropped `spec.ring`); module docstring updated with a dated removal
  note. [test_models_version.py](../../tests/strata/models/test_models_version.py)'s
  `test_version_accepts_tooling_hash` replaced with
  `test_version_rejects_hash_field` (mirrors `test_version_rejects_v1_ring_field`'s
  own shape). Full check suite clean, same 1964 tests (one replaced, not
  added — no new behavior to cover, just a field made absent).

### Phase 1 (blocks promotion.md) — ✅ DONE (2026-10-06) — `Version.spec.workspace` + exclusivity check

- Add `VersionSpecModel.workspace: Annotated[PlatformName,
  References(PlatformKind.WORKSPACE)] | None`.
- New Phase 2 check (same family as `_check_deployment_layers()`): every
  `Deployment` resolving to a given `Version` document must agree on its
  workspace; first real reference establishes it if unset, a declared
  value is authoritative, mismatch is an error.
- **Done when**: tests cover first-reference-establishes, declared-value-
  authoritative, and the mismatch-is-an-error cases; `strata validate`
  surfaces a real cross-workspace mistake end-to-end.
- **Shipped as**: `VersionSpecModel.workspace` field
  ([version_model.py](../../src/strata/models/version_model.py)) +
  `_check_version_workspace()` in
  [semantic_checks.py](../../src/strata/controllers/semantic_checks.py),
  wired into `run_semantic_checks()`. Tests:
  [test_models_version.py](../../tests/strata/models/test_models_version.py),
  [test_semantic_checks.py](../../tests/strata/controllers/test_semantic_checks.py)
  (`test_version_*` cases: declared-match, declared-mismatch, unset-
  established-by-first-referrer, unset-second-referrer-disagrees, no-
  referrer no-op, no-workspace-on-referrer skip). Full check suite
  (`mypy`/`ruff`/`pytest`/`lint-imports`) clean.
- Note: Phase 0's `VersionSpecModel.hash` removal was **not** bundled into
  this phase — it is independent cleanup, left for its own pass so this
  phase's diff stayed focused on the one new field + check it exists to
  add (matches this plan's own "one focused change at a time" discipline).

### Phase 2 — ✅ DONE (2026-10-06) — `strata path get`/`path list`

- New top-level `path` command group (settled placement — see its own
  section above).
- `get <kind> <name>` resolves via the existing document index; `list
  <kind> [--workspace X]` enumerates.
- **Done when**: both subcommands work against every real kind, not just
  `version`; `--output json` shape matches `{kind, name, path, exists}`
  for `get`.
- **Shipped as**: [path_controller.py](../../src/strata/controllers/path_controller.py)
  (`get_path()`/`list_paths()`, Phase 1-only resolution against the
  existing `DocumentIndex` — no `require_valid()`, matching `graph_command`'s
  own reasoning) + [path_command.py](../../src/strata/commands/path_command.py)
  (`strata path get <kind> <name>`, `strata path list <kind> [--workspace
  X]`), registered in [cli.py](../../src/strata/commands/cli.py). `path
  get` exits 3 (not found) rather than silently printing nothing; `path
  list` always exits 0, an empty result is not a failure (same convention
  `graph` already uses for "nothing found isn't a failure here"). Every
  `PlatformKind` is a valid `<kind>` argument via `click.Choice`, not just
  `version`/`deployment` — confirmed end to end against the real
  `config/` dogfood solution during manual testing, not only fixtures.
  `--workspace` filters by `spec.workspace` wherever a kind's spec model
  declares that field (today: `deployment`, `version`); any other kind
  returns empty rather than erroring — documented directly in both the
  CLI help text and `list_paths()`'s own docstring. Tests:
  [test_path_controller.py](../../tests/strata/controllers/test_path_controller.py)
  (unit-level) and
  [test_commands_path.py](../../tests/strata/commands/test_commands_path.py)
  (CLI-level, `CliRunner`). Full check suite clean, 1982 tests (18 new).

### Phase 3 — ✅ DONE (2026-10-06) — `strata version new`/`version update`

- `new <name> --workspace <ws> [--ring R --order N --wave W] [--from
  <existing>]` — needs a new `kind: version` template under
  `src/strata/templates/` (confirmed none exists today).
- `update <name>` — inverts `check_version_pins()`'s existence census;
  never touches an existing pin's value, only keys.
- **Done when**: `new --from` correctly clones pin keys without values/
  reasons; `update` correctly identifies both missing and orphaned pin
  keys against a real fixture solution.
- **Scope deviation, deliberate**: `--ring`/`--order`/`--wave` were **not**
  implemented — `VersionSpecModel.promotion` (promotion.md Phase 1) does
  not exist in the schema yet, so there was nothing real for them to set.
  Accepting and silently discarding them would be worse than not offering
  them at all; add them to `version new` once promotion.md's Phase 1
  ships, not before. No generic per-kind template tree was added under
  `src/strata/templates/` either — `scaffold_version()` writes the YAML
  directly (`yaml.safe_dump`), the same way `scaffold_controller.py`'s own
  `_ensure_manifest()` writes `strata.yaml` — inventing a Jinja-template
  mechanism for one small, fixed-shape document would be machinery with
  no second consumer yet.
- **Shipped as**: [version_controller.py](../../src/strata/controllers/version_controller.py)
  (`scaffold_version()`, `reconcile_version()`, plus private per-category
  inventory helpers shared by both) +
  [version_command.py](../../src/strata/commands/version_command.py)
  (`strata version new`/`strata version update`), registered in
  [cli.py](../../src/strata/commands/cli.py). The flagged naming
  collision was real and is now resolved: `version` is a Click group with
  `invoke_without_command=True`; `strata version` with no subcommand
  still just prints the CLI version (regression-guarded by
  `test_bare_version_still_prints_the_cli_version` plus the untouched
  pre-existing `test_commands_cli.py` suite, both passing unmodified).
  `update` is **read-only** — it only ever reports add/remove candidates
  (a `ReconcileRow` list), never writes a file; "a proposal, not an
  auto-apply" per this doc's own design, and is why `ruamel.yaml` (Phase
  4's own dependency, for `version set`'s surgical writes) was not needed
  here. A real bug was caught and fixed during manual smoke-testing
  against the `config/` dogfood solution before tests were even written:
  an "add" candidate whose real target declares no value of its own
  (e.g. a `fetch: external` remote, or a module service with no `image`)
  was initially offered with `seed_value=None` — fixed to drop such
  candidates entirely, matching the same rule `scaffold_version()`'s own
  `--from` cloning already followed. `version update`'s exit code follows
  `values status`'s own convention: 0 when fully reconciled, 3 when
  there are candidates to review (actionable, not a hard failure).
  Tests: [test_version_controller.py](../../tests/strata/controllers/test_version_controller.py)
  (13, unit-level — including the exact "no honest value to seed"
  and "fetch: external never offered" cases the manual-testing bug
  surfaced) and
  [test_commands_version.py](../../tests/strata/commands/test_commands_version.py)
  (11, CLI-level). Full check suite clean, 2006 tests (24 new).

### Phase 4 — ✅ DONE (2026-10-06) — `strata version set`

- Add `ruamel.yaml` as a real dependency (`pyproject.toml`) — settled
  write strategy, round-trip mode.
- `set <name> <category> <target> [<value>] [--available VALUE]
  [--force]`, resolution via Phase 2's `path get` internally.
- Guardrails: never touches `reason`/`reviewed`; refuses to change
  `version` on a `held`/`unverified` pin without `--force`; `--available`-
  only mode always allowed.
- **Done when**: a round-trip test proves hand-written comments survive a
  `set` call untouched; the `held`-without-`--force` refusal and the
  `--force` override are both covered; no git/clone/commit logic exists
  anywhere in this command.
- **Shipped as**: `set_version_pin()` added to
  [version_controller.py](../../src/strata/controllers/version_controller.py)
  + `strata version set` added to
  [version_command.py](../../src/strata/commands/version_command.py).
  `ruamel.yaml>=0.18` added to `pyproject.toml`'s real `dependencies`
  (`uv sync --index-strategy unsafe-best-match` — the repo's own known
  Acme-feed quirk, not a new issue). Resolution reuses
  `path_controller.get_path()` exactly as designed — one discovery
  mechanism, not two, even inside strata. Creates a brand-new pin
  (shorthand scalar, no `--available`) when `target` isn't declared yet,
  provided `<value>` is given; upgrades a shorthand pin to a structured
  mapping only when `--available` requires the extra field; mutates an
  existing structured mapping's `version`/`available` keys in place via
  `ruamel.yaml`'s `CommentedMap`, leaving `status`/`reason`/`reviewed`
  and every comment/quote-style/ordering untouched. Every write is
  re-parsed through `VersionModel` before returning, so a caller never
  has to re-read the file to trust the result. Manually verified against
  the real `config/versions/prd.yaml` (shorthand edit, held-without-force
  refusal, `--force` override, available-only on an `unverified` pin) —
  comment-for-comment identical except the one intended value, then
  reverted via `git checkout`. Exit codes match the controller's own
  `UsageError`s (bad category, nothing to set, empty value, creating a
  pin with only `--available`, held/unverified without `--force`) — all
  exit 2, consistent with `value_controller.py`'s own precedent for
  business-rule refusals, not exit 3. Tests:
  [test_version_controller.py](../../tests/strata/controllers/test_version_controller.py)
  (11 new, unit-level) and
  [test_commands_version.py](../../tests/strata/commands/test_commands_version.py)
  (9 new, CLI-level). Full check suite clean, 2026 tests (20 new).


### Phase 5 — ✅ DONE (2026-10-06) — `--pin` on `build run` (primary) and `deploy run`

- Ephemeral, per-invocation override of `Deployment.spec.version` —
  resolves a name against `kind: version`, substitutes it for this run
  only, zero persisted mutation.
- Primary target is `build run` (3 of 4 pin categories only take effect at
  build time); `deploy run` support covers the `artifacts` category.
- **Done when**: a `--pin`'d build produces identical output to hand-
  editing `spec.version` and building without the flag; the deployment
  document itself is provably untouched on disk afterward.
- **Shipped as**: the override happens in exactly one place —
  `value_controller.resolve_deployment()` gained a `version_pin` keyword
  that, when given, validates it names a real indexed Version document
  (else `UsageError`) and returns a `model_copy()` of the resolved
  deployment with `spec.version` replaced — nothing else to thread
  through, since every real consumer (`resolve_version()`,
  `resolve_artifact_field()`, the images/charts/remotes overlays in
  `build_controller.py`) reads `spec.version` off whatever deployment
  object it was handed. `resolve_values()` forwards the same parameter to
  its own internal `resolve_deployment()` call (it does its own,
  independent resolution rather than reusing a caller's) — without that,
  `--resolve`'s validation and the `artifacts` category at deploy time
  would silently see the on-disk version instead of the pin, since
  `store: artifact` resolves live through `resolve_artifact_field()`, the
  one category deploy time actually needs it for. `build_run()`/
  `deploy_run()` both gained a `pin` parameter forwarded to both calls;
  `build_command.py`/`deploy_command.py` both gained a `--pin VERSION`
  option. Discovered and fixed one real gap while implementing, not
  before: `resolve_values()` originally did not forward the pin at all —
  caught by deliberately testing the `artifacts` category specifically
  (the one category the design doc itself flags as "the one that resolves
  live"), not just the three build-time categories `_apply_remote_version_pins()`
  already covered. `audit_run.py` was deliberately left unpinned — the
  audit manifest doesn't capture version/pin provenance at all yet
  (confirmed: `ManifestImageModel`/`ManifestRepositoryModel` are still
  never constructed anywhere), so threading `pin` into it now would be
  premature; that wiring is exactly Phase 6's job, not this one's.
  Verified both "Done when" halves directly:
  `test_build_run_pin_produces_identical_output_to_hand_editing_spec_version`/
  `test_deploy_run_pin_matches_hand_editing_spec_version` build the same
  fixture twice — once with `spec.version` hand-edited on disk, once with
  it absent and `--pin` supplied instead — and assert identical rendered
  output; `test_build_run_pin_never_mutates_the_deployment_document_on_disk`/
  `test_deploy_run_pin_never_mutates_the_deployment_document_on_disk`
  assert the deployment file's bytes are unchanged afterward. Tests:
  [test_value_controller.py](../../tests/strata/controllers/test_value_controller.py)
  (6 new — `resolve_deployment()`/`resolve_values()` unit-level),
  [test_build_controller.py](../../tests/strata/controllers/test_build_controller.py)
  (4 new), [test_deploy_controller.py](../../tests/strata/controllers/test_deploy_controller.py)
  (6 new — including the artifacts-category coverage that caught the
  `resolve_values()` gap), and CLI-level smoke tests in
  [test_commands_build.py](../../tests/strata/commands/test_commands_build.py)/
  [test_commands_deploy.py](../../tests/strata/commands/test_commands_deploy.py)
  (2 each). Full check suite clean, 2045 tests (19 new).

### Phase 6 — ✅ DONE (2026-10-06) — audit gap wiring

- Populate `ManifestRepositoryModel` at the `remotes` overlay's existing
  computation point.
- Populate `ManifestImageModel` at the `images`/`artifacts` overlay
  points.
- Add `ManifestChartModel{name, chart, version}` (or widen
  `ManifestImageModel`) for `charts` — no existing slot covers it.
- **Done when**: a real `deploy run` against a multi-category pinned
  deployment produces a manifest recording the *effective* value for
  every actually-deployed target, not only pinned ones; this is also the
  prerequisite for fixing [promotion.md](promotion.md)'s "prospective, not
  actual" column and for verifying `--pin`'s effect post-build.
- **Shipped as**: `ManifestChartModel` added to
  [audit_manifest_model.py](../../src/strata/models/audit_manifest_model.py),
  plus `repositories`/`images`/`charts` fields on `ManifestArtifactsModel`
  (all `None` by default, matching every other optional field there).
  [audit_run.py](../../src/strata/controllers/audit_run.py) gained
  `_collect_artifacts_bom()` and a `pin` parameter (threaded from
  `deploy_command.py`, matching `deploy_run()`'s own Phase 5 parameter —
  the manifest must record what was *actually* deployed, not whatever
  `spec.version` says on disk).
  **Deliberately not sourced from `sbom.json`**: a real investigation
  during implementation found `write_sbom()` resolves every module
  straight off the `DocumentIndex` (`sbom_controller._resolve_all_modules()`),
  with **no `kind: version` pin overlay applied at all** — accurate for
  *which* images/charts exist, silently wrong for their *effective*
  version whenever a pin is active. (A real, separate, pre-existing SBOM
  gap, out of this phase's scope to fix — noted below instead.) Reused
  the exact same overlay functions `build_run()` itself calls instead, so
  there is exactly one resolution path, not a second independently-
  maintained copy: `workload_controller._apply_version_pins()` and
  `build_controller._apply_remote_version_pins()` were both promoted to
  public (`apply_version_pins()`/`apply_remote_version_pins()`) since
  `audit_run.py` is now a second, real production caller of each, not
  just their own tests reaching into a private helper. Scoped to what
  the deployment's own workspace actually reaches (its provisioners' and
  modules' `source.remote`, its namespaces' modules) — never the whole
  solution's — matching "effective value for every actually-deployed
  target," not everything merely declared somewhere else. Verified with
  a real multi-category fixture (one `fetch: strata` remote, one
  chart-based module, one image-based module, one `Version` document
  pinning all three) in three shapes: pinned via `spec.version` on disk,
  unpinned (declared values recorded faithfully — "not only pinned
  ones"), and pinned via `--pin` with no on-disk `spec.version` at all
  (proving Phase 5's audit-side gap is closed too). A fourth test
  confirms the pre-Phase-6 base fixture (nothing reachable) still omits
  all three fields entirely, not empty lists/dicts — no regression to
  `exclude_none` serialisation. Tests:
  [test_audit_run.py](../../tests/strata/controllers/test_audit_run.py)
  (4 new). Full check suite clean, 2049 tests.
  **Phases 0-6 are now all done — docs/work/version-lifecycle.md's full
  Implementation Plan is complete.**

## Related Decisions

- [ADR-0019](../decisions/0019-version-pinning.md) — source of
  `VersionPinModel`'s `status`/`reason`/`available` fields this whole doc
  relies on, the `remotes` provenance gap the audit section closes, and
  the `--pin` CLI flag this doc revives.
- `audit_manifest_model.py` / `audit_run.py` (docs/design/audit-trail.md)
  — `DeploymentManifestModel.artifacts.{images,repositories}` are the dead
  fields this doc proposes wiring to close the version-provenance gap.
- `VersionPinModel.available`'s own docstring (*"Refresh tooling may
  rewrite this field freely; it must never rewrite `reason`"*) — ADR-0019
  anticipated the third-party-dependency bot this doc designs.
- [promotion.md](promotion.md) — the ring/order/wave tagging and
  `promote status`/`apply` commands built on top of everything here.

## Remaining Work / Open Questions

- All six phases are now built (see the Implementation Plan above for
  what shipped, when, and with which tests). Nothing in this doc's own
  scope remains designed-but-unbuilt.
- **New, separate gap found during Phase 6's implementation, deliberately
  left unfixed here**: `write_sbom()` resolves every module straight off
  the `DocumentIndex` (`sbom_controller._resolve_all_modules()`), with no
  `kind: version` pin overlay applied at all — `sbom.json` is accurate for
  *which* images/charts exist, silently wrong for their *effective*
  version whenever a pin is active. Phase 6 worked around this by
  re-deriving the effective BOM independently for the audit manifest
  (reusing `apply_version_pins()`/`apply_remote_version_pins()`, not the
  SBOM) rather than fixing the SBOM's own resolution — a real, scoped fix
  for a future pass: thread the same `apply_version_pins()` overlay into
  `sbom_controller._resolve_all_modules()` itself (it already receives
  `graph`, which carries `deployment`; needs `resolve_version()`'s result
  threaded in alongside).
- Digests remain deferred — pending a real digest field on
  `VersionPinModel` and actual demand. (`spec.hash`, by contrast, is a
  settled "don't build this," not merely deferred.)
- Agent-assisted "should we adopt this" judgment (as opposed to mechanical
  `available`-discovery, which turned out to be ordinary Dependabot-style
  polling, not AI at all) is still an explicitly deferred idea, not
  designed — depends on an AI-assist capability out of scope now.

## Changelog

- 2026-10-06: Split out of [promotion.md](promotion.md) following a
  critical review that found most of its content applied equally with or
  without ring/order/wave tagging — not actually promotion-specific.
  Reframed `version set` to drop the "for machines, not humans" framing:
  the thing worth protecting is the mutation's guardrails (never touches
  `reason`/`reviewed`, refuses to bump a `held`/`unverified` pin without
  `--force`), not a restriction on who calls it. Reopened the file-lookup
  command's placement (previously settled on nesting under `sln`) as a
  genuinely undecided question, with three concrete options on the table
  and none chosen. Folded in `cfg-int-deployment`'s real, already-in-use
  release-train workflow as the concrete grounding for the CI/CD section,
  replacing what had been purely hypothetical framing.
- 2026-10-06: Settled the file-lookup command's placement — a dedicated
  `strata path` group, not `sln`/`graph`, on the standard that a group
  needs more than one real command to justify existing: `path get <kind>
  <name>` (single lookup) and `path list <kind>` (enumeration) are both
  genuinely about paths, satisfying that bar without stretching the name.
  A `show`-the-*content* idea was raised as a possible third sibling and
  explicitly left out — it doesn't fit `path`'s own name.
- 2026-10-06: Settled three more open questions. `version set`'s write
  strategy is `ruamel.yaml` round-trip, not a hand-rolled regex — a regex
  has to handle every valid YAML shape a pin's value can take, a
  purpose-built round-trip parser already does. The third-party-dependency
  polling bot is explicitly **not** strata's own job to build — per-
  ecosystem registry integration is a maintenance burden with no
  proportional payoff when `path`/`version set` already give any repo what
  it needs to build its own. `--pin` belongs on `build run`, primarily —
  not just a preference: 3 of 4 pin categories only take effect at build
  time, so `--pin` has to live there to affect them at all. Also connected
  `--pin` to the audit gap: once wired, that's the verification that the
  pin actually took effect, rather than trusting the invocation blindly.
- 2026-10-06: Added a full Implementation Plan — seven phases (0-6),
  starting with removing the now-settled-dead `VersionSpecModel.hash`
  field and adding `Version.spec.workspace` (both small, both block
  [promotion.md](promotion.md) entirely), then `path`, then `version new`/
  `update`/`set`, then `--pin`, then the audit-gap wiring. Each phase has
  a concrete "done when" check, not just a description of what it adds.
- 2026-10-06: **Shipped Phase 1** — `VersionSpecModel.workspace` field plus
  `_check_version_workspace()`, the cross-document exclusivity check
  (declared value authoritative, first referencing deployment establishes
  it when unset, mismatch is an error). 6 new tests across the model and
  semantic-check suites; full check suite clean. This unblocks
  [promotion.md](promotion.md), whose entire ring/order/wave design
  assumed this field and check already existed. Phase 0 (removing the
  dead `spec.hash` field) was deliberately left out of this pass — kept
  as its own, separately-scoped cleanup rather than bundled in.
- 2026-10-06: **Shipped Phase 0** — removed `VersionSpecModel.hash`.
  Re-grepped every call site immediately before deleting it (confirming
  the earlier census still held: zero real construction anywhere besides
  the field definition and its own now-replaced test) rather than trusting
  the prior grep's result at a distance. Phases 0 and 1 are now both done;
  Phase 2 (`strata path get`/`list`) is next.
- 2026-10-06: **Shipped Phase 2** — the `path` command group:
  `path_controller.py` (`get_path()`/`list_paths()`) + `path_command.py`
  (`strata path get`/`strata path list`), both Phase-1-only, no
  `require_valid()` (matching `graph_command`'s reasoning that a lookup is
  meaningful even against a Phase-2-broken solution). Verified against
  every `PlatformKind`, not just `version`, and manually exercised against
  the real `config/` dogfood solution end to end (not only test
  fixtures). `--workspace`'s documented limitation (empty, not an error,
  for a kind with no `spec.workspace` field) was deliberately kept rather
  than resolved with a `UsageError` — matches the "skip rather than guess
  a policy that was never declared" rule already used elsewhere in this
  codebase. Phases 0-2 are now all done; Phase 3 (`version new`/`update`)
  is next.
- 2026-10-06: **Shipped Phase 3** — `strata version new`/`strata version
  update`. Resolved the flagged `version` naming collision for real (Click
  group, `invoke_without_command=True`, bare `strata version` unchanged).
  Deliberately scoped down from the original plan: `--ring`/`--order`/
  `--wave` deferred (promotion.md's schema doesn't exist yet — nothing
  real for them to set), and no generic per-kind template-tree mechanism
  was built (one small fixed-shape document doesn't justify one). `update`
  shipped strictly read-only (report-only `ReconcileRow` list, exit 0/3
  mirroring `values status`'s own convention) — confirming Phase 4 is
  where `ruamel.yaml` actually needs to enter the dependency tree, not
  here. Manual smoke-testing against the real `config/` dogfood solution
  (not just fixtures) caught a real bug before any test was written: an
  "add" candidate for a target with no current value of its own (a
  `fetch: external` remote, an imageless module service) was being
  offered with a `None` seed — fixed to drop such candidates, matching
  `scaffold_version()`'s own `--from`-cloning rule. 24 new tests (13
  controller-level, 11 CLI-level); full check suite clean. Phases 0-3 are
  now all done; Phase 4 (`version set`) is next.
- 2026-10-06: **Shipped Phase 4** — `strata version set`, and with it
  `ruamel.yaml` entered the dependency tree for real
  (`uv sync --index-strategy unsafe-best-match`, same documented Acme-feed
  workaround as every other `uv sync` in this repo). Manually verified
  comment-for-comment preservation against the real
  `config/versions/prd.yaml` (not just fixtures) before writing a single
  test — a shorthand edit, a held-pin refusal, a `--force` override, and
  an available-only change on an unverified pin, each confirmed byte-exact
  except the one intended field, then reverted. Settled, not previously
  spelled out: creating a brand-new pin stays shorthand unless
  `--available` forces a structured mapping; business-rule refusals
  (unknown category, nothing to set, empty value, creating with only
  `--available`, held/unverified without `--force`) are all `UsageError`/
  exit 2, matching `value_controller.py`'s own precedent for this shape
  of refusal rather than treating them as validation failures (exit 3).
  20 new tests (11 controller-level, 9 CLI-level); full check suite clean,
  2026 tests. **Phases 0-4 are now all done** — the hand-editing-or-tooling
  parity this whole doc opened with is real: every mutation a human could
  make by hand to a `kind: version` document now has a narrow, guarded
  CLI equivalent. Phase 5 (`--pin` on `build run`/`deploy run`) is next.
- 2026-10-06: **Shipped Phase 5** — `--pin` on `build run`/`deploy run`.
  Centralized the override in one place, `resolve_deployment()`'s own new
  `version_pin` keyword, rather than threading a parallel parameter
  through every real consumer separately — every one of them already
  reads `spec.version` off the deployment object it was handed. Caught
  (via deliberately testing the `artifacts` category, not just the three
  build-time ones) and fixed a real gap mid-implementation:
  `resolve_values()` has its own independent internal `resolve_deployment()`
  call, so without also forwarding the pin there, `--resolve`'s validation
  and `deploy run --pin`'s `store: artifact` resolution would have
  silently used the on-disk version instead. Both "Done when" halves
  verified directly — a `--pin`'d run matches hand-editing `spec.version`
  byte-for-byte in rendered output, and the deployment document's own
  bytes are provably unchanged afterward — for both `build run` and
  `deploy run`. `audit_run.py` deliberately left out of scope: the audit
  manifest doesn't capture version/pin provenance at all yet (confirmed:
  `ManifestImageModel`/`ManifestRepositoryModel` still never constructed
  anywhere), so that's genuinely Phase 6's job, not a thing to bolt on
  here. 19 new tests across `value_controller`/`build_controller`/
  `deploy_controller` plus CLI smoke tests; full check suite clean, 2045
  tests. Phases 0-5 are now all done; Phase 6 (audit gap wiring) is next.
- 2026-10-06: **Shipped Phase 6** — the audit gap, closed. Added
  `ManifestChartModel` and wired `ManifestArtifactsModel.repositories`/
  `.images`/`.charts`. Found and deliberately worked around a real,
  separate gap while implementing: `sbom.json`'s own components are
  never pin-overlaid (`write_sbom()` resolves modules straight off the
  index), so the manifest's BOM is re-derived independently, reusing the
  same `apply_version_pins()`/`apply_remote_version_pins()` overlay
  functions `build_run()` itself calls — promoted both from private to
  public since `audit_run.py` is now a second real caller of each, not
  just their own tests reaching in. `deploy_command.py`'s `--pin` now
  also flows into the audit finalize step, closing Phase 5's own
  deliberately-deferred gap. Verified with a real multi-category fixture
  in three shapes (hand-edited `spec.version`, no version at all, `--pin`
  with no on-disk version) plus a no-regression case for the base
  fixture. 4 new tests; full check suite clean, 2049 tests.
  **Phases 0-6 are now all done — docs/work/version-lifecycle.md's full
  Implementation Plan is complete.** One real, separate gap was
  discovered and intentionally left for a future pass rather than
  scope-crept into this one: `sbom.json` itself still doesn't reflect
  pin overlays (documented in Remaining Work above).
- 2026-10-06: **Code review of Phases 0-6.** Read every changed file
  against its own design intent rather than re-trusting the tests that
  already passed. Found and fixed one real bug: `scaffold_version()`
  (`version new`) never validated the new document's `meta.name` before
  writing — `strata version new "Invalid Name" --workspace main`
  silently wrote a permanently schema-invalid file to disk (confirmed by
  actually running it, not just inspecting the code), with zero feedback
  until a later `strata validate` run. Fixed by validating the assembled
  document through `VersionModel` before writing, translating a pydantic
  `ValidationError` into a `UsageError` — same "catch it at the one
  command that can still say something useful, not two steps later"
  standard `set_version_pin()` already met via its own post-write
  re-parse. One new regression test. Also fixed a small doc-accuracy
  nit: `cli.py`'s own module docstring listed `version` as `(new/update)`,
  missing `set`. Noted, not fixed (deliberate, low-value tradeoffs):
  `apply_version_pins()`/`apply_remote_version_pins()` now run twice per
  real pin application per `deploy run` (once during `build run`, again
  when `audit_run.py` re-derives the effective BOM) — both calls are
  correct and cheap, but each logs its own "version pin applied" line,
  so that event no longer means "exactly once per real application";
  re-deriving from the pin-resolution functions rather than threading
  computed data across two separate CLI invocations was the deliberate
  Phase 6 tradeoff, and duplicate log lines are its one visible cost.
  Full check suite clean, 2050 tests (1 new).
