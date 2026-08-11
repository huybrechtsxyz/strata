# Fleet Rollout — Multi-Deployment Wave Execution (`strata rollout`)

- Status: proposed
- Date: 2026-07-14
## Remaining Work

- Not started — nothing in this ADR has been implemented yet.- Revised: 2026-07-21

## Context and Problem Statement

ADR-0011 introduced wave lock files and `--wave N` as an _explicit context override_ for a single
deployment (`strata deploy run -f deployment.yaml --wave 1 --promotion customer-apps`).  A common
real-world need — executing a wave rollout across an entire fleet — still requires the operator to
enumerate every relevant deployment file manually or write shell scripts.

The missing capability is:

```bash
strata rollout run --ring prod --wave 1 --promotion customer-apps
# → discover every deployment in ring 'prod' that belongs to wave 1
# → build + deploy each of them in the correct order
# → summarise results
```

This ADR introduces `strata rollout` as a **dedicated fleet-level command group** — separate from
`strata deploy` (single-file) and `strata promote` (version lock management).

### Why a separate command group?

| Concern         | `strata deploy run`                   | `strata rollout run`                      |
| --------------- | ------------------------------------- | ----------------------------------------- |
| Scope           | One deployment file (`-f`)            | All matching files in the solution        |
| Pipeline        | Deploy only (assumes artifacts exist) | Build → validate → deploy                 |
| Failure model   | Single success/failure exit           | Batch summary, stagger, continue-on-error |
| Target audience | CI per-file step                      | Operator executing a fleet wave           |
| Output model    | Per-stage events                      | Batch envelope + per-deployment events    |

Overloading `strata deploy run` with both modes (via "no `-f` = mass mode") was considered and
rejected: the hidden mode switch, different failure semantics, and build-gap problem make it
confusing for operators.

## Related Work

- **ADR-0011 — Promotion strategies**: defines waves, rings, lock files, `--ring / --wave / --promotion` context flags.
- **ADR-0028 — SIGTERM graceful shutdown**: applies — long-running batch must release locks on interrupt.
- **ADR-0027 — Command timeout**: each sub-deployment is subject to existing timeout semantics.
- **ADR-0029 — NDJSON streaming**: the rollout command uses the existing streaming conventions.
- **ADR-0023 — Pluggable provisioners**: build and deploy use `DeployerFactory`.

---

## Design Overview

### CLI Surface — `strata rollout`

```bash
# Full pipeline: build + deploy all wave-1 deployments in ring prod
strata rollout run --ring prod --wave 1 --promotion customer-apps

# Build only (generate artifacts for all matched deployments)
strata rollout build --ring prod --wave 1 --promotion customer-apps

# Deploy only (assumes artifacts already built)
strata rollout deploy --ring prod --wave 1 --promotion customer-apps

# Dry-run (discovery + plan, no provisioning)
strata rollout run --ring prod --wave 1 --promotion customer-apps --dry-run

# Sequential with 60-second bake time between deployments
strata rollout run --ring prod --wave 1 --promotion customer-apps --stagger 60

# Continue through failures, collect all results
strata rollout run --ring prod --wave 1 --promotion customer-apps --continue-on-error

# Status: show current wave state across the fleet
strata rollout status --ring prod --promotion customer-apps

# History: past rollout executions
strata rollout history --ring prod --promotion customer-apps
```

### Commands

| Command                  | What it does                           |
| ------------------------ | -------------------------------------- |
| `strata rollout run`     | Build + deploy all matched deployments |
| `strata rollout build`   | Build artifacts only                   |
| `strata rollout deploy`  | Deploy only (artifacts must exist)     |
| `strata rollout status`  | Show fleet wave state                  |
| `strata rollout history` | Past rollout results                   |

### Discovery — how deployments are matched

A deployment matches the filter when **both** conditions hold:

1. **Environment match** — the deployment's resolved environment has
   `spec.promotion.ring == --ring` AND `spec.promotion.strategy == --promotion`.
2. **Wave match** — when `--wave N` is given, the deployment's `meta.labels` must match the wave
   selector declared in `promotions.strategies[name].rings[ring].waves[N].match_labels` in the
   configuration.  Deployments that do not match any wave selector are in the **catch-all** group
   (not included in wave N execution — included only in the final `--complete` run).

When `--wave` is omitted, all matched deployments are included regardless of wave membership.

Discovery scans all deployment YAML files registered in `.strata/solution.json`.

### Execution Pipeline

For each matched deployment, `strata rollout run` executes:

```
1. Build   → strata build run -f <file>     (generate Terraform/Helm/etc. artifacts)
2. Validate → strata validate -f <file>     (pre-flight checks)
3. Deploy  → strata deploy run -f <file>    (provision infrastructure)
```

