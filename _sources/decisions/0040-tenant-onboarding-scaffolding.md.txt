# Tenant Onboarding Scaffolding

- Status: partial
- Date: 2026-07-15

## Context and Problem Statement

Adding a new tenant to a fleet-scale deployment (ADR 0038) requires manually creating files
across multiple directories in a precise path structure:

```
customers/<tenant>/tenant.yaml
customers/<tenant>/<ring>/env.yaml          (one per ring)
zones/<zone>/customers/<tenant>/env.yaml    (one per zone)
zones/<zone>/customers/<tenant>/<ring>/env.yaml   (one per zone × ring)
zones/<zone>/customers/<tenant>/<ring>/deploy.yaml
```

For a tenant deployed across 2 zones and 3 rings, this is 10+ files minimum, with paths that
must be internally consistent (tenant name, zone name, ring name must match across all files
and within each file's `meta`, `layers`, and `environments[]` references).

Errors in this process — wrong path, mismatched tenant name in `layers`, wrong env file
reference order — are not caught until `strata validate --deep` or a deployment is attempted.
There is no guided path from "I need to onboard tenant X" to "I have a valid, deployable
configuration tree."

ADR 0014 addressed onboarding friction for new strata users creating their first workspace.
This ADR addresses the ongoing operational friction of adding tenants to an existing fleet.

## Related Work

- **ADR 0014 — Guided Onboarding Experience**: `strata new` for initial workspace setup.
  This ADR extends the scaffolding surface for fleet tenant lifecycle operations.
- **ADR 0038 — Multi-Tenant Fleet Management Patterns**: identifies this as Gap 2 (Medium-High).
- **ADR 0039 — Deployment Templates**: tenant scaffolding generates deployment instantiation
  files that reference a shared template, not standalone deployment files.
- **ADR 0042 — Deep Validation and Layer Consistency**: validates the output of scaffolding
  before a tenant is committed.

---

## Design Overview

### New command: `strata new tenant`

```bash
strata new tenant \
  --name contoso \
  --zones europe-west nordics \
  --rings dev qas prd \
  --template customer-ring-template    # references ADR 0039 deployment template
```

**What it generates:**

| File                                                 | Content                                                     |
| ---------------------------------------------------- | ----------------------------------------------------------- |
| `customers/<tenant>/tenant.yaml`                     | Tenant metadata stub                                        |
| `customers/<tenant>/<ring>/env.yaml`                 | Zone-agnostic env stub per ring                             |
| `zones/<zone>/customers/<tenant>/env.yaml`           | Zone × tenant override stub per zone                        |
| `zones/<zone>/customers/<tenant>/<ring>/deploy.yaml` | Deployment instantiation (extends template) per zone × ring |
| `zones/<zone>/customers/<tenant>/<ring>/env.yaml`    | Zone × tenant × ring env stub per zone × ring               |

All generated files are **stubs** — they pass schema validation immediately but contain
only the structural keys (meta, kind, apiVersion, layers, environments list) pre-wired
correctly. The operator fills in actual variable/secret values.

### Path consistency enforcement

The scaffolding command derives all paths from `--name`, `--zones`, and `--rings`. It does
not allow the operator to specify paths independently — this eliminates the class of errors
where a file is placed in the wrong directory or references the wrong tenant name.

Generated `environments[]` lists in deploy files are assembled in the canonical order:
1. Zone baseline
2. Tenant × ring defaults (zone-agnostic)
3. Zone × tenant × ring specifics

### Dry-run and validation

```bash
strata new tenant --name fabrikam --zones europe-west --rings dev prd --dry-run
```

`--dry-run` prints what would be created without writing files. After generation, the
command automatically runs `strata validate` on each generated file and reports any
schema errors before the operator commits.

### Tenant removal: `strata remove tenant`

A matching removal command that lists all files associated with a tenant name and
prompts for confirmation before deletion. Prevents orphaned files when a tenant
is offboarded.

```bash
strata remove tenant --name contoso --zones europe-west
```

---

## Open Questions

1. **Template selection** — should `--template` be optional with a sensible default, or
   always required? Requiring it is explicit; defaulting to the first available template
   is ergonomic.
2. **Idempotency** — if some files already exist, should the command skip, overwrite
   (with `--force`), or error? Skip with a warning is the safest default.
3. **Interactive mode** — should `strata new tenant` without flags launch an interactive
   wizard (consistent with ADR 0014 REPL approach) or always require explicit flags?

---

## Consequences

- New tenant onboarding goes from 10+ hand-authored files to a single command.
- Path structure and layer consistency are enforced at generation time, not discovered
  at deployment time.
- ADR 0042 (deep validation) provides a post-generation validation gate before the
  operator commits the scaffolded files.
- `strata remove tenant` gives the fleet a clean offboarding path, preventing
  configuration accumulation for churned tenants.
