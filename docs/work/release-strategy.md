# v2 Release Strategy — Design

- Status: draft — Decisions 2, 4, 5, 6 executed (2026-09-30); Decisions 1, 3
  and the actual tag push remain outstanding
- Last updated: 2026-09-30

## Overview

strata-v2 lives in the **same repository** as v1 (`huybrechtsxyz/strata`): `main`
is v1's continuing line (tags `v0`…`v1.11.2`, still the moving major tags
external GitHub Action consumers pin to), and `v2` is the long-lived branch
where the from-scratch rewrite happens. This doc plans how v2 cuts its
**first published pre-release, `v2.0.0-alpha1`**, without disturbing v1's
existing release line, and captures the concrete decisions made so far so a
later session can execute them in small, checked phases (per repo convention
— small, tested, documented phases).

**Executed so far (2026-09-30, local/reversible changes only — no tag
pushed, no workflow run, no publish)**: `VERSION.txt` normalized,
`pyproject.toml` renamed, `ci-release.yml`'s prerelease/`latest`-gating
wired (Decisions 2/4/5/6 below). Verified with a real local build +
isolated-venv install (`uv build` → fresh `uv venv` → install the wheel →
`strata version`/`strata --help` both work). **Still not done**: Decision 1
(actually tagging from `v2`), Decision 3 (verifying PyPI trusted-publisher
settings), and porting `scripts/Release.ps1` — all deliberately left as
separate, later steps (pushing a tag triggers real CI that publishes to
PyPI/GHCR/Docker Hub, a shared-system action, not a local/reversible one).

## Current Design (as of 2026-09-30)

- **`VERSION.txt`** = `2.0.0-Alpha2` at the repo root (was `2.0.0-Alpha1`,
  originally `2.0.0-Alpha`) — the single source [`tool.setuptools.dynamic`](../../pyproject.toml) reads
  for the package version. `packaging` normalizes this to canonical PEP 440
  `2.0.0a2` at runtime (confirmed: `strata version` → `2.0.0a2`, and a real
  `uv build` produces `xyz_strata-2.0.0a2-py3-none-any.whl`).
- **`pyproject.toml`**: `name = "xyz-strata"` (was `strata-v2`) — per
  [`strata/utils/version.py`](../../src/strata/utils/version.py)'s own
  docstring, the distribution name was always meant to be a temporary,
  renameable placeholder, distinct from the permanent import package name
  (`strata`) — confirmed by `test_version_survives_a_distribution_rename`
  (`test_utils_version.py`), which passed with zero source changes needed.
  Same name v1 already publishes under (`strata` itself was unavailable on
  public PyPI when v1 first published).
- **`.github/workflows/ci-release.yml`** (as it exists on `v2`): triggers on
  any `v*.*.*` tag push, regardless of branch (tag refs aren't branch-scoped;
  the workflow definition used is whichever version exists at the tagged
  commit). Four jobs:
  1. `release` — downloads the `dist/` artifact from the matching `ci-build`
     run, creates a GitHub Release via `softprops/action-gh-release`, now
     with `prerelease: ${{ contains(github.ref_name, '-') }}` wired — an
     alpha/beta/rc tag (contains `-`) is correctly marked pre-release, a
     stable `vX.Y.Z` tag is not.
  2. `publish-pypi` — `uv publish --trusted-publishing always` (OIDC trusted
     publishing; PyPI trusted-publisher bindings key on **org/repo + workflow
     file path**, not branch or package name at request time — since v1's
     `main` already publishes `xyz-strata` through a `ci-release.yml` at the
     same path, this likely already works now that `pyproject.toml`'s `name`
     matches `xyz-strata`, but this must still be verified against the real
     PyPI project settings before the first tag push, not assumed).
  3. `publish` (GHCR) — tags `ghcr.io/<owner>/strata` using
     `docker/metadata-action` with `type=semver,pattern={{version}}`,
     `type=semver,pattern={{major}}.{{minor}}`, and now
     `type=raw,value=latest,enable=${{ !contains(github.ref_name, '-') }}` —
     an alpha/beta/rc tag no longer overwrites `latest`.
  4. `publish-hub-cli` (Docker Hub) — same `latest` gating now applied,
     still gated on `vars.REGISTRY_URL` being set.
- **v1's `scripts/Release.ps1`** (not yet ported to v2 — see
  [`scripts/README.md`](../../scripts/README.md) for what has been ported)
  hard-requires being on `main`, parses versions with .NET
  `[System.Version]` (cannot parse a `-alpha`/`a1` suffix at all — throws),
  and moves a floating major tag (`v0`) for external Action consumers. None
  of this applies as-is to a `v2`-branch alpha — still not ported.