Steps 1–2 are skipped for `strata rollout deploy`. Step 3 is skipped for `strata rollout build`.

### Execution Order

#### Phase 1 — Sequential with stagger (initial implementation)

Deployments execute one at a time in solution registration order. `--stagger N` inserts an
N-second bake time between completions.

#### Phase 2 — Parallel groups (future)

Deployments are grouped by `deployment_wave` field. Groups execute sequentially; deployments
within a group run in parallel up to `--concurrency N` (default: 3).

```
Group 1 (deployment_wave: 1): [svc-a, svc-b, svc-c]  ← parallel
          ↓ all succeed
Group 2 (deployment_wave: 2): [svc-d, svc-e]          ← parallel
          ↓ all succeed
Done.
```

### Failure Handling

- **Stop on failure (default)**: First failure halts remaining deployments.
- **Continue on failure (`--continue-on-error`)**: All deployments run. Exit code reflects worst result.
- **No automatic rollback**: A failed rollout is not automatically reversed. Rollback is a deliberate
  operator action (`strata promote rollback`).

### Output

Console:
```
🌊  Rollout: ring=prod wave=1 promotion=customer-apps
    Deployments matched: 4

  [1/4] customer-acme
        build   ✅  0m 22s
        deploy  ✅  2m 14s
  [2/4] customer-beta
        build   ✅  0m 18s
        deploy  ✅  1m 58s
  [3/4] customer-gamma
        build   ✅  0m 20s
        deploy  ❌  FAILED — stopped here

  Result: 2 succeeded, 1 failed, 1 skipped
```

JSON (`--output json`):
```json
{
  "rollout": {
    "ring": "prod",
    "wave": 1,
    "promotion": "customer-apps",
    "total": 4,
    "succeeded": 2,
    "failed": 1,
    "skipped": 1,
    "deployments": [
      { "file": "deploy/customer-acme.yaml", "build": "succeeded", "deploy": "succeeded", "duration_seconds": 156 },
      { "file": "deploy/customer-beta.yaml", "build": "succeeded", "deploy": "succeeded", "duration_seconds": 136 },
      { "file": "deploy/customer-gamma.yaml", "build": "succeeded", "deploy": "failed", "exit_code": 1 },
      { "file": "deploy/customer-delta.yaml", "build": "skipped", "deploy": "skipped", "reason": "stopped after failure" }
    ]
  }
}
```

NDJSON (`--output ndjson`): per-deployment `stage_start`/`stage_end` events following ADR-0029.

### SIGTERM / Interruption

If the process receives SIGTERM mid-rollout, the currently executing deployment completes its
current provisioner step and releases its lock. Queued deployments are skipped. Exit code: 130.

---

## Constraints and Non-Goals

- **No automatic rollback on failure** — use `strata promote rollback`.
- **No cross-batch ordering** — each rollout is scoped to one ring + one promotion.
- **No scheduling** — use a CI/CD pipeline scheduler.
- **Solution registry required** — only registered deployment files are discovered.
- **Not in `strata deploy`** — fleet operations are a distinct command group.

---

## Open Questions

1. **`deployment_wave` model field** — When to add for parallel-group support? Follow-up patch to ADR-0011 models.
2. **`--complete` semantics** — Should `strata rollout run --complete` advance the ring lock? Or keep in `strata promote` only?
3. **Re-entrancy** — Should re-run skip already-succeeded deployments? Requires batch state file. Deferred.
4. **Build-only failures** — If 2/4 builds fail, proceed with the 2 that built? Or all-or-nothing?

---

## Implementation Roadmap

| Phase | Description                                                        | Status     |
| ----- | ------------------------------------------------------------------ | ---------- |
| R-1   | Discovery: scan solution registry, match ring + promotion + labels | 🔲 TODO     |
| R-2   | `strata rollout build` — batch build for matched deployments       | 🔲 TODO     |
| R-3   | `strata rollout deploy` — sequential execution loop                | 🔲 TODO     |
| R-4   | `strata rollout run` — combined build + deploy pipeline            | 🔲 TODO     |
| R-5   | `--stagger N` inter-deployment bake time                           | 🔲 TODO     |
| R-6   | `--continue-on-error` flag                                         | 🔲 TODO     |
| R-7   | Batch summary output (console + JSON + NDJSON)                     | 🔲 TODO     |
| R-8   | SIGTERM handling for rollout context                               | 🔲 TODO     |
| R-9   | `strata rollout status` — fleet wave state                         | 🔲 TODO     |
| R-10  | `strata rollout history` — past rollouts                           | 🔲 TODO     |
| R-11  | Parallel groups (`deployment_wave`, `--concurrency N`)             | 🔲 DEFERRED |
