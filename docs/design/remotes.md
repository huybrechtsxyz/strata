# Remotes — Design

- Status: current — `resolve_remote()` implemented for `local` and `git`
  remotes (both `fetch: strata` and `fetch: external`); `oci`/`helm` and
  credentialed (private-repo) fetches not yet built.
- Last updated: 2026-09-24

## Overview

A **remote** (`SolutionRemoteModel`, `solution_model.py`) declares *identity
and location only* for an external artifact source — name, type (git/oci/
helm/local), URL, pinned ref, who fetches it (`strata` vs `external`/CI),
and which `Integration` supplies credentials. Every `@<name>/<path>`
reference in the schema (`SourceModel.remote` on a provisioner's source,
`ModuleFileModel.source`'s `@repo/...` syntax) selects *what* to take from a
remote; the remote itself is declared exactly once, in `strata.yaml`'s
`spec.remotes`.

**Nothing turned a remote name into an actual filesystem path before this**
- the missing primitive underneath `sync_source()`
([build-command.md](build-command.md)'s D3) and every other `@repo/...`
reference in the schema. `strata.controllers.remote_resolution.resolve_remote()`
now fills this in for `local` and `git` remotes.

## Current Design

### What exists

- **`SolutionRemoteModel`** (`solution_model.py`) — the full schema:
  `name`, `type: RemoteType` (`git`/`oci`/`helm`/`local`), `url`,
  `reference` (required for git/oci, rejected for helm/local),
  `fetch: RemoteFetch` (`strata` default vs `external`), `integration`
  (credentials, must declare the `sources` capability).
- **`type`/`fetch` are two orthogonal fields on purpose, already grounded in
  real evidence** — checked `RemoteFetch`'s own docstring, which quotes the
  exact production case this doc separately found in
  `cfg-int-deployment`'s real `config/remotes.yaml`: a git repository
  declared `type: bundled` (v1's single conflated enum) purely so v1 would
  skip its own fetch in a CI environment with no git credentials — silently
  losing ref-pinning and dirty-tree gating as a side effect of the type
  lie. `type: git` + `fetch: external` states the same real case honestly.
  No realignment needed here — a mistaken first instinct while scoping this
  doc, corrected by actually reading the model's own docstring before
  changing it.
- **`strata.utils.layout.remote_checkout_path(root, remote, reference)`**
  — already exists, already a *pure function* of already-known values
  (solution root + remote name + resolved ref), so the checkout location
  never needs a stored side-file. Its own module docstring records the
  exact v1 bug this avoids: a remote's checkout location stored in *both*
  `config/remotes.yaml` and `.strata/solution.json`, which humans had to
  keep equal by hand — when they diverged, builds failed with "has not
  been fetched yet" against a repo that WAS fetched, just at a different
  path. A missed find during this doc's first draft, which had proposed
  inventing a cache convention that already existed.
- **`strata.utils.repo_refs`** — pure detection/parsing only:
  `is_cross_repo_ref()`/`split_repo_ref()` recognise and split an
  `@repo_name/relative/path` string into `{repo_name, rest}`. Explicitly
  does not validate the repo name against anything real — "that is a
  resolution concern for a future solution-loading layer" (the module's own
  docstring).
- **`CAPABILITY_ABCS`** (`integrations/capabilities.py`) already reserves
  the concept: `"sources"` is a recognised capability string with
  deliberately **no ABC entry yet** (ADR-0021 D9) — declarable on an
  `Integration` document, but nothing dispatches to it, because there is no
  `SourceIntegration` ABC (no `fetch()`/`resolve()` contract defined) and no
  caller that would invoke one.
- **`strata.controllers.remote_resolution.resolve_remote(root, remote)`**
  — implemented:
  - `remote=None` → `root` (unset means "this solution's own repository",
    ADR-0018).
  - `type: local` → `(root / remote.url).resolve()` directly, never
    materialised under `.strata/remotes/` (matches the real `config`
    remote in `cfg-int-deployment`'s `remotes.yaml`: `url: "."`, no fetch).
  - `type: git`, checkout already exists at `remote_checkout_path(...)` →
    reused as-is. Ref-keyed path means it can never be stale — a different
    ref gets a different directory automatically, so there is no
    staleness check to write.
  - `type: git`, `fetch: external`, nothing checked out yet → a clear
    `RemoteResolutionError` naming the exact expected path (for CI/a
    developer to place a checkout at).
  - `type: git`, `fetch: strata`, nothing checked out yet → real
    `git clone`/`git checkout <reference>` via `strata.utils.transport.run_command()`
    (no new subprocess machinery — reused directly, the same way
    `TerraformIntegration`/etc. already do). No credential handling — relies
    entirely on whatever git already has configured locally (SSH
    agent/credential helper); a private repo with nothing configured fails
    with git's own error message.
  - `type: oci`/`helm` → a clear `RemoteResolutionError` naming what's
    missing, not a silent no-op or a crash.
  - `RemoteResolutionError` classified as a `SystemError` (not
    `UsageError`/`ValidationError`) — every case here is "the environment
    is not set up as declared", registered in `commands/exit_codes.py`'s
    exhaustiveness-checked table.

### What still does not exist

- No `SourceIntegration`/equivalent ABC for the `"sources"` capability, so
  `integration:`-declared credentials on a remote have nothing to bind to —
  `git clone` only works today against a remote git already has local
  credentials for.
- No `type: oci`/`helm` fetch mechanism.
- Not wired into `sync_source()` yet — that function itself does not exist
  (`build-command.md`'s D3).

### Why this blocks more than just `strata build run`

Every one of these already-real schema fields resolves through the same
missing primitive:

- `ProvisionerModel.source: SourceModel` (a provisioner's `.tf`/Ansible/
  Bicep code location) — `sync_source()`'s direct input.
- `ModuleFileModel.source` (`@repo/path/to/docker-compose.yml`) — Compose/
  Helm's workload pipeline (ADR-0022 D5-D7), independent of the
  provisioner pipeline above.
- Any future consumer of `configuration.yaml`'s own remote-hosted case
  (`solution_model.py`'s docstring: *"Configuration itself can live in a
  remote, so remotes must resolve before Configuration can be loaded"*).

Building remote resolution once, at this layer, is what lets all three
share one implementation instead of `sync_source()` inventing its own
git-clone logic that the workload pipeline then has to duplicate.

## Related Decisions

- [ADR-0021](../decisions/0021-integration-layer.md) D9 — reserved the
  `"sources"` capability string with no ABC yet; this doc is where that ABC
  would finally get defined.
- [ADR-0018](../decisions/0018-source-model-unified-remote-reference.md) —
  `SourceModel` itself (the *selection* side: `remote`/`source_path`/
  `chart_name`/`chart_version`). Implemented, but only as schema/validation
  — no materialization.
- [build-command.md](build-command.md) — `sync_source()` (ADR-0022 D3) is
  this design's most immediate blocked consumer, once it exists.

## Remaining Work / Open Questions

In dependency order — each step only needs the ones before it:

1. ~~`type: local` resolution~~ — done.
2. ~~A local cache/checkout convention~~ — turned out to already exist
   (`layout.remote_checkout_path()`), not built here.
3. ~~`type: git`, `fetch: strata`/`fetch: external`~~ — done
   (`resolve_remote()`). Open sub-question left deliberately unaddressed:
   shallow vs. full clone, and any re-fetch/staleness policy for a *branch*
   reference across repeated invocations (a tag/commit-SHA reference is
   inherently immutable so this only matters for branch refs) — today's
   implementation clones once and never re-fetches an existing checkout,
   which is correct for tags/SHAs but means a branch ref's checkout can go
   stale across long-lived local development. Revisit once that is a
   real, observed problem, not preemptively.
4. **`SourceIntegration` ABC (or equivalent)** for the `"sources"`
   capability — the credential-supplying half (`remote.integration`).
   `resolve_remote()`'s `_git_clone()` currently relies entirely on
   whatever git already has configured locally; a private repo with no
   local git auth fails with git's own error message rather than a
   strata-managed credential injection. Needed before private remotes
   work via `fetch: strata`.
5. **`type: oci`/`helm`** — pull mechanisms, likely much later than git
   given zero confirmed real usage of either in any checked workspace so
   far (same "don't build ahead of evidence" discipline as ADR-0023's
   deferred categories) — revisit once a real remote declares one.
6. **Wire into `sync_source()`** (`build-command.md`'s D3) once that
   function itself exists.

No done-when target dates for items 4-6 — this doc tracks a prerequisite,
not a scheduled phase; update it as each numbered item above lands.

## Changelog

- 2026-09-24: Created. Written while scoping `strata build run`'s
  `sync_source()` dependency (build-command.md) and finding its own
  prerequisite — remote-to-filesystem-path resolution — does not exist at
  any level, not just for `sync_source()` specifically.
- 2026-09-24: Implemented `strata.controllers.remote_resolution.resolve_remote()`
  for `local` and `git` remotes. Corrected two mistaken assumptions from
  the first draft along the way, both found by reading real code/config
  before building rather than after: (1) a cache/checkout convention
  already existed (`layout.remote_checkout_path()`) — the doc had proposed
  designing one from scratch; (2) `RemoteType`'s git/oci/helm/local split
  (vs. v1's bundled/gitops/container) did not need reconciling —
  `RemoteFetch`'s own docstring already cites the exact real
  `cfg-int-deployment` evidence this doc separately found, and the v2
  design is the deliberately-corrected version of it.
