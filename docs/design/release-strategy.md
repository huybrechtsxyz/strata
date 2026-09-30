# v2 Release Strategy — Design

- Status: draft
- Last updated: 2026-09-30

## Overview

strata-v2 lives in the **same repository** as v1 (`huybrechtsxyz/strata`): `main`
is v1's continuing line (tags `v0`…`v1.11.2`, still the moving major tags
external GitHub Action consumers pin to), and `v2` is the long-lived branch
where the from-scratch rewrite happens. This doc plans how v2 cuts its
**first published pre-release, `v2.0.0-alpha`**, without disturbing v1's
existing release line, and captures the concrete decisions made so far so a
later session can execute them in small, checked phases (per repo convention
— small, tested, documented phases).

Nothing in this doc has been executed yet — no file has been renamed, no
workflow edited, no tag pushed. This is the plan only.

## Current Design (as of 2026-09-30)

- **`VERSION.txt`** = `2.0.0-Alpha` at the repo root — the single source
  [`tool.setuptools.dynamic`](../../pyproject.toml) reads for the package
  version. `packaging` normalizes this to canonical PEP 440 `2.0.0a0` at
  runtime (confirmed: `strata version` → `2.0.0a0`) — the file itself is not
  yet in canonical form.
- **`pyproject.toml`**: `name = "strata-v2"` — per
  [`strata/utils/version.py`](../../src/strata/utils/version.py)'s own
  docstring, this is a **deliberate, temporary** placeholder distribution name
  "so v2 can be developed alongside v1," distinct from the permanent import
  package name (`strata`). The real, permanent PyPI name is `xyz-strata` —
  same name v1 already publishes under (`strata` itself was unavailable on
  public PyPI when v1 first published).
- **`.github/workflows/ci-release.yml`** (as it exists on `v2`): triggers on
  any `v*.*.*` tag push, regardless of branch (tag refs aren't branch-scoped;
  the workflow definition used is whichever version exists at the tagged
  commit). Four jobs:
  1. `release` — downloads the `dist/` artifact from the matching `ci-build`
     run, creates a GitHub Release via `softprops/action-gh-release` (no
     `prerelease:` input wired yet — always a "Latest" release today).
  2. `publish-pypi` — `uv publish --trusted-publishing always` (OIDC trusted
     publishing; PyPI trusted-publisher bindings key on **org/repo + workflow
     file path**, not branch or package name at request time — since v1's
     `main` already publishes `xyz-strata` through a `ci-release.yml` at the
     same path, this likely already works once `pyproject.toml`'s `name`
     matches `xyz-strata`, but this must be verified against the real PyPI
     project settings before the first tag push, not assumed).
  3. `publish` (GHCR) — tags `ghcr.io/<owner>/strata` using
     `docker/metadata-action` with `type=semver,pattern={{version}}`,
     `type=semver,pattern={{major}}.{{minor}}`, and **`type=raw,value=latest`
     unconditionally** — this last one is the problem: an alpha build would
     overwrite `latest` today.
  4. `publish-hub-cli` (Docker Hub) — same `latest` problem, gated only on
     `vars.REGISTRY_URL` being set.
- **v1's `scripts/Release.ps1`** (not yet ported to v2 — see
  [`scripts/README.md`](../../scripts/README.md) for what has been ported)
  hard-requires being on `main`, parses versions with .NET
  `[System.Version]` (cannot parse a `-alpha`/`a1` suffix at all — throws),
  and moves a floating major tag (`v0`) for external Action consumers. None
  of this applies as-is to a `v2`-branch alpha.

## Decisions

1. **Tag source branch: `v2` directly.** Do not merge `v2` into `main` first.
   `v2` is where alpha/pre-release tags are cut until v2 is ready to actually
   replace v1 on `main`. (User: "design only, no code, no merge yet" — this
   remains an open execution step, not performed.)
2. **Final distribution name: `xyz-strata`** (not `strata`, not
   `strata-v2`). Matches v1's real, already-registered PyPI project — `strata`
   was unavailable when v1 first published. `pyproject.toml`'s `name` field
   needs to change from `strata-v2` → `xyz-strata` before the first real
   publish. `strata.utils.version.PACKAGE_NAME` (the *import* package name)
   stays `strata` — only the distribution name changes.
3. **PyPI trusted publishing: assumed already configured** for `xyz-strata`
   (v1 already publishes there via a same-path `ci-release.yml`) — **must be
   verified against pypi.org's actual trusted-publisher settings for the
   `xyz-strata` project before the first tag push**, not assumed blind. If
   the trusted-publisher entry is scoped to a specific workflow filename that
   differs, or to `main` only, it will need a second entry added for
   publishing from `v2`.
