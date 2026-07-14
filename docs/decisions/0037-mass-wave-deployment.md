# Mass Wave Deployment

- Status: proposed
- Date: 2026-07-14

## Context and Problem Statement

ADR 0011 introduced wave lock files and `--wave N` as an _explicit context override_ for a single
deployment (`strata deploy run -f deployment.yaml --wave 1 --promotion customer-apps`).  A common
real-world need — executing a wave rollout across an entire fleet — still requires the operator to
enumerate every relevant deployment file manually or write shell scripts.

The missing capability is:

```bash
strata deploy run --ring prod --wave 1 --promotion customer-apps
# → discover every deployment in ring 'prod' that belongs to wave 1
# → deploy each of them in the correct order
# → summarise results
```

This "mass wave deployment" mode turns `strata deploy run` from a single-file tool into a
**fleet-aware operator** for rolling deployments.

## Related Work

- **ADR 0011 — Promotion strategies for version progression**: defines waves, rings, lock files,
  `--ring / --wave / --promotion` context flags (Layer 4/5 design). This ADR extends that design
  with fleet-level execution.
- **ADR 0028 — SIGTERM graceful shutdown and lock release**: applies directly — a long-running
  multi-deployment pipeline must release locks on interrupt.
- **ADR 0027 — Command timeout for long-running operations**: each sub-deployment is subject to the
  existing timeout semantics.
- **ADR 0029 — Real-time progress streaming (NDJSON)**: the output model for this command should
  follow the existing streaming conventions.

---

## Design Overview

### Trigger condition

Mass-wave mode activates when `-f` is **not** given and at least one of `--ring`, `--wave`, or
`--promotion` is provided.  When `-f` is given alongside the filter flags, behaviour is unchanged:
a single deployment runs with the filter flags used only for version-resolution context (ADR 0011
R-11).

```
-f given          → single-deployment mode (today's behaviour, filter flags = version context)
-f NOT given      → mass-wave mode (this ADR)
```

### Discovery — how deployments are matched

A deployment matches the filter when **both** conditions hold:

1. **Environment match** — the deployment's resolved environment has
   `spec.promotion.ring == --ring` AND `spec.promotion.strategy == --promotion`.
2. **Wave match** — when `--wave N` is given, the deployment's `meta.labels` must match the wave
   selector declared in `promotions.strategies[name].rings[ring].waves[N].match_labels` in the
   configuration.  Deployments that do not match any wave selector are in the **catch-all** group
   (not included in wave N execution — included only in the final `--complete` run).

When `--wave` is omitted, all matched deployments are included regardless of wave membership
(useful for `--complete` runs and for promotions that do not use waves).

Discovery scans all deployment YAML files registered in the workspace solution (`.strata/solution.json`).
Files not registered are not scanned.

### Execution model — open decision

The order and concurrency of sub-deployments is the primary open question.  Three candidate models:

#### Option A — Strict sequential
Deploy one file at a time in discovery order (alphabetical by file path).  Stop immediately on the
first failure.

| Pro | Con |
|-----|-----|
| Simple, predictable | Slow for large fleets |
| Each deployment fully settled before next starts | A single slow deployment blocks everything |
| Easy to reason about failure state | No parallelism benefit |

#### Option B — Sequential with configurable inter-deployment delay (`--stagger N`)
Same as Option A but an optional `--stagger <seconds>` flag inserts a wait between deployments.
This gives the monitoring system time to observe the previous deployment before proceeding.

| Pro | Con |
|-----|-----|
| Natural "bake time" between deployments | Still single-threaded |
| Operator controls blast radius rate | Long total wall-clock time |

#### Option C — Parallel groups (recommended starting point)
Deployments are sorted into **groups** based on the `deployment_wave` field in the promotion ring
(a sub-ordering within the ring wave, e.g. `deployment_wave: 1` = first, `deployment_wave: 2` =
second).  Deployments within the same `deployment_wave` group run in parallel up to
`--concurrency N` (default: 3).  Groups are executed sequentially — all of group 1 must succeed
before group 2 starts.

```
Group 1 (deployment_wave: 1): [svc-a, svc-b, svc-c]  ← run in parallel
          ↓ all succeed
Group 2 (deployment_wave: 2): [svc-d, svc-e]          ← run in parallel
          ↓ all succeed
Done.
```

When no `deployment_wave` is configured, all matched deployments are in a single group (equivalent
to "all in parallel, up to concurrency limit").

| Pro | Con |
|-----|-----|
| Significantly faster for large fleets | More complex failure analysis |
| Natural blast-radius control via groups | Requires `deployment_wave` to be configured for ordering |
| Concurrency limit caps infrastructure load | Partial failure state (some deployed, some not) |

**Failure handling for Option C:**
- **Stop on group failure (default)**: If any deployment in a group fails, the group is marked
  failed and subsequent groups do not run.  Already-completed deployments are not rolled back
  (rollback is a separate `strata promote rollback` operation).