## Decisions

1. **Tag source branch: `v2` directly.** Do not merge `v2` into `main` first.
   `v2` is where alpha/pre-release tags are cut until v2 is ready to actually
   replace v1 on `main`. (User: "design only, no code, no merge yet" — this
   remains an open execution step, not performed.)
2. **Final distribution name: `xyz-strata`** (not `strata`, not
   `strata-v2`) — **done (2026-09-30)**. Matches v1's real, already-registered
   PyPI project — `strata` was unavailable when v1 first published.
   `pyproject.toml`'s `name` field changed from `strata-v2` → `xyz-strata`;
   `strata.utils.version.PACKAGE_NAME` (the *import* package name) stays
   `strata` — only the distribution name changed, confirmed by a real
   `uv build` producing `xyz_strata-2.0.0a1-*` artifacts and
   `test_version_survives_a_distribution_rename` passing unchanged.
3. **PyPI trusted publishing: assumed already configured** for `xyz-strata`
   (v1 already publishes there via a same-path `ci-release.yml`) — **must be
   verified against pypi.org's actual trusted-publisher settings for the
   `xyz-strata` project before the first tag push**, not assumed blind. If
   the trusted-publisher entry is scoped to a specific workflow filename that
   differs, or to `main` only, it will need a second entry added for
   publishing from `v2`.
4. **Docker `latest` gating: done (2026-09-30).** `ci-release.yml`'s Docker
   metadata step no longer applies `type=raw,value=latest` unconditionally —
   gated to stable (non-pre-release) semver tags only
   (`type=raw,value=latest,enable=${{ !contains(github.ref_name, '-') }}`,
   applied to both the GHCR and Docker Hub jobs), so `v2.0.0-alpha1` cannot
   overwrite the `latest` image tag real consumers pull.
5. **GitHub Release prerelease flag: done (2026-09-30).** Wired
   `softprops/action-gh-release`'s `prerelease:` input, auto-detected from
   the tag: `prerelease: ${{ contains(github.ref_name, '-') }}` — true for
   `-alpha`/`-beta`/`-rc` tags, false for a stable `vX.Y.Z` tag.
6. **`VERSION.txt` normalization: done (2026-09-30).** Changed from
   `2.0.0-Alpha` to `2.0.0-Alpha1`, which `packaging` normalizes to
   canonical PEP 440 `2.0.0a1` (confirmed directly:
   `Version('2.0.0-Alpha1') == Version('2.0.0a1')`, and `strata version`/
   a real `uv build` both now report `2.0.0a1`, not the unnumbered `a0`
   default). **Caveat found while verifying this**: the dev venv's
   editable-install metadata does not update just from editing
   `VERSION.txt` — needs an actual reinstall/resync
   (`strata.utils.version`'s own documented "sharp edge"). Also found and
   cleaned up two genuinely stale `src/*.egg-info` directories (leftover
   from a pre-uv install, under the old `strata_v2` name) that were
   causing `importlib.metadata.packages_distributions()` to return
   multiple, ambiguously-ordered entries for the same import package —
   not a new problem this change created, but one this change's own
   verification step surfaced and fixed.
7. **Merge-to-`main` criterion: v2 reaches beta.** `v2` merges into `main`
   (becoming the real, released line — superseding v1) the moment v2 can
   stand in for v1 against both real consumers tracked in
   [`v1-consumer-usage`](../../.github/copilot-instructions.md) repo memory —
   **haven** and **config-deploy** — i.e. `strata build run` +
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
  [build-command.md](build-command.md)), `deploy run`'s
  confirmed `store: artifact` gap (see
  [deploy-command.md](../design/deploy-command.md) Remaining Work item 8), and the
  Tier 2 commands (`init`/`repo`/`profile`/`config`/`audit`/`tools`) starting
  to land.

### Stage 2 — `v2.0.0-betaN` (merge `v2` → `main` here, per Decision 7)

**Entry bar (all required, per Decision 7):** `strata build run` +
`strata deploy run` work end-to-end against **both** haven's and
config-deploy's real deployment files — not just `config/`'s shipped
example or synthetic tests. Concretely, before the first beta:

1. `.v2-haven/` (already migrated per repo memory, 2026-09-28) needs a real
   `build run` + `deploy run --dry-run` pass — today it has only been
   proven against `validate`.
2. An equivalent `.v2-cfgint/` fixture needs to be created the same way
   (hand-migrated from config-deploy's real config) — does not exist
   yet.
3. Build output rendering's remaining phases — token substitution, the
   Jinja2 escape hatch, Compose/Helm rendering, `modules`/`tenant`
   categories — land (see [build-command.md](build-command.md)
   for current phase status).
4. `deploy run`'s confirmed `store: artifact`-at-deploy-time gap closes (see
   [deploy-command.md](../design/deploy-command.md) Remaining Work). Locking,
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

