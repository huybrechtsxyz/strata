# Output-Store Lock Audit — Work

- Status: draft — not started, low priority
- Last updated: 2026-10-12
- Split out from: [cross-run-step-output-delivery.md](cross-run-step-output-delivery.md)
  Implementation Plan step 5, so it isn't lost once that doc's own status
  line stops mentioning it as "not yet built"

## Overview

`DeploymentOutputStore.record()` (built, docs/work/
cross-run-step-output-delivery.md) already has real write-safety via its
own CAS-retry read-modify-write loop — this doc is not about fixing a
correctness gap, there isn't one. It's about giving an operator the same
**audit visibility** into those writes that `ManifestLockReferenceModel`
already promises for a Terraform provisioner's own native remote-backend
state lock (azurerm/s3/consul/gcs/local) — who held it, when, for how
long — but has never actually recorded for anything, including Terraform
itself (confirmed: zero real references outside the model/docs today).

**Not urgent:** for the primary IaC use case (plan/apply state
consistency), Terraform's own backend locking already does the real
locking — this doc only covers the separate, optimistic-concurrency
write path for cross-run step *outputs*, a durability/observability
nice-to-have layered on top, not a safety-critical gap. Kept as a short
work doc specifically so it doesn't get silently dropped, not because
it's blocking anything.

## Current Design / Progress

Nothing built. `ManifestLockReferenceModel` and `DeploymentManifestModel.
lock` are commented out in place in `audit_manifest_model.py` (not
deleted), with a comment pointing back here.

Planned shape, reusing the existing model instead of inventing a new one
(`lock_id`/`backend`/`acquired_at`/`released_at`/`holder`/`hostname` —
exactly as already defined, commented out):

- One entry per **successful** `DeploymentOutputStore.record()` write —
  not per failed CAS attempt (too chatty under real contention, and
  `released_at` already implies a normally-completed event).
- `lock_id`: a fresh UUID per write.
- `backend`: the resolved output-store integration's own `type` string.
- `holder`: `f"{step.name}@{execution_id}"`.
- `hostname`: `socket.gethostname()`.
- `acquired_at`/`released_at`: the write's own start/end timestamps —
  this is a short-lived optimistic lock, not a held pessimistic one, so
  both are close together by construction.

## Related Decisions

- [cross-run-step-output-delivery.md](cross-run-step-output-delivery.md) —
  the parent feature; `DeploymentOutputStore.record()`'s own CAS-retry
  loop is the thing this doc would start recording.
- [key-value-store-integration.md](key-value-store-integration.md) — the
  `KeyValueStoreIntegration` capability `DeploymentOutputStore` is built
  on.

## Remaining Work / Open Questions

- Uncomment `ManifestLockReferenceModel` and `DeploymentManifestModel.lock`
  in `audit_manifest_model.py`.
- Give it a real producer: one entry appended per successful
  `DeploymentOutputStore.record()` call in `deploy_controller.py`,
  collected into the manifest the same way `ManifestStageModel`/other
  satellite references already are.
- Decide whether a *failed* CAS exhaustion (`SystemError` after
  `_MAX_CAS_RETRIES`) deserves its own audit entry too, or only the
  diagnostics warning it already gets — leaning towards "warning is
  enough," since an unreleased/failed lock record would need a
  different shape than this model's own "normally completed" framing.
- Decide whether this is worth its own small ADR, or stays folded into
  whatever (if anything) the parent doc's own "does this need its own
  ADR?" question resolves to.

## Changelog

- 2026-10-12: Created, extracted from cross-run-step-output-delivery.md's
  Implementation Plan step 5 — nothing built yet, kept deliberately short.
