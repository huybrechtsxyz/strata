# Bundle Entry Expansion (`each`) for Multi-File Scaffolding

- Status: proposed
- Date: 2026-07-21
## Remaining Work

- Not started — nothing in this ADR has been implemented yet.
## Context and Problem Statement

Adding a new entity (tenant, zone, workspace type) to a fleet-scale deployment (ADR 0038)
requires creating files across multiple directory dimensions simultaneously. For a tenant
deployed across 2 zones × 3 rings, this is 8+ files with paths that must be internally
consistent:

```
customers/<tenant>/tenant.yaml
customers/<tenant>/<ring>/env.yaml          (one per ring)
zones/<zone>/customers/<tenant>/env.yaml    (one per zone)
zones/<zone>/customers/<tenant>/<ring>/env.yaml   (one per zone × ring)
zones/<zone>/customers/<tenant>/<ring>/deploy.yaml
```

The `solution.json` bundle mechanism (`spec.templates[].bundle[]`) already supports
multi-file scaffolding via `strata new <template> <name>`. An operator can define a
bundle today that generates all of these files — but must statically enumerate one bundle
entry per zone × ring combination:

```json
{ "name": "zone-ring-deploy", "path": "zones/europe-west/customers/{{ name }}/dev" },
{ "name": "zone-ring-deploy", "path": "zones/europe-west/customers/{{ name }}/qas" },
{ "name": "zone-ring-deploy", "path": "zones/us-east/customers/{{ name }}/dev" },
{ "name": "zone-ring-deploy", "path": "zones/us-east/customers/{{ name }}/qas" },
...
```

Adding a new zone or ring means editing the bundle definition and adding N new entries.
The bundle definition becomes coupled to the topology rather than describing the structure
once.

This is not tenant-specific. Any fleet entity that spans zones × rings has the same
problem. The fix is in the bundle runner, not in a dedicated subcommand.

## Related Work

- **ADR 0014 — Guided Onboarding Experience**: introduced `strata new` and `solution.json`
  bundle templates. This ADR extends `SolutionTemplateBundleEntryModel`.
- **ADR 0038 — Multi-Tenant Fleet Management Patterns**: identifies multi-dimensional
  scaffolding as Gap 2 (Medium-High).
- **ADR 0039 — Deployment Templates**: bundle entries reference named templates, not
  standalone files.

---

## Decision

Add an `each` field to `SolutionTemplateBundleEntryModel`. When present, the bundle runner
computes the cartesian product of all dimension values and emits one output per combination,
substituting each dimension variable into the entry's `path` and file content alongside the
standard context variables.

```json
{
  "name": "zone-ring-deploy",
  "path": "zones/{{ zone }}/customers/{{ name }}/{{ ring }}",
  "each": {
    "zone": "{{ zones }}",
    "ring": "{{ rings }}"
  }
}
```

`each` values use the same `{{ var }}` Jinja2 syntax used everywhere else in strata. A
value is rendered against the current context, then split on `,` to produce the list for
that dimension. No new reference syntax is introduced.

---

## Design

### `SolutionTemplateBundleEntryModel` — model change

```python
class SolutionTemplateBundleEntryModel(BaseModel):
    name: str   # template source
    path: str   # Jinja2 destination path
    each: Optional[Dict[str, str]] = None
    # each key   → dimension variable name injected into path and content
    # each value → Jinja2 expression rendered against context, then split on ","
```

### Bundle runner — `_run_solution_bundle_execution`

For each entry in the bundle:

1. If `each` is absent → current behaviour (single file, render path with context).
2. If `each` is present:
   a. For each dimension key/value pair: render the value string with Jinja2 context,
      split on `,`, strip whitespace → produces a list of strings per dimension.
   b. Compute the cartesian product of all dimension lists.
   c. For each combination: merge `{dimension: value, ...}` into a copy of the context,
      then execute the standard single-entry logic (render path, render content, write file).

### Complete example — tenant onboarding bundle in `solution.json`