- **Continue on failure (`--continue-on-error`)**: All groups run regardless.  Exit code reflects
  the worst result across all deployments.

#### Recommendation

Start with **Option B** (sequential + `--stagger`) as the initial implementation.  It requires no
new model fields (`deployment_wave` is deferred), is easy to test, and provides the bake-time
story operators ask for.  Option C (parallel groups) is the target architecture and can be
introduced once `deployment_wave` is modelled and tested.

---

### CLI summary

```bash
# Deploy all wave-1 deployments in ring prod, using promotion customer-apps
strata deploy run --ring prod --wave 1 --promotion customer-apps

# Same, dry-run
strata deploy run --ring prod --wave 1 --promotion customer-apps --dry-run

# Sequential with 60-second bake time between deployments
strata deploy run --ring prod --wave 1 --promotion customer-apps --stagger 60

# Continue through failures, collect all results
strata deploy run --ring prod --wave 1 --promotion customer-apps --continue-on-error

# Complete: advance ring lock, no wave filter
strata deploy run --ring prod --promotion customer-apps --complete
```

### Output

The command produces a **batch summary** in addition to per-deployment output:

```
🌊  Mass wave deployment
    Ring: prod  |  Wave: 1  |  Promotion: customer-apps
    Deployments found: 4

  [1/4] customer-acme   ✅  2m 14s
  [2/4] customer-beta   ✅  1m 58s
  [3/4] customer-gamma  ❌  FAILED (exit 1)  — stopped here

  Result: 2 succeeded, 1 failed, 1 skipped
  Exit code: 1
```

JSON output (`--output json`) wraps the per-deployment results in a `batch` envelope:

```json
{
  "batch": {
    "ring": "prod",
    "wave": 1,
    "promotion": "customer-apps",
    "total": 4,
    "succeeded": 2,
    "failed": 1,
    "skipped": 1,
    "deployments": [
      { "file": "deploy/customer-acme.yaml", "status": "succeeded", "duration_seconds": 134 },
      { "file": "deploy/customer-beta.yaml", "status": "succeeded", "duration_seconds": 118 },
      { "file": "deploy/customer-gamma.yaml", "status": "failed",    "exit_code": 1 },
      { "file": "deploy/customer-delta.yaml", "status": "skipped",   "reason": "stopped after failure" }
    ]
  }
}
```

### Dry-run behaviour

`--dry-run` runs discovery and prints the list of matched deployments (with their resolved wave
group, if applicable) but does not execute any provisioner.  This is the safe "what would this do?"
check before a real rollout.

### Interaction with SIGTERM / interruption

If the process receives SIGTERM mid-batch, the currently executing sub-deployment is allowed to
complete its current provisioner step and release its lock.  Queued deployments are skipped.  The
batch exits with code 130 (interrupted).

---

## Constraints and Non-Goals

- **No automatic rollback on failure** — a failed batch is not automatically reversed.  Rollback
  is a deliberate operator action (`strata promote rollback` or `strata deploy run` on the previous
  wave).
- **No cross-batch ordering** — this ADR does not address dependencies *between* different
  promotions or rings.  Each batch is scoped to one ring + one promotion.
- **No scheduling** — cron-style scheduling of wave deployments is not in scope.  Use a CI/CD
  pipeline scheduler.
- **Solution registry required** — discovery depends on `.strata/solution.json`.  Ad-hoc
  deployment files not registered in the solution are not discovered.

---

## Open Questions

- **Option C (parallel groups) timing** — When should `deployment_wave` be added to the promotion
  ring model?  This field enables proper group-based parallelism.  Candidate: ADR 0038 or as a
  follow-up patch to ADR 0011 models.
- **`--complete` semantics in mass-wave mode** — Should `--complete` (advance ring lock + delete
  wave locks) be allowed as a mass-wave trigger?  Or should `--complete` remain a `strata promote`
  operation only?  Current lean: keep `--complete` in `strata promote`; mass-wave only handles
  deployment execution.
- **Re-entrancy** — If a batch is interrupted mid-way, should a re-run skip already-succeeded
  deployments?  Would require a batch state file.  Deferred.

---

## Implementation Status

| Phase | Description                                                         | Status     | Completed |
| ----- | ------------------------------------------------------------------- | ---------- | --------- |
| M-1   | Discovery: scan solution registry, match ring + promotion + labels  | 🔲 TODO    | —         |
| M-2   | Sequential execution loop (`RunBatchDeployCommand`)                 | 🔲 TODO    | —         |
| M-3   | `--stagger N` inter-deployment delay                                | 🔲 TODO    | —         |
| M-4   | `--continue-on-error` flag                                          | 🔲 TODO    | —         |
| M-5   | Batch summary output (console + JSON)                               | 🔲 TODO    | —         |
| M-6   | SIGTERM handling for batch context                                  | 🔲 TODO    | —         |
| M-7   | Parallel groups (`deployment_wave` field, Option C)                 | 🔲 DEFERRED | —        |
