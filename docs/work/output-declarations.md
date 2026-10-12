# Output Declarations — Work

- Status: proposed — design sketch only, nothing built, no decisions
  finalized. **Leading direction (2026-10-12): a dynamic per-step
  pre-flight check inside `deploy_controller.py`, requiring no new schema
  field at all** — found to be strictly better than the original
  declared-schema-field sketch (no double-bookkeeping/drift risk, checks
  real collected data instead of a separate claim about it). That
  original sketch is kept below as an analyzed alternative, not deleted.
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

### Leading direction (2026-10-12): a dynamic per-step pre-flight check, no new schema field

Found while discussing why `strata validate`/`build run` can't do this
check at all: unlike `${var:}`/`${secret:}`, whose valid key set is known
in advance (declared in Environment documents, checked once up front by
`resolve_values()`'s own real pre-flight), `${output:...}`'s real key set
is only ever known once a step has actually run and produced real,
collected outputs (or a durable record exists) — there is nothing to
check against before that, short of a declared ground truth (the
schema-field idea below, with its own double-bookkeeping cost).

**The check belongs in `deploy_controller.py`'s existing per-step loop,
not a new field at all** — exactly where `tokens = {**resolved.values,
**visible_outputs}` is already computed, right after `_merge_durable_
outputs()` runs, and before `rewrite_strata_payload()`/any file write/any
tool invocation for *that* step. At that exact point, `tokens` already
*is* the real, merged ground truth (this invocation's in-memory outputs
∪ the durable cross-run record) — a new check there extracts every
`${output:...}` key this step's own payload actually references and
verifies each is present in `tokens`, before the step proceeds. No
schema field, no manual declaration, no auto-derivation, no drift risk
at all — it checks against what's *really* available, not a separate
claim about what should be available.

**Semantics, settled by working through a concrete example** (step1 ->
step2, step2 references `${output:step1.vm_ip}`):
- Step1 already ran this invocation (or a durable record exists) —
  nothing to warn or error *on step1* regardless of which keys it
  produced. It succeeded on its own terms; whatever it really produced
  is ground truth, full stop.
- The check and any resulting failure belong entirely to the
  *referencing* step (step2) — if step2's own payload needs a key that
  isn't in the merged `tokens`, that is a hard **error** for step2,
  before step2's own tool ever runs, not a warning: step2 genuinely
  cannot proceed correctly without it, the same "hard pre-flight
failure, never silent" standard `_merge_durable_outputs()` already
  applies to the stale/unreachable durable-record case.

**A separate, real, already-existing bug found while confirming this**
(independent of whether this gets built): today, this exact case — a
referenced key genuinely missing from `tokens` — does not fail cleanly
at all. `resolve_value_tokens()` raises a bare `ValueError` (not a
`StrataError` subclass), which is never caught anywhere between there and
`command_run()` (which only catches `StrataError`) — so a typo'd token of
*any* kind (`var`/`secret`/`feature`/`output`/`value`), not just `output`,
crashes the CLI with a raw Python traceback today instead of a clean
diagnostic and exit code. Worth fixing on its own regardless of this
doc's own direction, since a proper pre-flight check would also need to
stop relying on that same uncaught exception as its only failure mode.

**Status: fixed (2026-10-12).** Found three unprotected call sites of this
exception, all in the real `deploy run` path — `ComposeIntegration.
deploy_namespace()` (`compose.py`) already caught it correctly and was the
template for the fix:
- `HelmIntegration.deploy_namespace()` (`helm.py`) — `resolve_module_values()`
  was uncaught; now wrapped exactly like Compose's own call, reporting a
  `Diagnostic` and skipping that module.
- `deploy_controller.py`'s `provisioner.backend.configuration` resolution
  (`resolve_value_tokens_in_mapping()`) — uncaught; now reports a
  `Diagnostic` and stops the run (same severity as `_merge_durable_outputs()`
  failing).
- `deploy_controller.py`'s unified `strata.auto.tfvars.json` rewrite
  (`rewrite_strata_payload()`) — uncaught; same fix.

No change to `value_tokens.py` itself or to `command_run()` — this module
deliberately stays a pure, kind-agnostic string utility (per its own module
docstring), and every other `ValueResolutionError`/`IntegrationError` in
this codebase is likewise caught and converted to a `Diagnostic` at its own
call site rather than taught to `command_run()`'s `StrataError`-only
handler, so this fix matches that existing convention rather than
introducing a new one. One pre-existing test
(`test_deploy_run_rejects_output_token_from_non_dependency`) had asserted
the bare `ValueError` as if it were correct behavior — updated to assert
the clean diagnostic instead, a real instance of "a passing test enshrining
a bug." New regression tests: `test_deploy_namespace_unresolvable_token_
reports_a_diagnostic_and_skips` (Helm), `test_deploy_run_reports_a_
diagnostic_when_a_backend_configuration_token_is_unresolved`, and
`test_deploy_run_reports_a_diagnostic_when_a_strata_payload_token_is_
unresolved`. Full suite green: 2416 passed, mypy/ruff/lint-imports clean.

This direction needs no "sourcing" decision (manual vs. auto-derived) at
all — see below for why that whole dilemma only applies to the
declared-schema-field alternative, which this supersedes as the
recommended approach (kept below for the record, not deleted — the
GitHub Actions comparison and drift analysis there remain valid
reasoning, just no longer the leading direction).

### Alternative considered: a declared schema field

The original sketch, before the dynamic-check direction above was found
to need no schema at all. Kept for the record since the manual-vs-
auto-derived tradeoff analysis remains valid background reasoning (e.g.
for a possible future stricter mode that wants validate-time, not just
deploy-time, coverage).

#### Proposed shape

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

#### Enforcement: a new Phase 2 semantic check

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

#### Sourcing the declaration: manual vs. auto-derived

Two real options, not yet decided between:

- **Manual** — the workspace author writes `outputs: [vm_ip]` by hand,
  alongside the step. Simple, zero parsing complexity, works uniformly
  for every tool (Terraform/Helm/Compose alike, none of which need a
  real "outputs" concept of their own for this to work). Same shape as
  GitHub Actions' own `jobs.<job>.outputs:` map, which is likewise a
  second declaration independent of the step that actually produces the
  value (`echo "name=value" >> "$GITHUB_OUTPUT"`) — double bookkeeping
  is the normal, accepted shape for this problem elsewhere, not a wart
  unique to this design.

  Real cost: the declaration can drift from the real `.tf` source, in
  two directions, not symmetric:
  1. **Declaration lags behind reality** (a real new `output` block
     exists, `outputs:` wasn't updated) — a downstream reference to that
     key gets a **false, loud validate-time rejection**. Annoying, blocks
     until the declaration catches up, but not dangerous — just
     inconveniently wrong, in the safe direction.
  2. **Declaration is stale** (`outputs:` still lists a key whose `.tf`
     block was renamed/removed) — validate passes (the declared key still
     matches), but `deploy run` still fails later exactly as it does
     today. No worse than the status quo; this check simply didn't help
     in that direction.

  Neither direction is silent or dangerous — worth comparing against
  GitHub Actions' own real, well-known failure mode for the identical
  double-bookkeeping shape: forgetting the job-level `outputs:` mapping
  there makes `needs.job.outputs.x` **a silent empty string at runtime,
  no error at all** — strictly worse than either drift direction above.
- **Auto-derived** — parse real `output "X" {}` blocks out of the synced
  `.tf` source, mirroring Interface's own (abandoned) auto-derivation
  idea for *input* variables. Stays in sync automatically, removing the
  double-bookkeeping burden entirely for Terraform specifically, but
  reintroduces the exact complexity `provisioning-injection-model.md`'s
  "Interface parse-failure policy" wrestled with and never got built:
  dynamically-generated output names (`for_each`/`count`), parse
  failures on a valid-but-unsupported HCL construct, and a tool-specific
  mechanism that simply doesn't exist for Helm/Compose at all.

  **Must be static regex/HCL scanning, not asking Terraform itself**
  (checked directly, 2026-10-12): `terraform validate`/`init` never touch
  output values at all; the only real command that enumerates them
  (`terraform plan -json` / `terraform show -json` on a saved plan)
  needs providers initialized and, for most real cloud providers, live
  credentials — a genuine network operation, not a free static check.
  That rules it out on two independent grounds: `build run` is
  architecturally no-network-by-default (ADR-0022 D4, "build run
  renders; it does not execute"), and `strata validate` is static
  everywhere else in this codebase. The only auto-derive approach that
  actually fits is scanning synced `.tf` source text for `output "X" {}`
  blocks via regex — the exact same technique `terraform.py`'s
  `_STRATA_VARIABLE_DECLARATION` already uses for `variable "strata" {}`
  detection, not a real `terraform` invocation at all.

**Leaning towards manual-only as the real first cut**, deferring
auto-derivation the same way this repo already defers several other
real-but-not-yet-evidenced mechanisms (OCI/Helm remote fetch in
[remotes.md](remotes.md), several of ADR-0023's categories) — revisit
auto-derivation only if manual drift turns out to be a real, observed
problem, not preemptively.

#### Deliberately out of scope: sensitivity

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
  precedent the alternative (schema-field) direction mirrors, for outputs
  instead of inputs — not needed by the leading (dynamic) direction, which
  checks real collected data instead of a declared ground truth.
- [cross-run-step-output-delivery.md](cross-run-step-output-delivery.md) /
  [ADR-0036](../decisions/0036-cross-run-key-value-output-store.md) — the
  feature this extends: `_merge_durable_outputs()` already does the
  step-level pre-flight check; the leading direction here is the
  key-level check immediately following it, same function, same
  invocation, no new mechanism.
- `src/strata/controllers/value_references.py`'s
  `resolve_document_value_references()` — the closest existing structural
  precedent for "walk every document, solution-wide, for a token kind and
  validate it", relevant to the alternative (schema-field) direction
  specifically, which would need solution-wide reach; the leading
  (dynamic) direction only needs to look at one step's own payload at a
  time, already in scope inside the existing loop.

## Remaining Work / Open Questions

- **Leading direction still needs: the exact key-extraction helper** —
  something like `_referenced_output_keys(payload) -> set[str]`, mirroring
  `_contains_output_token()`'s existing recursive walk but collecting
  every `${output:...}` key found rather than just testing presence, so
  the new check can diff it against `tokens`. Not yet written.
- **Exact diagnostic wording** for a step referencing a missing key —
  should name the referencing step, the missing key, and ideally the
  upstream step it was expected to come from (parseable directly from
  the token's own `"STEP.KEY"` shape). Not yet precisely worded.
- **The separate uncaught-`ValueError` bug** (see above) — worth fixing
  regardless of whether this check gets built; a proper pre-flight check
  would also remove the only path that currently reaches it for the
  `output` token kind specifically, but `var`/`secret`/`feature`/`value`
  tokens would still be able to hit it today.
- **Does this need its own ADR** — leaning no, more strongly than the
  schema-field alternative did: this is an extension of an existing,
  already-built function (`_merge_durable_outputs()`'s own neighborhood),
  not a new capability/field/concurrency model.
- *(Alternative-direction-only, kept for completeness, not current
  priorities):* field placement, required-vs-optional, and manual-vs-
  auto-derived sourcing for the declared-schema-field idea — see
  "Alternative considered" above for that analysis in full.

## Changelog

- 2026-10-12: Created, split out of `provisioning-injection-model.md`'s
  "Outputs declaration" remaining-work item after a re-check found it was
  the one piece of that doc's design that wasn't dead. Design sketch
  only — nothing built, no decisions finalized.
- 2026-10-12: Sharpened the manual-vs-auto-derived tradeoff, prompted by
  a direct comparison to GitHub Actions' own `jobs.<job>.outputs:`
  pattern — confirmed this double-bookkeeping shape is the industry-normal
  one for this exact problem, not a wart. Corrected an overstated claim in
  the original draft ("drift silently stops catching anything") with the
  two real, non-symmetric drift directions, neither of which is actually
  silent or dangerous — both beat GitHub Actions' own real failure mode
  (a forgotten `outputs:` entry there is a silent empty string at runtime,
  no error at all).
- 2026-10-12: Ruled out `terraform plan -json`/`show -json` as the
  auto-derivation mechanism — confirmed `validate`/`init` never touch
  output values at all, and `plan` needs live provider credentials for
  most real providers, a genuine network operation that conflicts with
  `build run`'s no-network-by-default rule (ADR-0022 D4) and `strata
  validate`'s static-everywhere-else convention. Auto-derivation, if ever
  built, must be static regex/HCL scanning instead (same technique
  `terraform.py`'s `_STRATA_VARIABLE_DECLARATION` already uses), never a
  real `terraform` invocation.
- 2026-10-12: **Found a strictly better direction, superseding the
  declared-schema-field sketch as the leading recommendation** — a
  dynamic per-step pre-flight check inside `deploy_controller.py`'s
  existing loop (right after `_merge_durable_outputs()`/`tokens` are
  computed), checking real collected data instead of a separate declared
  claim about it. Needs no schema field, no manual-vs-auto-derived
  sourcing decision, no drift risk. Along the way, found a separate, real,
  already-existing bug: a missing value-token key of *any* kind raises an
  uncaught `ValueError` today (not a `StrataError`), crashing the CLI with
  a raw traceback instead of a clean diagnostic — worth fixing regardless
  of this doc's own direction. The original schema-field sketch is kept
  as an analyzed alternative, not deleted. No code changed; documentation
  only.
