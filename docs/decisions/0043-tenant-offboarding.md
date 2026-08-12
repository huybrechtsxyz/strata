# Tenant Offboarding

- Status: proposed
- Date: 2026-07-15
## Remaining Work

- Not started — nothing in this ADR has been implemented yet.
## Context and Problem Statement

ADR 0040 addressed tenant onboarding — creating the files needed to bring a new tenant
into the fleet. The reverse operation is unaddressed: when a tenant churns or is migrated,
the operator must manually identify and delete all files associated with that tenant.

At fleet scale (ADR 0038) a tenant's presence spans:

- `tenants/<name>/tenant.yaml` — the tenant definition
- `tenants/<name>/` — any additional per-tenant env files
- Any deployment files that declare `spec.tenant: <name>`

Missing a file leaves stale configuration that wastes validation time and can confuse
tooling that scans the repository for active tenants. Deleting too aggressively breaks
deployments that still reference the tenant.

The operator needs a command that discovers all tenant-associated files, warns about
active deployment references, and removes files after explicit confirmation.

## Decision Drivers

- Discovery must be complete: all files belonging to a tenant must be listed before
  anything is deleted.
- Active deployment references must surface as warnings, not silent blockers.
  The operator decides whether to proceed.
- Default behaviour is dry-run — no files are deleted without an explicit flag.
- The command must work with any repository layout: `tenants/`, `customers/`, or a
  custom path configured per workspace.
- Deletion is atomic at the tenant level: all files or none. No partial removal.

## Decision Outcome

Add `strata remove tenant <name>` as a new command group under `strata remove`.

### Command interface

```bash
# Dry-run (default) — list what would be deleted
strata remove tenant contoso

# Execute deletion after confirmation
strata remove tenant contoso --force

# Skip interactive prompt (CI / scripted use)
strata remove tenant contoso --force --yes
```

### Discovery algorithm

1. **Tenant file scan** — search for files matching:
   - `<tenants_root>/<name>.yaml` (single-file layout)
   - `<tenants_root>/<name>/` (directory layout, all files inside)

   `<tenants_root>` is resolved in this order:
   - `spec.tenants_path` in `solution.json` (if present)
   - First of `tenants/`, `customers/` that exists under the workspace root
   - The workspace root itself (fallback)

2. **Deployment reference scan** — search all `*.yaml` files in the workspace for
   `spec.tenant: <name>`. Files found here are reported as warnings but do not
   block deletion.

### Output (console, dry-run)

```
🔍 Tenant: contoso
   Work path: /srv/fleet-config

Files to delete:
  tenants/contoso/tenant.yaml
  tenants/contoso/env.yaml

⚠️  Active deployment references (will not be deleted):
  zones/europe-west/customers/contoso/dev/deploy.yaml  ← spec.tenant: contoso
  zones/nordics/customers/contoso/prd/deploy.yaml      ← spec.tenant: contoso

Run with --force to delete the files listed above.
Deployment files referencing this tenant must be removed separately.
```

### Output (console, --force)

```
🗑️  Removed: tenants/contoso/tenant.yaml
🗑️  Removed: tenants/contoso/env.yaml

✅  Tenant contoso offboarded. 2 files removed.
⚠️  2 deployment files still reference this tenant — remove them manually.
```

### JSON envelope (--output json)

```json
{
  "success": true,
  "data": {
    "tenant": "contoso",
    "removed": ["tenants/contoso/tenant.yaml", "tenants/contoso/env.yaml"],
    "deployment_references": [
      "zones/europe-west/customers/contoso/dev/deploy.yaml",
      "zones/nordics/customers/contoso/prd/deploy.yaml"
    ],
    "dry_run": false
  }
}
```

### Implementation

New command group `strata remove` with sub-command `tenant`:

```
src/strata/commands/remove/
  __init__.py
  remove_tenant_command.py    ← RemoveTenantCommand(BaseCommand)
src/strata/commands/cli_remove.py
```

`RemoveTenantCommand._execute()`:

1. Resolve `tenants_root` (see discovery algorithm above).
2. Scan for tenant files → `files_to_remove`.
3. Scan all `*.yaml` in workspace for `spec.tenant: <name>` → `deployment_refs`.
4. Report findings (always, even with `--force`).
5. If `--force` and `files_to_remove` is non-empty:
   - If not `--yes`: prompt `"Delete {N} files? [y/N]"`
   - Delete all files atomically (collect errors; if any fail, report and stop).
6. Return `True` on success, `False` if files remain after errors.

`RemoveTenantCommand` requires an initialized workspace (`super()._initialize()` — tenant
root resolution depends on `solution.json`).

## Consequences

- Operators get a safe, discoverable offboarding path. Dry-run by default means accidental
  deletion is not possible.
- Deployment reference warnings surface misconfiguration that would otherwise be discovered
  only during the next build or deploy.
- The command does not touch deployment files — these are intentionally excluded from
  automatic deletion because they may be in different repositories or managed by a
  different team.
- A new `strata remove` command group is introduced. Future removal operations (e.g.
  `strata remove profile`, `strata remove repo`) fit naturally here.

## Related ADRs

- [ADR-0038](0038-multi-tenant-fleet-management-patterns.md) — fleet usage patterns that
  motivate this command.
- [ADR-0040](0040-tenant-onboarding-scaffolding.md) — the onboarding counterpart.
- [ADR-0012](0012-rename-customer-to-tenant.md) — establishes `tenant` as the canonical term.
