# Deploy-Time Output Sensitivity & Step-Output Persistence Gaps — Work

- Status: draft
- Last updated: 2026-10-11

## Overview

Two independent, real gaps found during [unified-terraform-projection.md](../design/unified-terraform-projection.md)'s
own review (2026-10-10), neither caused by nor fixed by that redesign —
split out here so they aren't lost once that doc's own Remaining Work
section was collapsed into its History. Both concern `deploy_controller.py`'s
handling of `${output:...}` tokens and cross-step Terraform outputs.

## Current Design / Progress

Not started. Two distinct findings, likely two distinct fixes:

1. **`${output:...}` tokens are never treated as secret-shaped, regardless
   of the referenced Terraform output's own `sensitive = true`.**
   `resolve_value_tokens_tracking_secrets()`'s `_is_secret_shaped()`
   checks only a token's *kind* (`kind == "secret"`) — an
   `${output:step.key}` token is never secret-shaped by that check no
   matter what the referenced value actually is. `collect_step_outputs()`
   compounds this: it reads `entry["value"]` from `terraform output
   -json` and ignores `entry["sensitive"]` entirely, even though real
   Terraform always returns the true value (never redacted) in `-json`
   output alongside that flag. Net effect: a workspace author who marks a
   Terraform output `sensitive = true` and references it elsewhere via
   `${output:...}` gets that real value written straight into a plain
   on-disk `*.auto.tfvars.json` today — never routed through the
   `${secret:}`-only, env-var-safe delivery path. Fix needs to thread a
   `sensitive: bool` alongside each collected output through the flat
   `tokens: dict[str, str]` pipeline (which currently carries no
   per-key metadata at all) so `_is_secret_shaped()`/`find_secret_paths()`
   can treat it the same as a `${secret:}` leaf.
2. **A step's collected outputs are never persisted to disk.**
   `deploy_controller.py`'s `step_outputs: dict[str, dict[str, str]]` is
   purely in-memory, rebuilt fresh every `deploy run` by calling
   `collect_step_outputs()` only for steps inside *this* invocation's
   `resolved_steps` loop. A scoped `deploy run` (the code already has a
   `scope` concept) that excludes an upstream dependency leaves that
   upstream's outputs completely unavailable to a dependent step this
   run, even though the upstream's real Terraform state/outputs still
   exist on disk and `terraform output -json` could be called against
   its directory on demand regardless of whether it ran this invocation.
   A dormant, designed-but-never-wired schema already exists for roughly
   this: `audit_manifest_model.py`'s `outputs_artifact:
   ManifestOutputsReferenceModel | None` (path + sha256, pointing at a
   separate `DeploymentOutputsModel` file, per
   [audit-trail.md](audit-trail.md)) — confirmed zero real writer
   anywhere today. Wiring `collect_step_outputs()`'s result into that
   file would close both this gap and the dormant schema field in one
   change, and double as the audit trail it was originally designed for.

## Open Questions

- Does fixing (1) require a schema change to the flat `tokens: dict[str,
  str]` pipeline (e.g. a parallel `sensitive_keys: set[str]`), or can it
  reuse the existing `secrets`-tracking return value some callers already
  thread through?
- Does (2) belong on `DeploymentOutputsModel` as designed in
  [audit-trail.md](audit-trail.md), or does scoped `deploy run` need its
  own, narrower on-disk cache independent of the audit trail's own
  timing/retention rules?

## Related

- [unified-terraform-projection.md](../design/unified-terraform-projection.md) —
  where both gaps were found, during an unrelated redesign's own review.
- [audit-trail.md](audit-trail.md) — the dormant `outputs_artifact` schema
  field (2) would wire into.
