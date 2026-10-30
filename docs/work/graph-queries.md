# Querying the solution graph - Work

- Status: draft
- Last updated: 2026-10-30

## Overview

`strata graph` shows the cross-document references of a solution. It is the closest thing strata has to
a queryable model of the solution itself: what exists, and what points at what. Two readers want more
from it than it gives today:

- A person (or the VS Code Graph view) asking "what uses this artifact?", "which deployments does this
  version document affect?", "what does this deployment reach?", "what is unreferenced?".
- An AI asked to explain or change a solution. The schema export
  ([schema-export.md](../design/schema-export.md)) tells it what a document *can* contain. The
  graph is the other half: how the documents of this solution *actually* relate. Without it an AI has
  to read every file and guess the links.

Today the graph answers neither question reliably, because it is incomplete (see [Current state](#current-state)).
This doc designs completing it and querying it. Not in `docs/design/` because nothing is built.

## Current state

Verified against the source on 2026-10-30.

- `strata graph [PATH]` is a flat command, not a group. It lists edges grouped by source document, in
  index order. On `config/`: 30 edges across 32 documents.
- Edges come only from fields annotated `References(kind)`, through `extract_references()` and
  `references_in()` (`graph_controller.build_reference_graph`). The same walk drives
  `validate_references`, so the two cannot drift.
- It is schema-only (`open_solution`, no `require_valid()`), always exits 0, and shows dangling
  references as `resolved: false` instead of failing.
- JSON: `data.edges[]` with `source_kind`, `source_name`, `source_path`, `location`, `target_kind`,
  `target_name`, `resolved`. The VS Code Graph view renders this and nothing else
  (`strataClient.ts` calls `strata graph [path]`).

### Gaps

1. **Conditional references have no edge.** A field is a reference only when a sibling says so, so it
   cannot carry `References()`. Each is validated by a dedicated check instead:
   - `store: artifact` variable `value` -> artifact (the environment declares it, so it is also what
     links a deployment to an artifact)
   - `kind: version` pins, keyed by module service, module, remote, artifact or asset name
   - `${value:kind.name.path}` tokens in any string field
   - `Version.spec.workspace` is a plain reference, but the check that every referrer agrees on it is
     not visible as a relationship
2. **Same-document references have no edge.** Step `depends_on`, a step's `provisioner`, and a
   provisioner's `depends_on` name things inside one document. Whether the graph should go below
   document granularity is open.
3. **Nodes with no edges are invisible.** The graph is a list of edges, so an artifact, version or
   environment that nothing references never appears. Dead configuration cannot be found.
4. **No queries.** The only filter is "everything". There is no "who points at X", "what does X
   point at", or transitive closure.
5. **Remote references are omitted** on purpose (a remote is a manifest entry, not a document).

## Current Design / Progress

Nothing is built. Proposed decisions:

- **G1 - Edges carry how they were found.** Add `via` to `GraphEdge`: `reference` (an annotated field,
  today's edges), `store` (a `store: artifact` variable), `pin` (a version pin), `token` (a
  `${value:}` token). Additive JSON: the Graph view keeps working, and can colour by `via`.
- **G2 - Conditional edges reuse the existing checks' walks.** The dedicated checks already find these
  references to validate them (`_check_environments` in `semantic_checks.py`, `check_version_pins` in
  `version_pins.py`, `resolve_document_value_references` in `value_references.py`). Edge building calls
  the same lookups instead of a second walk, for the reason `graph_controller.py` already gives.
- **G3 - A derived edge from a deployment to what it reaches stays a query, not a stored edge.**
  Deployment -> environment -> artifact crosses `extends` and tenant defaults, which
  `resolve_deployment_chains()` already folds. Store direct edges only. "What does deployment X reach"
  is computed over them plus that resolution.
- **G4 - Queries are flags on the existing command, not subcommands.** `strata graph` takes a
  positional `PATH` that the extension passes, so turning it into a group would break it. Add, all
  optional and combinable:
  - `--to kind[/name]`: edges pointing at it
  - `--from kind[/name]`: edges leaving it
  - `--depth N` with `--from`/`--to`: follow edges N levels (default 1)
  - `--orphans`: documents nothing references (needs nodes, see G5)
  - `--via reference|store|pin|token`: restrict by edge type
- **G5 - Output gains nodes.** `data.nodes[]` (`kind`, `name`, `source_path`, `in`, `out` counts) beside
  `data.edges[]`, so an orphan is visible and a consumer does not have to infer the node set from edges.
  Additive.
- **G6 - Stays schema-only and exit 0.** A graph of what points at what is meaningful even when another
  document is broken, and an AI exploring a half-written solution needs it most then. Same contract as
  today.
- **G7 - Deployment attributes and effective values are not graph facts.** `deploy list` (tenant,
  version, ring, filters) and `deploy images` (the tag after a pin, where it came from) report
  attributes and derived values. See [effective-images.md](effective-images.md). The graph answers
  relationships.
- **G8 - Subcommands (`graph nodes`, `graph edges`, `graph reach`) only with a deliberate break.** The
  positional `PATH` would become a subcommand name. If wanted, do it before `2.0.0`, together with the
  extension, and use `--path` like every other command.

### For AI readers

- The JSON envelope is already the stable contract (`{ok, command, version, context, summary,
  diagnostics, data}`); the extension relies on it.
- `--to`/`--from`/`--depth` keep a query small, so a model asks "what uses artifact datahub" and gets a
  few edges instead of the whole graph.
- Absolute `source_path` values tie an edge to a file. Consider also emitting the path relative to the
  solution root, which is stable across machines and what an AI should quote.
- Open question: whether the agent skill should teach these queries. It would be generated from the
  command's options, like the kind table in the schema-export work.

## Related Decisions

- [ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md) - references are
  `(kind, meta.name)`, never paths
- [ADR-0019](../decisions/0019-version-pinning.md) - version pins
- [ADR-0026](../decisions/0026-pinnable-artifact-references.md) - `store: artifact`

## Remaining Work / Open Questions

### Implementation plan

- [ ] 1. `via` on `GraphEdge`, `reference` only; update the Graph view type and tests
- [ ] 2. Conditional edges: `store` first (it links deployments to artifacts), then `pin`, then `token`
- [ ] 3. `data.nodes[]` with in/out counts, and `--orphans`
- [ ] 4. `--from`, `--to`, `--depth`, `--via`
- [ ] 5. Graph view: colour or group by `via`; no new CLI calls
- [ ] 6. Docs and the agent skill: the queries, and `via` meanings

### Open questions

- Is `Version.spec.workspace` (a plain reference) enough, or should the "every referrer agrees" rule
  also appear as an edge?
- Do same-document references (step `depends_on`) belong in the graph? It means nodes below document
  level, with ids like `workspace/main#step/plan`.
- `--depth` on a graph that crosses `extends`: follow the raw edge, or the resolved one?
- Should `--orphans` ignore kinds that are roots by design (solution-level documents, a deployment
  nothing extends)?
- Group conversion (G8): before `2.0.0`, or never?

### Release fit

Completing the edges and adding queries is new capability, so it belongs in 2.0.x or 2.1, not rc2.
Steps 1 and 3 are small and additive and could go first.

## Changelog

- 2026-10-30: created. Findings from reading `graph_controller.py` and `graph_command.py`, and from the
  `deploy list` / `deploy images` discussion in [effective-images.md](effective-images.md).