**Entry bar:** beta is feature-complete for the haven/config-deploy
bar above; only bugfixes land from here, no new capability. Requires an
actual trial run against one real consumer's real CI (not just the fixture
copies) — e.g. a side-by-side/opt-in run of haven's or config-deploy's
real pipeline against a `v2.0.0-beta*` build, not merely the local fixture.
**Exit bar:** no P0/P1 bug found during the trial period (length TBD —
revisit once beta is actually reached; premature to fix a number now).

### Stage 4 — `v2.0.0` (stable)

**Entry bar:** RC trial period clean; `docs/work/gap_fit_v1.md`'s open gaps are
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
   "can deploy both haven and config-deploy." Still genuinely open:
   what happens to v1's moving major tag (`v0`) and its existing stable
   `xyz-strata` release line at that point — does `main` post-merge start
   cutting `v2.x` stable releases immediately, or does a `v1` maintenance
   branch get cut off first so v1 patches remain possible after the merge?
   Revisit once beta is actually in sight (`build run`/`deploy run` both
   proven against haven and config-deploy) — per repo memory, `build
   run` is the current next step, `deploy run` after that.

## Changelog

- 2026-10-25: **Built RC1** - bumped `VERSION.txt` from `2.0.0-Alpha27` to `2.0.0-RC1` (normalizes to `2.0.0rc1`, a
  PEP 440 pre-release; confirmed via `packaging.version.Version` first; no trailing newline, as before). **A maintainer
  decision that skips this doc's own gates:** no beta was cut, Stage 2's entry bar is not met (`.v2-haven` and cfg-int
  `origin/v2` build, but `deploy run` against both is unproven), and `v2` is not merged into `main`, so this RC is cut
  from `v2-beta`, not `main`. Stage 4's entry bar (clean RC trial, gap_fit_v1 resolved, v1-maintenance decision) still
  stands before a plain `v2.0.0`. Asked first, and the pre-release suffix was chosen deliberately: a `v2.0.0-rc1` tag
  is a GitHub pre-release, PyPI marks it pre-release (an unpinned `pip install xyz-strata` keeps resolving to v1),
  Docker `latest` stays put, and the Marketplace job is skipped. The VS Code extension, now `xyz-strata` on v1's
  listing, packages as `2.0.0` (ci-build strips the suffix) but is only published by a stable tag. Full check suite
  green: mypy (160 files), ruff check, ruff format, import-linter, pytest (3095 passed), Sphinx with `-W`, `config/`
  validates (32 documents, 3 warnings). `uv sync --reinstall-package xyz-strata` then `uv sync --all-extras` refreshed
  the dev install; `strata version` reported `2.0.0rc1`. `uv build` produced `xyz_strata-2.0.0rc1-py3-none-any.whl`/
  `.tar.gz`; a fresh isolated venv confirmed `strata version` -> `2.0.0rc1`, the command list (`audit`, `build`,
  `deploy`, `graph`, `path`, `promote`, `sln`, `validate`, `values`, `version`), `strata validate` on `config/`, and
  that the agent-skill template ships in the wheel. Verification venv removed after. The commit and the
  `v2.0.0-rc1` tag are left to the maintainer.
- 2026-10-21: **Built alpha26** — bumped `VERSION.txt` to `2.0.0-Alpha26` (normalizes to `2.0.0a26`;
  confirmed via `packaging.version.Version` first; the file has no trailing newline, as before), cut after
  the Helm/OCI chart work ([helm-chart-deployment.md](../design/helm-chart-deployment.md)) so haven can be converted onto it.
  Behaviour changes for its notes: a pinned `chart_version`/`pins.charts` now
  reaches `helm` as `--version` (a chart that used to float to the latest release installs the pinned one); a `helm lint`
  `[ERROR]` on a built local chart now fails `deploy check`/`plan`/`run` before anything is deployed; HTTP chart repositories
  are registered with `--force-update` under a new, collision-free alias (old aliases on a machine are simply unused); for a
  `helm`/`oci` remote a mis-declared integration (unknown, not `sources`, disabled, a method other than `token`/`cli`, or a
  token without `username`) is now a `strata validate` error. New: a private chart registry (ProGet, Nexus, ACR, ...)
  authenticates through the remote's integration ([chart-registries.md](../integrations/chart-registries.md)), and a
  `configuration` key a local chart's `values.yaml` does not declare is a build warning. Beta not considered in this bump.
  Full check suite green: mypy (160 files), ruff check, ruff format, import-linter, pytest (2988 passed), Sphinx docs
  build with `-W`, `config/` validates (32 documents, 3 warnings). `uv sync --reinstall-package xyz-strata
  --index-strategy unsafe-best-match` then `uv sync --all-extras --index-strategy unsafe-best-match` refreshed the dev
  install (the stale-dist-info sharp edge again; the docs extras restored), and `strata version` then reported
  `2.0.0a26`. `uv build --index-strategy unsafe-best-match` produced `xyz_strata-2.0.0a26-py3-none-any.whl`/`.tar.gz`;
  installed into a fresh isolated `uv venv`, confirmed `strata version` → `2.0.0a26`, `strata --help`'s command list
  (`audit`, `build`, `deploy`, `graph`, `path`, `promote`, `sln`, `validate`, `values`, `version`; `deploy` has `apply`,
  `check`, `plan`, `run`, `setup`), `strata validate` against `config/`, and that the new Helm code imports from the wheel.
  Verification venv removed after. The commit and the `v2.0.0-alpha26` tag are left to the maintainer.
