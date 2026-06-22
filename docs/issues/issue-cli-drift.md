<!-- CHILD ISSUE
  Parent: x-strata-cli.md — strata env command group
  Absorbs: z-strata-drift.md
  Status: Ready to implement (step 6 in implementation order)
-->

# Feature: `strata env drift` — Infrastructure Drift Detection

**Parent:** [x-strata-cli.md](x-strata-cli.md) — `strata env` Unified Environment Inspection Group

## Summary

Implement `strata env drift` to detect manual infrastructure changes that diverge from the declared configuration. Runs `terraform plan` per stage and reports a detailed per-resource delta.

**Goal:** Answer "Has my infrastructure diverged from what strata last deployed?" with actionable detail.

---

## Motivation

- No current way to detect manual infrastructure changes without dropping out of strata to run `terraform plan` directly
- `strata env status --full` provides aggregate drift detection (deployed/drifted per stage) but not detailed per-resource changes
- CI pipelines need a dedicated drift check command with exit code signalling
- Operators need to know exactly which resources drifted before deciding whether to remediate

---

## Command Interface

```bash
strata env drift -f deploy-prd.yaml                # Per-deployment drift scan
strata env drift -f deploy-prd.yaml --output json   # Machine-readable for CI
strata env drift -f deploy-prd.yaml --output text   # Detailed text output (default)
```

### Future Flags (Not in V1)

```bash
strata env drift -f deploy-prd.yaml --remediate     # Auto-apply corrections
```

---

## Output Format (Console)

No drift:
```
🔍 strata env drift — deploy-prd.yaml
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Stage: infrastructure
  ✅ No drift detected — 0 changes

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Result: infrastructure is in sync
```

Drift detected:
```
🔍 strata env drift — deploy-prd.yaml
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Stage: infrastructure
  ⚠️  Drift detected — 3 changes

  ~ hcloud_server.hearth          (server_type: cx22 → cx32)
  + hcloud_firewall_rule.manual   (added outside strata)
  - hcloud_rdns.hearth_ipv6       (removed outside strata)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Result: 1 to change, 1 to add, 1 to destroy
```

## Output Format (JSON)

```json
{
  "deployment": "haven_deploy_prd",
  "file": "deploy/deploy-prd.yaml",
  "drifted": true,
  "stages": [
    {
      "name": "infrastructure",
      "drifted": true,
      "changes": 3,
      "resources": [
        {"action": "change", "resource": "hcloud_server.hearth", "detail": "server_type: cx22 → cx32"},
        {"action": "add", "resource": "hcloud_firewall_rule.manual", "detail": "added outside strata"},
        {"action": "destroy", "resource": "hcloud_rdns.hearth_ipv6", "detail": "removed outside strata"}
      ]
    }
  ]
}
```

---

## Architecture

```
commands/
  env/
    drift_command.py             ← EnvDriftCommand extends BaseCommand
controllers/
  env_drift_controller.py        ← Runs terraform plan, parses resource-level changes
```

### Layer Rules

- `EnvDriftCommand` → `EnvDriftController` → `TerraformDeployer.plan()`
- `INIT_REQUIRED = True`
- Reuse existing `TerraformDeployer.plan()` — already runs `terraform plan` and can parse output
- Uses `terraform plan -detailed-exitcode` (exit 0 = no changes, exit 2 = changes)

---

## Relationship to `strata env status`

| Aspect   | `env status --full`                        | `env drift`                    |
| -------- | ------------------------------------------ | ------------------------------ |
| Scope    | All deployments, aggregate                 | Single deployment, detailed    |
| Output   | Per-stage state (deployed/drifted/unknown) | Per-resource change list       |
| Speed    | Slower (checks all deployments)            | Focused (one deployment)       |
| Use case | Dashboard overview                         | Troubleshooting specific drift |

Both invoke `terraform plan` internally. `env status --full` could delegate to `env drift` per deployment, or they can share the underlying controller logic.

---

## Exit Codes

| Code | Meaning                                                  |
| ---- | -------------------------------------------------------- |
| 0    | No drift detected                                        |
| 1    | System/execution error (auth failure, build dir missing) |
| 3    | Drift detected (matches validation failure convention)   |

Exit code 3 enables CI: `strata env drift -f deploy-prd.yaml || alert "Drift detected"`

---

## Future Scope (Not V1)

- `--remediate`: auto-apply `terraform apply` to correct drift
- `drift.schedule: daily` in deployment spec for scheduled checks
- Read-lock acquisition during drift scan (depends on locking implementation)

---

## Acceptance Criteria

- [ ] `strata env drift -f FILE` runs `terraform plan` per stage
- [ ] Per-resource change list displayed (change/add/destroy)
- [ ] `--output json` emits valid JSON with per-resource detail
- [ ] Exit code 0 when no drift, 3 when drift detected
- [ ] Hard-fail with clear message when no build output exists
- [ ] Hard-fail with clear message when auth fails
- [ ] `--remediate` flag accepted but prints "not yet implemented" in V1

## Relationships

- **Absorbs:** `z-strata-drift.md` (design expanded, moved from `deploy drift` to `env drift`)
- **Depends on:** `x-strata-cli-status.md` (shared terraform plan infrastructure)
- **Related:** `x-strata-cli-status.md` (status = aggregate, drift = detailed)
