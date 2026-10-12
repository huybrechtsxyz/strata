# Deploy-Time Output Sensitivity & Step-Output Persistence Gaps — Work

- Status: **Archived (2026-10-12) — both gaps resolved, nothing pending
  in this doc's own scope.** Gap 1 done (2026-10-11). Gap 2 split out and
  fully built — see [cross-run-step-output-delivery.md](cross-run-step-output-delivery.md)
  (archived alongside this doc) and [ADR-0036](../decisions/0036-cross-run-key-value-output-store.md).
- Last updated: 2026-10-11

## Overview

Two independent, real gaps found during [unified-terraform-projection.md](../design/unified-terraform-projection.md)'s
own review (2026-10-10), neither caused by nor fixed by that redesign —
split out here so they aren't lost once that doc's own Remaining Work
section was collapsed into its History. Both concern `deploy_controller.py`'s
handling of `${output:...}` tokens and cross-step Terraform outputs.

## Current Design / Progress

1. **DONE — `${output:...}` tokens are now treated as secret-shaped when
   the real Terraform output is `sensitive = true`.**
   `collect_step_outputs()` now returns `(values, sensitive_keys)` —
   reading `entry["sensitive"]` from `terraform output -json`, which real
   Terraform always returns the true value alongside regardless of that
   flag. `resolve_value_tokens_tracking_secrets()`/`rewrite_strata_payload()`
   gained an optional `sensitive_output_keys: set[str]` parameter, keyed
   `"step.key"` exactly like an `${output:...}` token's own key half, so
   an `${output:}` reference to a sensitive output is now sentineled in
   `strata.auto.tfvars.json` and delivered via `TF_VAR_strata_secrets`
   like any other secret — never written to disk in plain text. Covered
   by a unit test (`collect_step_outputs` surfaces `sensitive_keys`) and
   an end-to-end `deploy_run()` regression test (a DNS record's value
   referencing a sensitive output ends up sentineled, the real value only
   reachable via the env var). Full suite green after.
2. **Split out — a step's collected outputs are never persisted across
   separate `deploy run` invocations.** What started as "wire this into
   the dormant `outputs_artifact`/`DeploymentOutputsModel` schema" turned,
   through discussion, into a much bigger question (pluggable remote
   storage for cross-pipeline hand-off, concurrency, a v1 precedent) —
   tracked in its own doc now:
   [cross-run-step-output-delivery.md](cross-run-step-output-delivery.md).

## Related

- [unified-terraform-projection.md](../design/unified-terraform-projection.md) —
  where both gaps were found, during an unrelated redesign's own review.
- [cross-run-step-output-delivery.md](cross-run-step-output-delivery.md) —
  gap 2's own, now much larger, design-in-progress doc.