4. **Docker `latest` gating: needed.** `ci-release.yml`'s Docker metadata
   step must stop applying `type=raw,value=latest` unconditionally — gate it
   to stable (non-pre-release) semver tags only, so `v2.0.0-alpha` never
   overwrites the `latest` image tag that real consumers pull. (Docker's
   `metadata-action` supports this via `type=raw,value=latest,enable=...`
   conditioned on `!contains(github.ref_name, '-')` or similar — exact
   expression TBD at implementation time.)
5. **GitHub Release prerelease flag: needed.** Wire
   `softprops/action-gh-release`'s `prerelease:` input, auto-detected from
   the tag (contains `-alpha`, `-beta`, `-rc`, etc. → `true`).
6. **`VERSION.txt` normalization: needed.** Change from `2.0.0-Alpha` to
   canonical PEP 440 `2.0.0a1` (not `2.0.0a0` — `a0` is what `packaging`
   defaults to for an *unnumbered* `-Alpha`; an explicit first alpha build
   should read `a1`) so the file itself matches what `packaging`/`pip`
   actually report, instead of relying on silent normalization.
7. **Merge-to-`main` criterion: v2 reaches beta.** `v2` merges into `main`
   (becoming the real, released line — superseding v1) the moment v2 can
   stand in for v1 against both real consumers tracked in
   [`v1-consumer-usage`](../../.github/copilot-instructions.md) repo memory —
   **haven** and **cfg-int-deployment** — i.e. `strata build run` +
   `strata deploy run` work end-to-end for both repos' real deployment
   files, not just `validate`/`values get`. That milestone is what
   `v2.0.0-beta1` marks; alpha tags (`v2.0.0-alphaN`) are cut before that,
   entirely from `v2`, without touching `main`. This directly follows the
   repo's own "evidence over assumption" convention — parity is proven
   against real consumer CI, not assumed once the last model/service is
   built.

## Release Plan

A version-line roadmap: `v2.0.0-alphaN` (on `v2`) → `v2.0.0-betaN` (`v2` merged
into `main`) → `v2.0.0-rcN` → `v2.0.0` (stable). Each stage's exit criterion is
a real capability/consumer bar, not a calendar date or a fixed feature count —
consistent with the repo's "evidence over assumption" convention. Command
implementation status is deliberately **not** duplicated in detail here — see
each command's own design doc (linked below) for the authoritative, current
state; those docs are living and get updated more often than this plan will.

### Stage 1 — `v2.0.0-alphaN` (current stage, tag from `v2`)

**Purpose:** get an installable pre-release (`pip install xyz-strata==2.0.0a1`)
out for early/internal testing — "installable and internally coherent," not
"production-ready." Does not require full command parity with v1.

- Entry bar for `alpha1`: `validate`, `values get`, `build run`, `deploy run`,
  and `sln init`/`update` all exist and the full check suite is green
  ([`Check.ps1`](../../scripts/Check.ps1)) — true today.
- Further `alphaN` tags are cut ad hoc as significant units land — no fixed
  exit criterion gates leaving this stage other than Stage 2's entry bar
  below. Candidate units likely to each warrant a new alpha tag:
  build output rendering's remaining phases (see
  [build-pipeline-status.md](build-pipeline-status.md)), `deploy run`'s
  confirmed `store: artifact` gap (see
  [deploy-command.md](deploy-command.md) Remaining Work item 8), and the
  Tier 2 commands (`init`/`repo`/`profile`/`config`/`audit`/`tools`) starting
  to land.

### Stage 2 — `v2.0.0-betaN` (merge `v2` → `main` here, per Decision 7)

**Entry bar (all required, per Decision 7):** `strata build run` +
`strata deploy run` work end-to-end against **both** haven's and
cfg-int-deployment's real deployment files — not just `config/`'s shipped
example or synthetic tests. Concretely, before the first beta:

1. `.v2-haven/` (already migrated per repo memory, 2026-09-28) needs a real
   `build run` + `deploy run --dry-run` pass — today it has only been
   proven against `validate`.
2. An equivalent `.v2-cfgint/` fixture needs to be created the same way
   (hand-migrated from cfg-int-deployment's real config) — does not exist
   yet.
3. Build output rendering's remaining phases — token substitution, the
   Jinja2 escape hatch, Compose/Helm rendering, `modules`/`tenant`
   categories — land (see [build-pipeline-status.md](build-pipeline-status.md)
   for current phase status).
4. `deploy run`'s confirmed `store: artifact`-at-deploy-time gap closes (see
   [deploy-command.md](deploy-command.md) Remaining Work). Locking,
   SIEM/audit, whole-run timeout, and `--force`'s advisory-gate consumption
   stay deliberately deferred through beta — hardening, not correctness,
   and no real consumer evidence forces them yet.