- 2026-10-20: **Built alpha25** — bumped `VERSION.txt` to `2.0.0-Alpha25` (normalizes to `2.0.0a25`;
  confirmed via `packaging.version.Version` first; the file has no trailing newline, as before), cut after
  the per-customer repositories work ([remotes-at-scale.md](remotes-at-scale.md)): asset `location`
  credentials for every build-time clone with the new `strata validate` checks, a 300-customer scale test
  that found and fixed a quadratic `DocumentIndex.names_of()`, loading with libyaml's safe loader (about 2x
  faster `open_solution`), and the selective-promotion design (not built). Beta not considered in this bump.
  Full check suite green: mypy (160 files), ruff check, ruff format, import-linter, pytest (2914 passed),
  Sphinx docs build with `-W`. Hit the stale-dist-info sharp edge again (expected on every version-string
  change): `uv sync --reinstall-package xyz-strata --index-strategy unsafe-best-match` dropped the docs
  extras as documented, `uv sync --all-extras --index-strategy unsafe-best-match` restored them, and
  `strata version` then reported `2.0.0a25`. `uv build --index-strategy unsafe-best-match` produced
  `xyz_strata-2.0.0a25-py3-none-any.whl`/`.tar.gz`; installed into a fresh isolated `uv venv`, confirmed
  `strata version` → `2.0.0a25`, `strata --help`'s command list, and `strata validate` against `config/`
  (32 documents, 3 warnings). Verification venv removed after. The bump commit is tagged
  `v2.0.0-alpha25`.
