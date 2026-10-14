# Output Declarations — Design

- Status: current
- Last updated: 2026-10-14

## Overview

`${output:STEP.KEY}` lets a deploy-time field reference another execution
step's collected output (e.g. a VM's IP produced by one Terraform step,
consumed by a DNS record another step manages). Before this feature, only
`STEP` was ever validated — is it a real, upstream (`depends_on`-reachable)
execution step. The literal `KEY` half was never checked against anything:
a typo'd `${output:step1.vm_ipp}` passed `strata validate` cleanly and only
surfaced deep inside a real `deploy run`, once the referencing step's tool
actually tried and failed to substitute it.

This closes that gap with a dynamic, per-step/per-module pre-flight check
at `deploy run` time — no new schema field, no declared ground truth to
keep in sync. It reuses `deploy_controller.py`'s own already-computed,
real `tokens` dict (this invocation's in-memory outputs, merged with any
durable cross-run record) as the check's source of truth.

## Current Design

**`referenced_output_keys(node: object) -> set[str]`**
(`strata/utils/value_tokens.py`) walks any dict/list/str payload
recursively and collects every `${output:STEP.KEY}` token's `"STEP.KEY"`
string, ignoring every other token kind (`var`/`secret`/`feature`/`value`).
It lives in `utils`, not next to its structural sibling
`_contains_output_token()` in `deploy_controller.py` (a presence test used
for a different, build-time purpose) — this codebase's own layering
(`commands > controllers > services > integrations > models > utils >
logging`, ADR-0003) means `strata.integrations` can never import from
`strata.controllers`, and both Terraform's and Helm/Compose's own checks
need this helper.

**Terraform path** (`deploy_controller.py`, inside the per-step deploy
loop): right after `tokens` is computed, a check unions
`referenced_output_keys()` over `provisioner.backend.configuration` and
`step_payload` (the step's own view of the unified `strata.auto.tfvars.json`
payload), and verifies every key referenced is already present in `tokens`.
Every missing key is reported as its own diagnostic — naming the
referencing step, the expected upstream producer, and every key that *is*
actually available from that producer — before any resolution or
file-write is attempted. A missing key aborts the whole step (the same
severity the step-level `_merge_durable_outputs()` pre-flight already
uses, one level up).

**Helm/Compose paths** (`helm.py`/`compose.py`'s own `deploy_namespace()`)
run the identical check, but at different granularity, matching each
integration's own existing shape:

- **Helm** loops per-module (one `helm upgrade` each) — the check runs
  inside that loop, scanning `referenced_output_keys(_render_values(item.module))`
  right before `resolve_module_values()`. A missing key reports a
  diagnostic and skips *only that module*; every other module in the
  namespace still deploys.
- **Compose** merges every module into **one** `docker-compose.yml` before
  this point — there is no single offending module left to skip, so the
  check scans the merged document once and aborts the whole namespace
  deploy on a miss.

Both integrations' severity matches their own pre-existing
`ValueError`-catch precedent at the same call site (see History) — not a
new, invented model.

All three call sites run under `dry_run` too, not just a real deploy —
placed before any dry-run-specific branching, so a preview gets the same
protection a real deploy does (confirmed by dedicated tests: the
underlying tool — `terraform`/`helm`/`docker` — is never invoked at all
when the check fails, under either mode).

## Related Decisions

- [ADR-0036](../decisions/0036-cross-run-key-value-output-store.md) —
  `DeploymentOutputStore`, the durable cross-run output record this
  feature's `tokens` dict already includes; this feature adds no new
  mechanism on top of it, only a new check against data it already merges.
- [provisioning-injection-model.md](../archive/provisioning-injection-model.md) —
  where this gap was originally identified (Interface's own "required but
  missing is a hard build error" precedent, never built for a step's
  *output* keys) before being split out into its own design thread.

## History

- Considered a declared schema field (`ProvisioningStepModel.outputs:
  list[str]`, checked at `strata validate` time) before building anything —
  rejected in favor of the dynamic check: a declared ground truth can drift
  from the real `.tf` source in either direction (stale-but-safe false
  rejection, or stale-and-silent pass-through), while the dynamic check has
  no separate claim to drift from at all, since it validates against the
  same real, collected data every resolution call already uses.
  Auto-deriving that declaration from real `.tf` `output` blocks (via the
  `python-hcl2` parser already used elsewhere in this repo for a
  structurally adjacent task) was researched as a way to avoid manual
  drift, but the schema-field direction itself was dropped before that
  mattered.
- Found and fixed a separate, real, pre-existing bug while building this:
  a missing value-token key of *any* kind (not just `output`) raised a bare
  `ValueError` (never a `StrataError`), crashing the CLI with a raw
  traceback instead of a clean diagnostic, at three unprotected call sites
  (`HelmIntegration.deploy_namespace()`, `deploy_controller.py`'s backend
  config resolution, and its unified tfvars rewrite). Fixed independently
  of this feature's own pre-flight check, which needed clean failure
  behavior to build on top of. One pre-existing test had asserted the bare
  `ValueError` as correct behavior — a real instance of a passing test
  enshrining a bug.
- A code review after shipping found no functional bugs, only minor,
  deliberately-not-changed observations: Helm's own rendered-values payload
  is computed twice (once by this check, once inside
  `resolve_module_values()`) — an accepted cost for keeping both functions
  pure; the "available keys" diagnostic hint could, in a contrived naming
  collision, list a `${var:}`/`${secret:}`/`${feature:}` key as if it were
  a real output, inherited from the pre-existing `tokens` merge shape, not
  introduced here; and `deploy_controller.py`'s `step_payload` computation
  is now unconditional rather than gated on `ENV_VAR_PREFIX`, which is the
  correct design (the check must see every Terraform-shaped step's real
  payload regardless of delivery mechanism) but worth remembering if a
  future non-container, `ENV_VAR_PREFIX = None` tool is ever added. One
  genuine test gap the review did find — no test proved the check still
  fires, and the underlying tool is never invoked, under `dry_run=True` —
  was closed with a regression test per integration.