**On reaching this bar:** merge `v2` → `main`, then cut `v2.0.0-beta1` from
`main`. Alpha tags only ever exist on `v2`; beta tags only ever exist on
`main`. **Concrete git constraint to plan around:** a branch and a tag
cannot share a name — the `v2` branch must be deleted (post-merge) before a
`v2` moving major tag (the eventual successor to v1's `v0` convention) can
be created; sequence that rename/cutover deliberately, not as an afterthought.

### Stage 3 — `v2.0.0-rcN`

**Entry bar:** beta is feature-complete for the haven/cfg-int-deployment
bar above; only bugfixes land from here, no new capability. Requires an
actual trial run against one real consumer's real CI (not just the fixture
copies) — e.g. a side-by-side/opt-in run of haven's or cfg-int-deployment's
real pipeline against a `v2.0.0-beta*` build, not merely the local fixture.
**Exit bar:** no P0/P1 bug found during the trial period (length TBD —
revisit once beta is actually reached; premature to fix a number now).

### Stage 4 — `v2.0.0` (stable)

**Entry bar:** RC trial period clean; `docs/design/gap_fit_v1.md`'s open gaps are
each either resolved or explicitly re-confirmed as an accepted, documented
scope cut (not silently dropped); `README.md`/`CONTRIBUTING.md` updated to
describe v2 as *the* current version rather than a parallel-track rewrite;
the v1-maintenance-branch question (Decision 7 / Remaining Work item 6
below) is resolved, not left open. This is also when the `xyz-strata` PyPI
project's "stable" line begins for real (removing any pre-release
disclaimers from `README.md`).

## Remaining Work / Open Questions

Execution order (each its own small, checked phase per repo convention —
`Check.ps1`/full suite green before moving to the next):

1. Rename `pyproject.toml`'s `name` to `xyz-strata`; confirm `uv sync` /
   `uv build` still produce a correctly named wheel/sdist; confirm
   `strata.utils.version.get_distribution_name()` still resolves correctly
   post-rename (it reads `packages_distributions()`, keyed by the *new*
   name).
2. Verify the real PyPI trusted-publisher configuration for `xyz-strata`
   (pypi.org project settings → Publishing) covers a tag pushed from a
   non-`main` ref through this repo's `ci-release.yml` path. This is an
   account/website check, not a code change — flag to the user to confirm
   directly on pypi.org since this agent has no PyPI access.
3. Fix `ci-release.yml`: gate the Docker `latest` tag to stable releases,
   and wire the `prerelease:` flag on the GitHub Release step.
4. Normalize `VERSION.txt` to `2.0.0a1`.
5. Decide whether to port a v2-adapted `scripts/Release.ps1` (supporting
   PEP 440 pre-release suffixes, and not hard-requiring `main`) or cut the
   first alpha tag manually. Given this is a one-off first alpha, manual
   `git tag -a v2.0.0-alpha1 -m "..."` + `git push origin v2.0.0-alpha1` from
   `v2` may be simpler than building tooling for a not-yet-repeated case —
   revisit once a second/third alpha is needed.
6. **Resolved (see Decision 7): merge `v2` → `main` at beta**, defined as
   "can deploy both haven and cfg-int-deployment." Still genuinely open:
   what happens to v1's moving major tag (`v0`) and its existing stable
   `xyz-strata` release line at that point — does `main` post-merge start
   cutting `v2.x` stable releases immediately, or does a `v1` maintenance
   branch get cut off first so v1 patches remain possible after the merge?
   Revisit once beta is actually in sight (`build run`/`deploy run` both
   proven against haven and cfg-int-deployment) — per repo memory, `build
   run` is the current next step, `deploy run` after that.

## Changelog

- 2026-09-30: Initial draft — captured current state and the decisions made
  in this session (branch = `v2` direct, name = `xyz-strata`, Docker `latest`
  gating needed, GitHub prerelease flag needed, `VERSION.txt` → `2.0.0a1`).
  No files changed yet; this doc is the plan only.
- 2026-09-30: Added Decision 7 — `v2` merges into `main` at beta, defined as
  the moment `strata build run` + `strata deploy run` can stand in for v1
  against both real tracked consumers (haven, cfg-int-deployment), not on a
  fixed calendar/feature-count basis. Left the v1-maintenance-branch
  question open (item 6) since it only matters once beta is actually near.
- 2026-09-30: Added `## Release Plan` — the 4-stage version-line roadmap
  (alpha on `v2` → beta merges to `main` → rc → stable), with each stage's
  entry/exit bar spelled out concretely rather than left as a date. Flagged
  a real git constraint found while writing it: the `v2` branch must be
  deleted before a `v2` moving major tag can exist (branches/tags share a
  namespace) — sequencing note for the eventual beta cutover.
