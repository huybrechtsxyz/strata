# Remotes — Design

- Status: draft — schema exists (`SolutionRemoteModel`, `@repo/` parsing);
  no resolution/fetch mechanism exists at all yet.
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

**Nothing turns a remote name into an actual filesystem path yet.** This is
the missing primitive underneath `sync_source()`
([build-command.md](build-command.md)'s D3) and every other `@repo/...`
reference in the schema — not a `strata build run`-specific problem, a
solution-loading-layer one shared by everything that names a remote.

## Current Design

### What exists

- **`SolutionRemoteModel`** (`solution_model.py`) — the full schema:
  `name`, `type: RemoteType` (`git`/`oci`/`helm`/`local`), `url`,
  `reference` (required for git/oci, rejected for helm/local),
  `fetch: RemoteFetch` (`strata` default vs `external`), `integration`
  (credentials, must declare the `sources` capability).
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

### What does not exist (checked directly, not assumed)

- No function resolves a remote `name` to a real filesystem path, for any
  `type`. Not even the trivial `type: local` case (a solution-relative
  path, no fetch needed at all) has a resolver.
- No git clone/fetch mechanism, no OCI pull mechanism.
- No local cache/checkout convention — v1 had a `deploy_path` field on the
  remote itself for this; v2 deliberately dropped it as "CLI convention,
  not declarative schema" (`solution_model.py`'s own docstring), which
  means v2 needs to decide this convention somewhere, just not on the model.
- No `SourceIntegration`/equivalent ABC for the `"sources"` capability, so
  `integration:`-declared credentials on a remote have nothing to bind to.
- No handling for `fetch: external` (CI/developer already checked the repo
  out) — there is currently no way to tell strata *where* an externally
  fetched repo landed, since `deploy_path` was removed and nothing replaced
  it.

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
  this design's most immediate blocked consumer.

## Remaining Work / Open Questions

In dependency order — each step only needs the ones before it:

1. **`type: local` resolution** — trivial, no fetch: a solution-relative
   `url` resolved to an absolute path. Worth building first even though
   it's the least interesting case: it exercises the resolver's calling
   convention (whatever takes a remote name and returns a path) with zero
   git/network/credential complexity, and unblocks tests/examples that
   don't want real git I/O.
2. **A local cache/checkout convention** — v2 has no replacement yet for
   v1's removed `deploy_path`. Needs a decision: a fixed
   `.strata/remotes/<name>/` convention, a CLI flag, or something else —
   and how `fetch: external` tells strata where a CI-provided checkout
   already lives, now that there is no schema field for it.
3. **`type: git`, `fetch: strata`** — actual `git clone`/`git fetch` at
   `reference`, into the cache convention from (2). Real design questions:
   shallow vs. full clone, re-fetch/staleness policy across repeated `build
   run` invocations, and error surfacing (auth failure vs. ref not found vs.
   network failure need to be distinguishable, matching this codebase's
   general "distinguish failure kinds" discipline — see
   `value_controller.py`'s "not declared vs. resolution failed" precedent).
4. **`SourceIntegration` ABC (or equivalent)** for the `"sources"`
   capability — the credential-supplying half (`remote.integration`).
   Needed before (3) can authenticate against a private repo, though (3)
   can be built/tested against public repos first.
5. **`type: oci`** — pull mechanism, likely much later than git given zero
   confirmed real usage of `type: oci` in any checked workspace so far
   (same "don't build ahead of evidence" discipline as ADR-0023's deferred
   categories) — revisit once a real remote declares it.
6. **Wire into `sync_source()`** (`build-command.md`'s D3) once (1)-(3)
   exist for the types actually in use.

No done-when target dates — this doc tracks a prerequisite, not a
scheduled phase; update it as each numbered item above lands.

## Changelog

- 2026-09-24: Created. Written while scoping `strata build run`'s
  `sync_source()` dependency (build-command.md) and finding its own
  prerequisite — remote-to-filesystem-path resolution — does not exist at
  any level, not just for `sync_source()` specifically.
