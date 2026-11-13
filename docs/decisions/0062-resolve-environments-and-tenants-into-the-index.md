# Environments and tenants are resolved when the solution loads and replace their documents in the index

- Status: accepted
- Date: 2026-11-13
- Related: [ADR-0061](0061-parameter-sets-and-load-time-param-token.md) (parameter sets and `${param:}`, which this
  extends to environments and tenants), [ADR-0024](0024-tenant-defaults-merge.md) (tenant defaults, which now read the
  resolved tenant), [docs/design/parameter-sets.md](../design/parameter-sets.md) (the design)

## Context and Problem Statement

Environments and tenants gain `extends`, `partial` and `meta.params`, as deployments have. A leaf's real content is
then spread over its chain and its parameter sets, and only the merged document is what strata should use.

Deployments are resolved into a separate map that their readers ask for (`resolve_deployments()`,
`deployment_of()`). Environments and tenants are read in many more places: value assembly for `build run` and
`deploy run`, `strata values`, the per-deployment token checks, the geography checks, tenant defaults on
deployments, `${value:}`, attributeconfig, reachability. Each reads `index.get(kind, name)` today.

Where should the resolved environment or tenant live, and when should it be made?

## Considered Options

- **A resolved map per kind**, as for deployments, passed to or recomputed by every reader.
- **Resolve when the solution loads, and replace the document in the index.**
- **Resolve lazily on each read** (a resolving `get` for these kinds).

## Decision Outcome

Chosen: **resolve when the solution loads, and replace the document in the index**, because every reader then sees
the merged document without a change of its own, and none can forget to ask for it.

- Resolution runs in `open_solution()`, right after loading and before anything reads the index, in the order
  parameter sets, environments, tenants; deployments are resolved later, against the resolved tenants.
- `DocumentIndex.replace()` is the only way to replace an entry, and only resolution uses it.
- Every chain is resolved against the documents as written before any is replaced, and the entry keeps the
  document as written (`IndexEntry.written`): chain walks and `param_unused` need it once a leaf's `extends` and
  tokens are gone from the merged model.
- A resolution problem (a cycle, a missing value, a merged document the model rejects, a `name_rule` mismatch) is a
  load finding, like a schema failure, so the cross-document checks do not run on top of it. A missing base is left
  to the reference check.
- A `partial: true` environment or tenant stays as written, so `extends` can find it, and every check that lists,
  selects or reads a document rejects it.
- Deployments keep their separate resolved map: they are few readers' concern, and resolving them needs the
  resolved tenants.

### Consequences

- Good: about fifteen readers needed no change; a reader added later is right by default.
- Good: one resolution per load, not one per reader or per read.
- Bad: a finding about a merged environment or tenant names the leaf's file, not the base that wrote the field;
  the rendered view (`strata render`) shows where each value came from.
- Bad: the index no longer holds only documents as written; code that needs the written form reads `as_written`.
- Bad: two resolution points (load for environments and tenants, a map for deployments) to keep in mind.
- Neutral: a resolving `get` was not chosen because every read would redo or cache the work, and a resolution
  failure would surface wherever the first reader happened to be.
