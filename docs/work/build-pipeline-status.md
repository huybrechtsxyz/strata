# Build Pipeline (Integration → Build Run → Output Rendering) — Status

- Status: current
- Last updated: 2026-09-24

## Overview

Tracks cross-ADR status for the three-part build pipeline design: the
Integration layer ([ADR-0021](../decisions/0021-integration-layer.md)),
`strata build run` orchestration ([ADR-0022](../decisions/0022-strata-build-run.md)),
and build output rendering ([ADR-0023](../decisions/0023-build-output-rendering.md)).
These three ADRs describe one continuous system, deliberately split as each
grew large enough to deserve its own document. Read this doc first for
"what's the current state" — then the relevant ADR's own Remaining
Work/Implementation Plan for the specifics; they are not duplicated here.

## Current Status

| Layer | ADR | Status |
| --- | --- | --- |
| Integration layer (capability ABCs, registry, Terraform/Compose/Helm classes) | [ADR-0021](../decisions/0021-integration-layer.md) | **Implemented** — Phases 1-6 all done |
| `strata build run` orchestration (the loop calling `prepare()`/`prepare_namespace()` per provisioner/namespace) | [ADR-0022](../decisions/0022-strata-build-run.md) | Designed, not built — `prepare()`'s signature and `ResolvedWorkspaceGraph` (D1a) exist as part of ADR-0023 Phase 1's work; the orchestrator loop (`build_controller.py`), `resolve_integration()`, `sync_source()`, and the workload pipeline (D5-D7) are not built |
| Build output rendering (default Terraform projection, `OutputProfileModel` rejection, the Jinja2 escape hatch) | [ADR-0023](../decisions/0023-build-output-rendering.md) | Partially implemented — Phase 1's `dns`/`networks` categories done; `modules` (2b) skipped; `tenant` (2c) and Phases 3-5 (token substitution, Jinja2 escape hatch, Compose/Helm rendering) not started |

## Related Decisions

- [ADR-0021](../decisions/0021-integration-layer.md) — Integration layer
- [ADR-0022](../decisions/0022-strata-build-run.md) — build run orchestration
- [ADR-0023](../decisions/0023-build-output-rendering.md) — output rendering

## Remaining Work / Open Questions

Detailed, phase-by-phase remaining work lives in each ADR's own section —
not duplicated here:

- ADR-0022's Remaining Work: orchestrator loop, `resolve_integration()`,
  `sync_source()`, the workload pipeline, CLI wiring.
- ADR-0023's Implementation Plan: Phase 1's remaining categories (modules,
  tenant), Phases 2-5 (token substitution, Jinja2 escape hatch, Compose/Helm
  rendering, shipped examples).
- Open cross-ADR question flagged in ADR-0023: whether `OutputProfileModel`'s
  "zero usage" rejection (D2) needs revisiting once a real foreign `.tf`
  root needing emit-suppression is found — not resolved, deliberately
  deferred.
- v1 parity gaps found by the 2026-09-25 v1-vs-v2 build comparison that are
  in *no* ADR — stale-output cleaning, `ModuleReferenceModel.enabled`,
  `.gitignore` emission, substitution inside synced sources, build lifecycle
  hooks/policies, plus the two conditionally-deferred items whose trigger
  has now fired (overlap detection, ADR-0023 D2) — are listed in
  [build-command.md](build-command.md)'s Remaining Work.

## Changelog

- 2026-09-25: Added a pointer to [build-command.md](build-command.md)'s new
  "v1 parity gaps found 2026-09-25" section, so the items no ADR covers are
  reachable from this dashboard too.
- 2026-09-24: Created, as a single status dashboard across ADR-0021/0022/0023
  so a reader doesn't have to read the full ~2000 lines across all three to
  answer "what's built so far".
