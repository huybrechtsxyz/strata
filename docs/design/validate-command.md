# `strata validate` Command — Design

- Status: current — implemented
- Last updated: 2026-09-24

## Overview

`strata validate [PATH] [--strict]` checks every document in a solution:
schema (Phase 1) plus cross-document reference/semantic/version-pin checks
(Phase 2). Grounded directly in `src/strata/commands/validate_command.py`
and the controller layer it calls (`solution_context.py`,
`solution_controller.py`, `references.py`, `deployment_resolution.py`,
`semantic_checks.py`, `version_pins.py`) — this is the first fully-wired
command in v2 and the reference shape every future command (`build`,
`deploy`) should follow.

## Current Design

### CLI surface — deliberately diverges from v1

- **No `-f FILE`.** v2 addresses documents by `(kind, name)` and discovers
  them by walking up to `strata.yaml` ([ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md)),
  so validation is always "this solution", not "this file". `PATH` (optional,
  defaults to cwd) is just where to start looking for the solution root.
- **No `--deep`.** v1 made cross-reference checking opt-in because it
  needed an initialised workspace with an active profile; discovery gives
  v2 the whole index for free, so that precondition is gone. Checked
  against real usage (ADR-0020): all four production invocations passed
  `--deep` anyway. The complete check is now the unconditional default.
- **`--strict`**: treat warnings as errors (for release pipelines that must
  not ship a stale pin).
- Exit codes: `0` valid, `1` system failure, `2` not inside a solution/bad
  arguments, `3` validation errors found.

### Flow

```
validate_command()
  └─ open_solution(path)                       # solution_context.py
       ├─ find_solution_root(path)              # walk up to strata.yaml
       └─ SolutionController(root).load()       # Phase 1: discover + schema-validate every document
  └─ if context.ok:
       └─ context.resolve()                     # Phase 2, four passes in order:
            1. validate_references(index, solution)          # references.py
            2. resolve_deployment_chains(index)               # deployment_resolution.py
            3. run_semantic_checks(index, resolved_deployments)  # semantic_checks.py
            4. check_version_pins(index, solution)            # version_pins.py
     else:
       skip Phase 2 (a document that failed schema validation never
       entered the index — running Phase 2 anyway would turn one real
       error into a screenful of derived noise)
```

### Phase 1 — discovery + schema validation (`solution_controller.py`)

Recursively scans from the solution root; a file's own `kind:` field
selects which `BaseService` subclass validates it (`SERVICE_BY_KIND`, one
entry per kind — solution, configuration, providerconfig, topologyconfig,
provider, resource, dns, network, firewall, module, namespace, topology,
workspace, integration, tenant, environment, deployment, version). A YAML
file with no strata `apiVersion` is skipped silently (real repos are full
of Helm values/CI YAML); a strata-versioned file with an unrecognised
`kind` is an error (that's a typo). Duplicate `(kind, name)` is a hard
error. Every entry keeps its source path for error messages — this is now
the sole place provenance lives, since `file:` fields were removed from the
schema.

### Phase 2 — four passes, all over the already-loaded index

1. **Reference existence** (`references.py`) — walks every field marked
   `References(...)` (via `Annotated`, `reference_fields.py`) and checks the
   named document exists in the index, with typo-suggestion via
   `difflib.get_close_matches`. Deliberately does **not** check topology
   components/namespaces (those name workspace-local instances, not
   documents — `WorkspaceService.validate_topology_references` covers that
   scope separately, inside pass 3).
2. **`extends` chain resolution + tenant defaults** (`deployment_resolution.py`) —
   folds a deployment's `extends` ancestry into one complete document
   (cycle detection unconditional; a `partial: true` intermediate link is
   resolved but not itself required to be complete), then folds a
   referenced Tenant's `properties`/`custom`/`environments` in as a base
   layer underneath ([ADR-0024](../decisions/0024-tenant-defaults-merge.md)).
   Produces `resolved_deployments: dict[str, DeploymentModel]`, consumed by
   pass 3 so a deployment that only gets `workspace`/`environments` through
   `extends` is checked against its complete form.
3. **Semantic checks** (`semantic_checks.py`) — the "seven checks that
   existed but were never called": content-consistency checks between
   already-reference-resolved document pairs (deployments, tenants,
   providers, resources, workspaces, and deployment value tokens against a
   real `Environment`). Each was already a tested method on its owning
   service, built alongside that kind, and finally wired here — this is
   what closes the "parked Phase 2 validator" gap tracked in
   [solution-loading-and-phase2-validation.md](solution-loading-and-phase2-validation.md).
4. **Version pin checks** (`version_pins.py`) — per Version document (not
   per deployment referencing it, to avoid double-reporting): does a pin's
   target exist, and is pinning it even meaningful (e.g. a `remotes` pin
   naming a `fetch: external` remote is an **error** — CI already placed
   that remote, the pin cannot take effect; anything else unresolved is a
   **warning**, per [ADR-0019](../decisions/0019-version-pinning.md)).

### `SolutionContext.require_valid()`

A second entry point (`resolve()` + raise `ValidationError` on failure),
for any future command that needs to *act* on configuration rather than
just report on it (`build`, `deploy`) — building/deploying from a
partially-loaded index is never correct. `validate` itself uses `resolve()`
directly since it needs to render findings regardless of outcome.

## Related Decisions

- [ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md) — discovery loader this command is built on
- [ADR-0016](../decisions/0016-kind-field-validation.md) — `kind` mismatch rejection, part of Phase 1
- [ADR-0019](../decisions/0019-version-pinning.md) — version pin check severities
- [ADR-0020](../decisions/0020-v1-consumer-feature-priority.md) — real-usage evidence that `--deep` should be the default
- [ADR-0024](../decisions/0024-tenant-defaults-merge.md) — tenant defaults folded into pass 2
- [solution-loading-and-phase2-validation.md](solution-loading-and-phase2-validation.md) — tracks the validators this command wires (now stale — see its own note)

## Remaining Work / Open Questions

- **`--schema-only`** (the opt-out fast path, for a case where Phase 2's
  full solution load is too slow) is mentioned in the module docstring as
  the intended name but is **not implemented** — there is currently no way
  to skip Phase 2.
- Remote-qualified references (`@remote/name`) are not implemented — the
  index key already has a `remote` slot, always `None` today (ADR-0015).
- No `strata validate --output json` schema is documented here — see
  `commands/json_output.py`/`output.py` for the actual reporting shape, not
  duplicated in this doc.

## Changelog

- 2026-09-24: Created, grounded directly in `validate_command.py` and the
  controller layer, superseding the "not implemented" assumption in
  earlier design docs written before this command existed.