```json
"context": {
  "zones": "europe-west,us-east",
  "rings": "dev,qas,prd"
},
"templates": [
  {
    "name": "onboard-tenant",
    "bundle": [
      {
        "name": "tenant",
        "path": "customers/{{ name }}"
      },
      {
        "name": "tenant-ring-env",
        "path": "customers/{{ name }}/{{ ring }}",
        "each": { "ring": "{{ rings }}" }
      },
      {
        "name": "zone-tenant-env",
        "path": "zones/{{ zone }}/customers/{{ name }}",
        "each": { "zone": "{{ zones }}" }
      },
      {
        "name": "zone-ring-env",
        "path": "zones/{{ zone }}/customers/{{ name }}/{{ ring }}",
        "each": { "zone": "{{ zones }}", "ring": "{{ rings }}" }
      },
      {
        "name": "zone-ring-deploy",
        "path": "zones/{{ zone }}/customers/{{ name }}/{{ ring }}",
        "each": { "zone": "{{ zones }}", "ring": "{{ rings }}" }
      }
    ]
  }
]
```

```bash
strata new onboard-tenant contoso
# → 1 + 3 + 2 + 6 + 6 = 18 files for 2 zones × 3 rings
# → adding a zone: update spec.context.zones — bundle definition unchanged
```

`spec.context.zones` and `spec.context.rings` act as the fleet's authoritative dimension
lists, defined once. The `{{ zones }}` reference in `each` picks them up through the same
context resolution path used for all other Jinja2 variables.

### Idempotency

Existing `--overwrite` flag applies per-file within an expanded entry. Without `--overwrite`,
the runner stops on the first already-existing destination and reports it.

### Validation

Existing `--validate` flag applies to all generated files regardless of expansion.

---

## Phase 1 — Literal and context-variable expansion

**Scope:** implement `each` with values sourced from:
- Literal comma-separated string: `"zone": "europe-west,us-east"`
- `spec.context` variable reference: `"zone": "{{ zones }}"` where `zones` is a key in
  `spec.context`
- `--set` overrides: `--set zones=eu-only` narrows expansion at invocation time

No changes to how `NewCommand` loads workspace data are required. `spec.context` is already
loaded from `solution.json` and merged into the render context.

**Model change:** add `each: Optional[Dict[str, str]] = None` to
`SolutionTemplateBundleEntryModel`.

**Runner change:** extend `_run_solution_bundle_execution` in `run_new_command.py` to
detect `each`, compute cartesian product, and iterate.

---

## Phase 2 — Workspace-model-injected dimension variables

**Scope:** `NewCommand` additionally loads the workspace configuration YAML and injects
well-known variables into the render context before bundle execution:

| Injected variable     | Source                                             |
| --------------------- | -------------------------------------------------- |
| `configuration_zones` | `configuration.spec.zones[].name` joined with `,`  |
| `configuration_rings` | Default progression `rings[].name` joined with `,` |

Operators can then write:

```json
"each": { "zone": "{{ configuration_zones }}", "ring": "{{ configuration_rings }}" }
```

The bundle definition becomes fully topology-agnostic — no zone/ring names appear in
`solution.json` at all. Adding a zone to `configuration.spec.zones` automatically expands
all bundle entries on the next `strata new` invocation.

`spec.context` values take precedence over injected configuration values (same priority
order as existing `--set` > `spec.context` > injected).

Phase 2 is gated on a real operator requirement for fully topology-driven expansion.
Phase 1 `spec.context` references cover the common case with less implementation risk.

---

## Out of Scope

- **`strata new tenant` dedicated subcommand** — unnecessary; the bundle mechanism is
  generic and already invoked via `strata new <template-name> <entity-name>`.
- **`--zones` / `--rings` CLI flags** — zones and rings are already known to the workspace;
  they belong in `spec.context` or injected from configuration, not passed per-invocation.
- **`strata remove <entity>`** — entity removal is a destructive multi-file operation that
  warrants its own ADR and confirmation UX. Not addressed here.

---

## Consequences

- Multi-dimensional scaffolding (tenant × zone × ring, or any future N-dimensional
  structure) collapses to a single `strata new <template> <name>` invocation.
- Adding a topology dimension (new zone, new ring) requires updating `spec.context` in
  `solution.json` once — bundle definitions do not change.
- The `each` mechanism is general: any bundle entry for any entity kind benefits from it.
- No new CLI subcommands, no new flag surface, no new syntax beyond the `{{ var }}`
  operators already use.
- `SolutionTemplateBundleEntryModel` gains one optional field; existing bundles without
  `each` are unaffected.
