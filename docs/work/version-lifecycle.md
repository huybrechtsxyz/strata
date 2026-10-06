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

- `version new`/`version update`/`version set` are designed, not built —
  `version set`'s write strategy (`ruamel.yaml` round-trip) is settled,
  just not implemented yet. The `version` CLI naming collision (today a
  bare command, needs to become a group while still printing the CLI
  version with no subcommand) is a real implementation detail, not just a
  doc nit. `strata path get`/`list` are designed, not built either.
- Wiring `ManifestArtifactsModel.images`/`.repositories` (and the new
  `ManifestChartModel`) is real, scoped, not-yet-built work — the actual
  fix for the audit gap, for `promotion.md`'s prospective-vs-actual
  problem, and for verifying `--pin`'s effect post-build (see its own
  section above).
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