- 2026-10-16: **Built alpha24** — bumped `VERSION.txt` to `2.0.0-Alpha24`
  (normalizes to `2.0.0a24`; confirmed via `packaging.version.Version`
  first), cut after the `deploy setup`/`check`/`plan`/`apply` pipeline-
  stage-commands feature (6 implementation phases, additive on top of
  `deploy run` — see
  [deploy-pipeline-stage-commands.md](../design/deploy-pipeline-stage-commands.md))
  and a docs consolidation pass that merged `deploy-command.md`/
  `deploy-destroy-command.md`/`deploy-operational-commands.md` into one
  new [deploy-command.md](../design/deploy-command.md). Considered
  bumping straight to beta1 instead — checked this doc's own Decision 7
  bar first: not met (same gaps as every prior alpha's check — `.v2-haven`/
  `.v2-cfg` only proven against `validate`, not both `build run`/`deploy
  run` end to end against both real consumers, and `v2-beta`/`main`
  haven't been merged) — bumped the next alpha instead. Also fixed a
  real, pre-existing (unrelated) Sphinx docs-build failure found while
  running `scripts/Check.ps1`: `docs/config/attributeconfig.md` was
  never added to `docs/index.rst`'s toctree (left over from an earlier
  session's attributeconfig feature) — added it, confirmed a clean
  `sphinx-build -W --keep-going` with exit code 0 afterward. Full check
  suite green: mypy (159 files), ruff check, ruff format, import-linter,
  pytest (2670 passed), CLI smoke test, Sphinx docs build. Hit the
  documented stale-dist-info sharp edge again (expected on every
  version-string change) — `uv sync --reinstall-package xyz-strata
  --index-strategy unsafe-best-match` + `uv sync --all-extras
  --index-strategy unsafe-best-match`; green after (`strata version`
  confirmed `2.0.0a24` via a real subprocess call, not just in-process).
  `uv build --index-strategy unsafe-best-match` produced
  `xyz_strata-2.0.0a24-py3-none-any.whl`/`.tar.gz`; installed into a
  fresh isolated `uv venv`, confirmed `strata version` → `2.0.0a24`,
  `strata --help`'s command list, and `strata deploy --help` lists the
  new `setup`/`check`/`plan`/`apply` subcommands (`validate` correctly
  hidden). Verification venv removed after. Tag+push intentionally left
  for explicit user confirmation, same as every prior alpha.
- 2026-10-13: **Built alpha23** — bumped `VERSION.txt` to `2.0.0-Alpha23`
  (normalizes to `2.0.0a23`; confirmed via `packaging.version.Version`
  first), cut after a full gap-tracker review session (`docs/work/
  gap_fit_v1.md` gaps #20/#23/#24 fixed — value-token diagnostic source
  misattribution, `meta` wrongly swept by the Value-token/`${value:...}`
  scanners) and `.v2-cfg`/`.v2-haven` both regenerated and validated
  clean against current schema. Considered bumping straight to beta1
  instead — checked this doc's own Decision 7 entry bar first: not met
  (`.v2-haven`/`.v2-cfg` only proven against `validate`, not `build run`/
  `deploy run --dry-run`; `deploy run`'s `store: artifact` gap still
  confirmed open per `deploy-command.md`) — bumped the next alpha instead,
  same as the 2026-10-06 precedent. Full check suite green: mypy (155
  files), ruff check, ruff format, import-linter, pytest (2526 passed).
  Hit the documented stale-dist-info sharp edge again (expected on every
  version-string change) — `uv sync --reinstall-package xyz-strata
  --index-strategy unsafe-best-match` + `uv sync --all-extras
  --index-strategy unsafe-best-match`; green after. `uv build
  --index-strategy unsafe-best-match` produced
  `xyz_strata-2.0.0a23-py3-none-any.whl`/`.tar.gz`; installed into a
  fresh isolated `uv venv`, confirmed `strata version` → `2.0.0a23` and
  `strata --help` lists the same ten commands. Verification venv removed
  after. Tag+push intentionally left for explicit user confirmation, same
  as every prior alpha — noted separately that this repo's own automated
  commit/push process had already committed `VERSION.txt` directly to
  `v2-beta`/`origin` by the time this was recorded, outside this
  procedure's own tag/push step.
- 2026-10-13: **Built alpha22** — bumped `VERSION.txt` to `2.0.0-Alpha22`
  (normalizes to `2.0.0a22`; confirmed via `packaging.version.Version`
  first), cut to pick up the fixes found while diagnosing why alpha21's
  CI release pipeline failed: `ci-build.yml`'s push/pull_request triggers
  now include `v2-beta` (the real active dev branch — `v2` was stale
  since 2026-10-09), a genuinely pre-existing bug in
  `test_deploy_run_no_longer_collides_on_shared_flat_category_key_names`
  (missing the `shutil.which`/`run_command` mock every sibling test uses —
  only ever passed locally because a real `terraform` binary happens to
  be on PATH), and `docs/config/asset.md` added to `docs/index.rst`'s
  "Configuration Files" toctree (was triggering a Sphinx `toc.not_included`
  warning). Full check suite green via `scripts/Check.ps1
  -SkipDocsBuild`: ruff lint/format, mypy (155 files), import-linter,
  pytest (2522 passed). Hit the documented stale-dist-info sharp edge
  again (expected on every version-string change) — `uv sync
  --reinstall-package xyz-strata --index-strategy unsafe-best-match` +
  `uv sync --all-extras --index-strategy unsafe-best-match`; green after.
  `uv build --index-strategy unsafe-best-match` produced
  `xyz_strata-2.0.0a22-py3-none-any.whl`/`.tar.gz`; installed into a
  fresh isolated `uv venv`, confirmed `strata version` → `2.0.0a22` and
  `strata --help` unchanged. Verification venv removed after. Tag+push
  intentionally left for explicit user confirmation, same as every prior
  alpha.
- 2026-10-13: **Built alpha21** — bumped `VERSION.txt` to `2.0.0-Alpha21`
  (normalizes to `2.0.0a21`; confirmed via `packaging.version.Version`
  first), cut right after the `ProvisioningStepModel.targets` breaking
  schema split (ADR-0039: six kind-specific fields replacing the flat
  `targets` list, plus topology/resource-derived dns/firewall/network
  claiming — see [workspace-execution-model.md](../design/
  workspace-execution-model.md)) and the follow-up `dns_zones`
  Terraform-projection gap fix (`_strata_resources()`/`strata_variables.tf`),
  both landed this session — a real breaking-change unit, matching this
  stage's "cut ad hoc as significant units land" rule. Full check suite
  green via `scripts/Check.ps1 -SkipDocsBuild`: ruff lint, ruff format (1
  file needed reformatting after the deterministic-namespace-ordering
  code-review fix — fixed), mypy (155 files), import-linter, pytest (2522
  passed), CLI smoke test. Hit the documented stale-dist-info sharp edge
  first (`get_version()` still reported `2.0.0a20` right after the
  `VERSION.txt` edit) — `uv sync --reinstall-package xyz-strata
  --index-strategy unsafe-best-match` + a follow-up `uv sync --all-extras
  --index-strategy unsafe-best-match` to restore the dropped `docs` group;
  green after. `uv build --index-strategy unsafe-best-match` produced
  `xyz_strata-2.0.0a21-py3-none-any.whl`/`.tar.gz` (confirmed
  `strata_variables.tf`, including its new `dns_zones` key, is packaged);
  installed into a fresh isolated `uv venv`, confirmed `strata version` →
  `2.0.0a21`, `strata --help` still lists the same ten commands, and
  directly imported `claimed_names()`/`effective_resources_and_
  namespaces()`/`ProvisioningStepModel.resources` from the installed
  wheel (not just the dev tree) to confirm this session's schema change is
  really present in the packaged code. Verification venv removed after.
  Tag+push intentionally left for explicit user confirmation, same as
  every prior alpha.
- 2026-10-11: **Rebuilt alpha20** (same `2.0.0-Alpha20`/`2.0.0a20`, not a
  new version — explicit request to "rebuild," not rebump) after a
  requirements-traceability pass found and fixed a real, silent-wrong-
  value bug in `strata_locals.tf` generation (see unified-terraform-
  projection.md's "Post-Phase-8 correction") that the first `alpha20`
  build (below) did not include. Full check suite green again: mypy,
  ruff check, ruff format, import-linter, pytest (2306 passed — up from
  2293, the new regression tests for this fix). `uv build
  --index-strategy unsafe-best-match` produced a fresh `xyz_strata-
  2.0.0a20-py3-none-any.whl`/`.tar.gz`, overwriting the first build's
  artifacts; installed into a fresh isolated `uv venv`, confirmed
  `strata version` → `2.0.0a20`, `strata --help` unchanged, AND directly
  imported `build_strata_locals()` from the installed wheel (not just the
  dev tree) to confirm the fix's exact backslash-count behavior is really
  present in the packaged code, not just the source. Verification venv
  removed after. **Known sharp edge**, confirmed once more: the dev
  venv's own editable install needed no reinstall this time (`VERSION.txt`
  itself didn't change, only source content — editable installs pick that
  up live; the stale-dist-info edge only bites when the version *string*
  changes).
- 2026-10-11: **Built alpha20** — bumped `VERSION.txt` to `2.0.0-Alpha20`
  (normalizes to `2.0.0a20`; confirmed via `packaging.version.Version`
  first), deliberately skipping `alpha10`-`alpha19` to leave room for
  bugfix alphas on the unified-terraform-projection (ADR-0035) work
  landed as `alpha9`, per explicit request. Full check suite green: mypy
  (151 files), ruff check, ruff format (2 files needed reformatting after
  Phase 7/8's edits — fixed), import-linter, pytest (2293 passed — hit
  the documented stale-dist-info sharp edge first, same as alpha9;
  `uv sync --reinstall-package xyz-strata --index-strategy
  unsafe-best-match` + a follow-up `uv sync --all-extras
  --index-strategy unsafe-best-match` to restore the dropped `docs`
  group; green after). `uv build --index-strategy unsafe-best-match`
  produced `xyz_strata-2.0.0a20-py3-none-any.whl`/`.tar.gz` (confirmed
  `strata_variables.tf`, Phase 5's packaged template, is included);
  installed into a fresh isolated `uv venv`, confirmed `strata version`
  → `2.0.0a20` and `strata --help` still lists the same nine commands.
  Verification venv removed after. Tag+push intentionally left for
  explicit user confirmation, same as every prior alpha.
- 2026-09-30: Initial draft — captured current state and the decisions made
  in this session (branch = `v2` direct, name = `xyz-strata`, Docker `latest`
  gating needed, GitHub prerelease flag needed, `VERSION.txt` → `2.0.0a1`).
  No files changed yet; this doc is the plan only.
- 2026-09-30: Added Decision 7 — `v2` merges into `main` at beta, defined as
  the moment `strata build run` + `strata deploy run` can stand in for v1
  against both real tracked consumers (haven, config-deploy), not on a
  fixed calendar/feature-count basis. Left the v1-maintenance-branch
  question open (item 6) since it only matters once beta is actually near.
- 2026-09-30: Added `## Release Plan` — the 4-stage version-line roadmap
  (alpha on `v2` → beta merges to `main` → rc → stable), with each stage's
  entry/exit bar spelled out concretely rather than left as a date. Flagged
  a real git constraint found while writing it: the `v2` branch must be
  deleted before a `v2` moving major tag can exist (branches/tags share a
  namespace) — sequencing note for the eventual beta cutover.
- 2026-09-30: **Executed Decisions 2, 4, 5, 6**, per direct request
  ("normalize version and prepare for a v2 alpha release") — all local,
  reversible file edits, no tag pushed, no CI triggered, no publish. Changed
  `VERSION.txt` to `2.0.0-Alpha1` (normalizes to `2.0.0a1`, confirmed via
  `packaging.version.Version` directly before committing to it) and
  `pyproject.toml`'s `name` to `xyz-strata`. Wired `ci-release.yml`'s
  `prerelease:` input and both Docker jobs' `latest` gating, both keyed off
  `contains(github.ref_name, '-')`. Verified end-to-end: `uv build` (with
  `--index-strategy unsafe-best-match`, the known Acme-feed quirk) produces
  `xyz_strata-2.0.0a1-py3-none-any.whl`; installed into a fresh, isolated
  `uv venv` and confirmed `strata version` → `2.0.0a1` and `strata --help`
  lists all six commands. Found and fixed an unrelated pre-existing
  environment issue while verifying `VERSION.txt`'s change against the dev
  venv: two stale `src/*.egg-info` directories (leftover from an old,
  pre-uv `strata_v2`-named install) made `importlib.metadata.
  packages_distributions()` return multiple, ambiguously-ordered entries
  for the `strata` import package, which could non-deterministically
  resolve to a stale cached version — removed both; full check suite green
  after (mypy 121 files, ruff, import-linter, pytest 1645 passed, same one
  pre-existing unrelated failure as every prior run this session).
  Still open: Decision 1 (tag push itself), Decision 3 (verify PyPI
  trusted-publisher settings for `xyz-strata` before that push), and
  porting `scripts/Release.ps1` — all deliberately deferred as separate,
  later, harder-to-reverse steps.
- 2026-10-01: **Built alpha2** — bumped `VERSION.txt` to `2.0.0-Alpha2`
  (normalizes to `2.0.0a2`). `uv build --index-strategy unsafe-best-match`
  produced `xyz_strata-2.0.0a2-py3-none-any.whl`; installed into a fresh
  isolated `uv venv`, confirmed `strata version` → `2.0.0a2` and `strata
  --help` lists all six commands. Local/reversible only — no tag pushed, no
  CI triggered, no publish; Decisions 1/3 and `scripts/Release.ps1` remain
  the same still-open items as before.
- 2026-10-05: **Built alpha6** — bumped `VERSION.txt` to `2.0.0-Alpha6`
  (normalizes to `2.0.0a6`; confirmed via `packaging.version.Version`
  before committing to it, same verification as every prior bump). Note:
  alpha3-5 were bumped in earlier sessions without a matching changelog
  entry here — not re-created retroactively, just flagged so the gap in
  this log isn't mistaken for alpha3-5 never having existed.
- 2026-10-05: **Built alpha7** — bumped `VERSION.txt` to `2.0.0-Alpha7`
  (normalizes to `2.0.0a7`; confirmed via `packaging.version.Version`
  before committing to it). Full check suite green (mypy 139 files, ruff,
  import-linter, pytest 1958 passed) after fixing one unrelated,
  pre-existing `mypy` failure surfaced by this run —
  `src/strata/logging/otel.py`'s `# type: ignore[no-any-return]` on
  `attach_otlp()`'s return had gone stale (an installed dependency now
  types `LoggingHandler` correctly) and tripped `warn_unused_ignores`;
  removed the dead comment, no behavior change. `uv build
  --index-strategy unsafe-best-match` produced
  `xyz_strata-2.0.0a7-py3-none-any.whl`; installed into a fresh isolated
  `uv venv`, confirmed `strata version` → `2.0.0a7` and `strata --help`
  lists all eight commands. Local/reversible only — no tag pushed yet, no
  CI triggered, no publish.
  **Caveat hit again, same "sharp edge" `version.py`'s own docstring
  documents:** a plain `uv sync` did not regenerate the editable install's
  `dist-info` (stayed `xyz_strata-2.0.0a5.dist-info` — confirmed by
  inspecting it directly, and by `get_version()` still reporting `2.0.0a5`
  after the sync) — needed `uv sync --reinstall-package xyz-strata` to
  force the rebuild; worth remembering for the next bump rather than
  re-discovering. Also found that `uv sync` alone (no `--all-extras`)
  silently drops the `docs` optional-dependency group from the dev venv —
  restored with `uv sync --all-extras --index-strategy unsafe-best-match`
  immediately after, an unrelated side-effect of running sync at all, not
  of the version bump itself. Full check suite green post-bump: mypy (139
  files), ruff check, ruff format, import-linter (1 kept, 0 broken),
  pytest (1958 passed). `uv build --index-strategy unsafe-best-match`
  produced `xyz_strata-2.0.0a6-py3-none-any.whl`; installed into a fresh
  isolated `uv venv`, confirmed `strata version` → `2.0.0a6` and `strata
  --help` now lists **seven** commands (`audit` added this session,
  alongside the original six). Verification venv removed after; `dist/`
  build artifacts left in place, git-ignored, nothing staged beyond
  `VERSION.txt` itself (confirmed via `git status`). Local/reversible
  only — no tag pushed, no CI triggered, no publish; Decisions 1/3 and
  `scripts/Release.ps1` remain the same still-open items as before.
- 2026-10-06: **Considered, then deferred, jumping straight to
  `v2.0.0-beta1`** — user asked about it directly; checked this doc's own
  Decision 7 entry bar first rather than assuming it was met. It is not:
  `.v2-haven/` is only proven against `validate` (not `build run`/
  `deploy run`), an equivalent `.v2-cfgint/` fixture for config-deploy
  does not exist yet, build output rendering's remaining phases haven't
  landed, and `deploy run`'s `store: artifact`-at-deploy-time gap is still
  open — plus a beta tag requires actually merging `v2` into `main`
  first, a real branch operation, not just a version-string edit. User
  chose to bump the next alpha instead; the beta gate itself is
  unchanged, still gated on the same four items above.
- 2026-10-06: **Built alpha8** — bumped `VERSION.txt` to `2.0.0-Alpha8`
  (normalizes to `2.0.0a8`; confirmed via `packaging.version.Version`
  before committing to it). Forced the dev venv's editable-install
  metadata to pick up the change with `uv sync --reinstall-package
  xyz-strata --all-extras --index-strategy unsafe-best-match` in one
  pass (both documented sharp edges at once — stale dist-info, and a
  plain sync dropping the `docs` extras group) rather than discovering
  either again. Full check suite green via `scripts/Check.ps1` (ruff
  lint, ruff format, mypy 146 files, import-linter, pytest 2170 passed,
  smoke test, Sphinx docs build). `uv build --index-strategy
  unsafe-best-match` produced `xyz_strata-2.0.0a8-py3-none-any.whl`;
  installed into a fresh isolated `uv venv`, confirmed `strata version` →
  `2.0.0a8` and `strata --help` now lists **nine** commands (`promote`
  added this session, alongside `audit` from alpha6). Verification venv
  removed after; `dist/` build artifacts left in place, git-ignored,
  nothing staged beyond `VERSION.txt` itself (confirmed via `git
  status`). Local/reversible only — no tag pushed, no CI triggered, no
  publish; Decisions 1/3 and `scripts/Release.ps1` remain the same
  still-open items as before.
- 2026-10-08: **Built alpha9** — bumped `VERSION.txt` to `2.0.0-Alpha9`
  (normalizes to `2.0.0a9`; confirmed via `packaging.version.Version`
  first). Full check suite green: mypy (151 files), ruff check, ruff
  format --check (289 files), import-linter, pytest (2315 passed — hit
  the documented stale-dist-info sharp edge first, `test_utils_version.py`
  failed reporting `2.0.0a8` until `uv sync --reinstall-package xyz-strata
  --index-strategy unsafe-best-match` + a follow-up `uv sync --all-extras`
  to restore the `docs` group it drops as a side effect; green after).
  `uv build --index-strategy unsafe-best-match` produced
  `xyz_strata-2.0.0a9-py3-none-any.whl`/`.tar.gz`; installed into a fresh
  isolated `uv venv`, confirmed `strata version` → `2.0.0a9` and `strata
  --help` still lists the same nine commands. Verification venv removed
  after. **Found and tagged this as the actual precedent going forward:
  `v2.0.0-alpha5`/`alpha6`/`alpha8` are already real, pushed tags on
  `origin`** (confirmed via `git ls-remote --tags origin`) — Decision 1
  ("tag source branch: `v2` directly") is therefore already being
  exercised in practice, not merely planned; this doc's own "Decisions 1,
  3 ... remain outstanding" framing is stale for Decision 1 specifically
  and should be read as "verified PyPI trusted-publisher settings
  (Decision 3) only" going forward. Local build/verify steps done;
  tag+push intentionally left for explicit user confirmation before
  running (pushing a tag triggers real CI that publishes to PyPI/GHCR/
  Docker Hub — a shared-system action).
