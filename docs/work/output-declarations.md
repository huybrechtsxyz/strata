# Output Declarations — Work

- Status: proposed — design sketch only, nothing built, no decisions finalized
- Last updated: 2026-10-12
- Split out from: [provisioning-injection-model.md](../archive/provisioning-injection-model.md)'s
  "Outputs declaration" remaining-work item — the one piece of that doc's
  Interface/Injection/Grant/Translation/Context design that survived a
  2026-10-12 re-check against the real codebase; everything else there
  turned out to be a dead direction, superseded by ADR-0035.

## Overview

`${output:step.key}` is validated today at exactly one level: is `step` a
real, upstream (`depends_on`-reachable) execution step
(`_upstream_step_names()`). The literal `key` half is **never** validated
against anything — not at `strata validate` time, not even at `deploy run`
time until the referencing step's tool actually runs and the token
substitution fails to find a value. A typo'd `${output:step1.vm_ipp}`
passes validation cleanly and only surfaces deep inside a real deploy,
exactly the "did not resolve to a value" `ValueError` class of bug
[cross-run-step-output-delivery.md](cross-run-step-output-delivery.md)
already fixed for the *step*-level case (`_merge_durable_outputs()`) but
never for the *key* level.

This is the same shape of problem Interface originally existed to solve
for a provisioner's own *input* variables (`provisioning-injection-model.md`:
"a required-but-missing Interface variable is a hard build error, not a
silent omission") — just never built for a step's *output* keys, flagged
in that doc's own Remaining Work list as "a ground truth to validate
`${step:...}`'s key against, symmetric to Interface... open, unscheduled"
and never picked up since.

## Current Design / Progress

Nothing built. Sketch only, below — deliberately not decided in enough
detail to build yet (see Open Questions).

### Proposed shape

A new optional field, tentatively on `ProvisioningStepModel` (keyed by
step name, matching how `${output:step.key}` itself is keyed — not on
`ProvisionerModel`, which is the reusable tool definition, not the
per-step execution instance a token actually names):

```python
outputs: list[VariableKey] | None = Field(
    None,
    description="Output keys this step is declared to produce (e.g. real "
    "Terraform 'output' block names). When set, every '${output:THIS_STEP.key}' "
    "reference elsewhere in the solution is validated against this list at "
    "'strata validate' time, catching a typo'd/nonexistent key before any "
    "deploy runs. Omit to keep today's fully dynamic behavior (key "
    "existence only discovered at actual 'deploy run' time).",
)
```

Optional, not required — `None` keeps today's behavior completely
unchanged for any workspace that doesn't opt in, matching this session's
own precedent for every other new governance-adjacent field
(`allowed_key_value_stores`, `output_store_max_age`): a lever that costs
nothing when unset, not a forced migration.

### Enforcement: a new Phase 2 semantic check

Shape closely mirrors `value_references.py`'s existing
`resolve_document_value_references()` — walk every document in the
solution (not just the workspace's own fields: a `${output:...}` token
can appear inside a DNS/network/firewall document claimed by a step, not
only inside the workspace document itself, same reach that function
already needs for `${value:...}`), extract every `${output:step.key}`
token, resolve `step` to its real `ProvisioningStepModel`, and — only
when that step's `outputs` is set — check `key` is a member. A dangling
`step` name, or a step whose `outputs` is unset, is not re-reported here
(existing checks / today's fully-dynamic behavior already cover those
cases respectively) — same "skip rather than guess" rule every other
check in `semantic_checks.py` already follows.

### Sourcing the declaration: manual vs. auto-derived

Two real options, not yet decided between:

- **Manual** — the workspace author writes `outputs: [vm_ip]` by hand,
  alongside the step. Simple, zero parsing complexity, works uniformly
  for every tool (Terraform/Helm/Compose alike, none of which need a
  real "outputs" concept of their own for this to work). Real cost: it's
  a second ground truth that can drift from reality — a `.tf` file
  gaining/losing an `output` block without the declaration being updated
  defeats the point (the author now gets a false "undeclared" validate
  error, or worse, the whole check silently stops catching anything
  because it's stale-permissive).
- **Auto-derived** — parse real `output "X" {}` blocks out of the synced
  `.tf` source, mirroring Interface's own (abandoned) auto-derivation
  idea for *input* variables. Stays in sync automatically, but
  reintroduces the exact complexity `provisioning-injection-model.md`'s
  "Interface parse-failure policy" wrestled with and never got built:
  dynamically-generated output names (`for_each`/`count`), parse
  failures on a valid-but-unsupported HCL construct, and a tool-specific
  mechanism that simply doesn't exist for Helm/Compose at all.

**Leaning towards manual-only as the real first cut**, deferring
auto-derivation the same way this repo already defers several other
real-but-not-yet-evidenced mechanisms (OCI/Helm remote fetch in
[remotes.md](remotes.md), several of ADR-0023's categories) — revisit
auto-derivation only if manual drift turns out to be a real, observed
problem, not preemptively.

### Deliberately out of scope: sensitivity

An output's `sensitive` flag is only known at real `apply` time
(Terraform's own `-json` output reports it per-key) — `outputs:` should
only ever assert *"this key will exist"*, never *"and it's/isn't
sensitive"*, to avoid inventing a second, independently-driftable ground
truth for sensitivity specifically. `collect_step_outputs()`'s own
dynamic `sensitive_keys` return value stays the sole authority for that,
unchanged.

## Related Decisions

- [provisioning-injection-model.md](../archive/provisioning-injection-model.md) —
  Interface's original "declared ground truth, hard-fail if missing"
  precedent this design mirrors, for outputs instead of inputs.
- [cross-run-step-output-delivery.md](cross-run-step-output-delivery.md) /
  [ADR-0036](../decisions/0036-cross-run-key-value-output-store.md) — the
  feature whose validate-time coverage this would close the remaining gap
  in (today: step-level pre-flight exists via `_merge_durable_outputs()`;
  key-level does not, at any stage).
- `src/strata/controllers/value_references.py`'s
  `resolve_document_value_references()` — the closest existing structural
  precedent for "walk every document, solution-wide, for a token kind and
  validate it" this check would copy the shape of.

## Remaining Work / Open Questions

- **Field placement** — tentatively `ProvisioningStepModel.outputs`
  (matches how `${output:step.key}` is keyed, by step name). Not fully
  settled — worth confirming no real case needs two different steps
  running the *same* provisioner to report different declared outputs
  (if so, step-level is clearly right; if outputs are purely a property
  of the underlying tool/source, provisioner-level might fit better).
- **Required vs. optional** — leaning optional/opt-in, not required,
  consistent with this session's own precedent for every other new
  governance-adjacent field. Not yet confirmed against a real user need.
- **Manual vs. auto-derived sourcing** — leaning manual-only for a first
  cut, auto-derivation deferred (see above). Not yet decided for certain.
- **A reference to an *undeclared* key, on a step that HAS declared some
  outputs** — the entire point, should be a hard validate-time error.
  Uncontroversial, but not yet precisely worded (exact diagnostic code/
  message).
- **Does this need its own ADR, or is it a small enough addition to land
  without one** — likely the latter (one optional field + one semantic
  check, no new capability/concurrency model the way ADR-0036 needed),
  but worth a final call once it's actually being built.

## Changelog

- 2026-10-12: Created, split out of `provisioning-injection-model.md`'s
  "Outputs declaration" remaining-work item after a re-check found it was
  the one piece of that doc's design that wasn't dead. Design sketch
  only — nothing built, no decisions finalized.
