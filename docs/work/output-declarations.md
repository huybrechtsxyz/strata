# Output Declarations — Work

- Status: **design decided, 4-phase Implementation Plan written
  (2026-10-14); all 4 phases shipped (2026-10-14) — feature complete.**
  Leading direction (2026-10-12): a
  dynamic per-step pre-flight check inside `deploy_controller.py`,
  requiring no new schema field at all — found to be strictly better than
  the original declared-schema-field sketch (no double-bookkeeping/drift
  risk, checks real collected data instead of a separate claim about it).
  That original sketch is kept below as an analyzed alternative, not
  deleted.
- Last updated: 2026-10-14
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
[cross-run-step-output-delivery.md](../archive/cross-run-step-output-delivery.md)
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
  double-bookkeeping burden entirely for Terraform specifically.

  **Must be static regex/HCL scanning, not asking Terraform itself**
  (checked directly, 2026-10-12): `terraform validate`/`init` never touch
  output values at all; the only real command that enumerates them
  (`terraform plan -json` / `terraform show -json` on a saved plan)
  needs providers initialized and, for most real cloud providers, live
  credentials — a genuine network operation, not a free static check.
  That rules it out on two independent grounds: `build run` is
  architecturally no-network-by-default (ADR-0022 D4, "build run
  renders; it does not execute"), and `strata validate` is static
  everywhere else in this codebase.

  **A real HCL2 parser already exists in this repo as a dependency,
  checked directly (2026-10-12) rather than assumed** — `python-hcl2>=8.0`
  (`pyproject.toml`), already used for exactly this kind of structural
  `.tf` scan in `sbom_collectors/terraform_collector.py` (extracting
  `required_providers`/`module {}` blocks). Real, grounded findings from
  that existing code, not speculation:
  - **Not a semantic query API** — `hcl2.load(fh)` parses the full syntax
    tree into a plain nested `dict`/`list`, nothing more. There is no
    `hcl2.get_outputs(file)`; the caller still writes the extraction walk
    by hand, exactly the "HCL language library, then scan it yourself"
    shape guessed at — confirmed, not assumed.
  - **Labeled blocks (`output "name" {}`, same shape as `module "name" {}`)
    turn the label into a dict key**: `{"output": [{"vm_ip": [{"value": ...}]}]}`
    — so extracting output *names* specifically is just `block.keys()`
    (filtered past a `__is_block__` sentinel key the library also emits),
    genuinely simple once you know the shape.
  - **String values keep their surrounding quotes** (`'"some-string"'`,
    not `"some-string"`) — the existing code has its own
    `_strip_hcl_string()` helper to work around this; a real, confirmed
    quirk, not a hypothetical one.
  - **Parse failures raise a catchable exception** (`except Exception`
    in the real code, since the library doesn't document one single
    exception type) — confirmed directly via a real test
    (`test_collect_warns_on_an_unparseable_tf_file`) feeding deliberately
    broken HCL and asserting a clean, caught failure — not a silent
    wrong-result or a crash.
  - **Maturity**: a third-party Lark-grammar reimplementation of HCL2,
    not HashiCorp's own parser — real, documented lag-behind-real-syntax
    risk exists (the same caveat `provisioning-injection-model.md`'s
    "Interface parse-failure policy" already flagged), but it's already a
    proven, working dependency in this exact codebase for a structurally
    adjacent task, not an unproven external risk.
  - **Correction to an earlier overcautious note in this doc**: `output`
    blocks, unlike `resource`/`module`, never support `for_each`/`count`
    in real Terraform — an output's label is always a static string
    literal. The "dynamically-generated output names" concern that
    applies to *variable*/*resource* auto-derivation (Interface's own
    abandoned worry) does not apply to output *names* at all — only to
    an output's *value* expression, which this design never needs to
    evaluate, only the name.

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
- [cross-run-step-output-delivery.md](../archive/cross-run-step-output-delivery.md) /
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

## Implementation Plan

**Baseline already shipped, not part of this plan:** the 2026-10-12
uncaught-`ValueError` fix (see above) already makes a missing/typo'd
`${output:...}` key fail as a clean `Diagnostic` instead of a crash, at
every real call site (Terraform backend config, the unified
`strata.auto.tfvars.json` rewrite, Helm/Compose module values). What
this plan adds on top is strictly a **quality/completeness** improvement,
not a new safety property: today's fix reports the *first* bad key
`resolve_value_tokens()` happens to hit, mid-resolution, with whatever
generic message `value_tokens.py` produces (`"Value token '${output:X}'
... did not resolve to a value"`). This plan collects *every* bad key
across the whole step in one pass, before any resolution attempt, with a
message naming the referencing step and the expected upstream producer.

- [x] **Phase 1 — `referenced_output_keys()` helper, `strata/utils/
  value_tokens.py`.** Done 2026-10-14. Deliberately placed in `utils`, not
  next to its structural sibling `_contains_output_token()` in
  `deploy_controller.py` — this codebase's own layering (`commands >
  controllers > services > integrations > models > utils > logging`,
  ADR-0003) means `strata.integrations` (Phase 3 below) can never import
  from `strata.controllers`; `utils` is the lowest common layer both
  Phase 2 and Phase 3 can reach.
  - `referenced_output_keys(node: object) -> set[str]` — same recursive
    dict/list/str walk shape as `_contains_output_token()`, collecting
    `key` (the full `"STEP.KEY"` string) from every `extract_value_tokens()`
    match where `kind == "output"`, unioned across the whole structure.
    Assumes well-formed tokens only (malformed syntax is already rejected
    at Phase 1 schema validation — same assumption `_contains_output_token()`
    already makes, not a new one).
  - Tests in `test_utils_value_tokens.py` (6 new): empty/no-token payload
    (dict/list/string forms all tested), a single flat-string key, keys
    nested across dicts and lists, mixed token kinds (only `output`
    collected — `var`/`secret`/`feature`/`value` all present in the same
    payload and ignored), a key appearing twice (de-duplicated via
    `set`), scalar non-string leaves (`int`/`bool`/`None`) walked without
    error. Full check suite green: mypy (155 files), ruff, import-linter
    (1 kept, 0 broken), pytest (2532 passed, up from 2526).
- [x] **Phase 2 — wire the check into `deploy_controller.py`'s Terraform
  path.** Done 2026-10-14. `step_payload` (and its `step_claimed`
  dependency) hoisted out of `if integration.ENV_VAR_PREFIX is not None:`
  to run unconditionally, right before the new pre-flight check; the
  existing block now just reuses the already-computed value. The check
  itself runs immediately after that hoist, before `provisioner.backend`'s
  `resolve_value_tokens_in_mapping()` call and before the tfvars rewrite:
  `referenced_output_keys()` over `provisioner.backend.configuration`
  (or `{}`) unioned with `step_payload`, every key not already in `tokens`
  reported as its own `diagnostics.error(...)` (step name, the missing
  `STEP.KEY`, and every key actually available from that upstream step),
  then `return diagnostics` — matching the design sketch exactly, no
  deviations.
  - Runs under `dry_run` (matching `_merge_durable_outputs()`'s own
    dry-run-inclusive behavior — only `smoke_test` short-circuits before
    `tokens` is even computed, unchanged).
  - Tests in `test_deploy_controller.py`: `test_deploy_run_resolves_
    output_token_from_upstream_step` (pre-existing, unchanged — a real
    produced key still resolves and deploys normally); `test_deploy_run_
    reports_a_diagnostic_when_a_backend_configuration_token_is_unresolved`
    (pre-existing, **updated** — now caught by this phase's pre-flight
    check earlier than the old `ValueError`-catch path it used to hit, so
    its assertion was rewritten to the new, more specific message instead
    of the old generic "did not resolve to a value" text; the old
    exception-catch call site is now unreachable for this exact scenario
    but kept as defense-in-depth for other value-token kinds); new
    `test_deploy_run_output_preflight_reports_every_missing_key_in_one_
    pass` — a step referencing **two** distinct missing keys, one via
    `backend.configuration` and one via a DNS document claimed through
    `dns_zones` (part of `step_payload`), both reported in the same
    `deploy run` — the concrete, testable proof this phase adds real
    value over the already-shipped first-error-only fix. Full check suite
    green: mypy (155 files), ruff check + format, import-linter (1 kept,
    0 broken), pytest (2533 passed, up from 2532). Both real-world
    fixtures (`.v2-haven` 52/0, `.v2-cfg` 82/0 + 24 accepted warnings)
    re-validated clean, no regression.
- [x] **Phase 3 — wire the same check into Helm/Compose's own
  `deploy_namespace()`.** Done 2026-10-14. Turned out the two integrations
  need different diagnostic granularity, not identical code: Helm's own
  `deploy_namespace()` loops per-module (one `helm upgrade` each), so its
  check runs inside that loop, right before `resolve_module_values()`,
  scanning `referenced_output_keys(_render_values(item.module))` — a
  missing key reports a diagnostic and `continue`s to the next module
  (skip-only-the-offending-module, matching the already-shipped
  2026-10-12 `ValueError` fix's own severity for this path). Compose
  already merges every module into **one** `docker-compose.yml` before
  this point (`deploy_namespace()`'s own docstring: "exactly one `docker
  stack deploy` per namespace, not per module") — there is no single
  offending module left to skip, so its check scans the merged `document`
  once and `return diagnostics` on a miss, aborting the whole namespace
  deploy — the same granularity its existing `ValueError`-catch path
  already uses, not a new, inconsistent behavior.
  - Both call sites import `referenced_output_keys` from `strata.utils.
    value_tokens` (same Phase 1 helper, no duplication) and use the
    identical diagnostic message shape Phase 2 established (step/module
    name, the missing `STEP.KEY`, every key actually available from that
    upstream step).
  - Tests: `test_deploy_namespace_output_preflight_skips_only_the_
    offending_module` (Helm) — two modules, one referencing a real
    produced key and one referencing a missing key; confirms the good
    module's `helm upgrade` still ran (exactly one real call, naming
    'good' not 'bad') while the bad module is reported and skipped.
    `test_deploy_namespace_output_preflight_aborts_the_whole_namespace`
    (Compose) — one missing key in the merged document; confirms zero
    `docker stack deploy` calls happened (whole namespace aborted, not a
    partial deploy). Full check suite green: mypy (155 files), ruff check
    + format, import-linter (1 kept, 0 broken), pytest (2535 passed, up
    from 2533). Both real-world fixtures (`.v2-haven` 52/0, `.v2-cfg`
    82/0 + 24 accepted warnings) re-validated clean, no regression.
- [x] **Phase 4 — documentation.** Done 2026-10-14. Updated this doc's
  own `Status` line to the fully-shipped state above. Re-checked
  `deploy-command.md`'s own status summary and found two genuinely stale
  spots (its header, and the "Concrete implementation plan" step 3 note)
  both still claiming the literal `KEY` half of `${output:STEP.KEY}` was
  wholly unvalidated ("matches ADR-0006's own admission that Context does
  not solve output validation") — true when written 2026-10-12, superseded
  two days later by Phases 1-3 here; added forward pointers at both
  locations rather than rewriting the historical claims in place (they
  were accurate descriptions of that specific 2026-10-12 fix, only the
  reader's takeaway needed updating). Confirmed (again) no
  `docs/work/gap_fit_v1.md` entry belongs here — this is a new feature
  with no v1 precedent to be "behind" on, not a parity gap. No code
  changed; documentation only.

## Code Review (2026-10-14, post-Phase-4)

A review of the full Phases 1-3 diff (`deploy_controller.py`, `helm.py`,
`compose.py`, `value_tokens.py` + tests) found no functional bugs. Check
placement is correct in all three integrations (after the container-
capability dispatch in `deploy_controller.py`, so Helm/Compose steps
never hit the Terraform-path check twice; before any `dry_run` branching
in all three, so a dry-run preview gets the same protection a real
deploy does), and each integration's abort granularity matches that
integration's own pre-existing `ValueError`-catch precedent at the same
call site, not a new, invented severity model.

Four minor, non-blocking observations surfaced, disposed of as follows:

1. **Double computation in Helm** — `_render_values(item.module)` runs
   once in the pre-flight check and again inside `resolve_module_values()`.
   Deliberate (keeps both functions pure/side-effect-free per their own
   existing docstrings) and cheap given realistic module sizes. **Not
   changed** — the duplication is the right tradeoff here, not an
   oversight.
2. **Cosmetic edge case in the "Available from" diagnostic hint** — it's
   derived by splitting `tokens` dict keys on the first `.`; a
   `${var:}`/`${secret:}`/`${feature:}` key name that itself contained a
   literal `.` matching an upstream step's name as a prefix (e.g. a var
   named `infra.foo`) could appear in the hint as if it were a real
   output. Doesn't affect missing-key *detection*, only the hint text,
   and the ambiguity is inherited from the pre-existing `tokens` merge
   design (`{**resolved.values, **visible_outputs}`), not introduced by
   this feature. Strata's own var/secret naming convention is
   `UPPER_SNAKE_CASE`, making a real collision implausible. **Not
   changed** — flagged for awareness, not worth a defensive fix for a
   naming collision this codebase's own conventions already avoid.
3. **Test gap: no test proved the check still fires under `dry_run=True`**
   for any of the three integrations. **Fixed 2026-10-14** — added
   `test_deploy_run_output_preflight_runs_under_dry_run_too`
   (`test_deploy_controller.py`), `test_deploy_namespace_output_preflight_
   runs_under_dry_run_too` (both `test_integrations_helm.py` and
   `test_integrations_compose.py`). Each confirms both halves: the
   diagnostic still fires, and the underlying tool (`terraform`/`helm`/
   `docker`) is never invoked at all for the affected step/module/
   namespace — not merely skipped past a later dry-run-specific branch.
   Full check suite green: mypy (155 files), ruff check + format,
   import-linter (1 kept, 0 broken), pytest (2538 passed, up from 2535).
4. **Slightly widened scope** — `deploy_controller.py`'s `step_payload`
   computation is now unconditional (previously only computed when
   `integration.ENV_VAR_PREFIX is not None`). Harmless today (every
   non-container integration is Terraform, which always sets that
   prefix) but worth remembering if a future `ENV_VAR_PREFIX = None`,
   non-container tool is ever added — it will now also pay this
   computation's cost. **Not changed** — this is the correct design
   (the check must see every Terraform-shaped step's real payload
   regardless of delivery mechanism), just noted for future-reader
   awareness.

## Remaining Work / Open Questions

- **None — all 4 phases shipped 2026-10-14.** See the Implementation Plan
  above for the key-extraction helper and diagnostic wording (Phase 1/2),
  and both the Terraform and Helm/Compose wiring (Phase 2/3).
- **Does this need its own ADR** — leaning no, more strongly than the
  schema-field alternative did: this is an extension of an existing,
  already-built function (`_merge_durable_outputs()`'s own neighborhood),
  not a new capability/field/concurrency model.
- *(Alternative-direction-only, kept for completeness, not current
  priorities):* field placement, required-vs-optional, and manual-vs-
  auto-derived sourcing for the declared-schema-field idea — see
  "Alternative considered" above for that analysis in full.

## Changelog

- 2026-10-14: **Post-Phase-4 code review** — reviewed the full Phases 1-3
  diff, found no functional bugs; 4 minor observations recorded in the
  new "Code Review" section above. Of those, one was a genuine test gap
  (no test proved the pre-flight check still fires, and the underlying
  tool is never invoked, under `dry_run=True`) — fixed with 3 new tests,
  one per integration. The other three (Helm's double `_render_values()`
  call, a cosmetic edge case in the "Available from" hint's dotted-key
  splitting, and `step_payload`'s now-unconditional computation) were
  deliberately left as-is — each is either the correct tradeoff already,
  or a pre-existing characteristic this feature inherited rather than
  introduced. Full check suite green: mypy (155 files), ruff check +
  format, import-linter (1 kept, 0 broken), pytest (2538 passed, up from
  2535).
- 2026-10-14: **Built Phase 4 — documentation wrap-up. All 4 phases now
  shipped; feature complete.** Updated this doc's own `Status` line and
  `Remaining Work` section to the fully-shipped state. Re-checked
  `deploy-command.md` per the plan's own instruction and found two
  genuinely stale spots (its header, and the "Concrete implementation
  plan" step 3 note) still claiming the literal `KEY` half of
  `${output:STEP.KEY}` was wholly unvalidated — true when written
  2026-10-12, superseded two days later by Phases 1-3 here. Added
  forward pointers at both locations plus a dated Changelog entry there,
  rather than rewriting the historical claims in place (accurate
  descriptions of that specific 2026-10-12 fix at the time — only the
  reader's takeaway needed updating). Confirmed, again, no
  `docs/work/gap_fit_v1.md` entry belongs here. No code changed;
  documentation only.
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
- 2026-10-14: **Wrote the 4-phase Implementation Plan** ("create and
  design an implementation plan") for the leading (dynamic) direction —
  Phase 1 (`referenced_output_keys()` helper, placed in `strata/utils/
  value_tokens.py` rather than next to `_contains_output_token()` in
  `deploy_controller.py`, since `strata.integrations` can never import
  from `strata.controllers`, ADR-0003 layering, and Phase 3 needs it from
  `helm.py`/`compose.py`), Phase 2 (wire into `deploy_controller.py`'s
  Terraform path, hoisting `step_payload`'s computation so both
  `backend.configuration` and the unified tfvars payload are covered in
  one pre-flight pass), Phase 3 (same check in Helm/Compose's own
  `deploy_namespace()`, skip-only-the-offending-module severity matching
  the already-shipped ValueError fix), Phase 4 (docs only, no new
  `gap_fit_v1.md` entry — confirmed this is a new feature, not a v1
  parity gap). Clarified precisely what this plan adds on top of the
  already-shipped 2026-10-12 fix: not a new safety property (missing keys
  already fail cleanly today) but comprehensiveness (every bad key in one
  pass, not just the first) and better diagnostic wording. Superseded the
  "exact key-extraction helper"/"exact diagnostic wording" Remaining Work
  items, both now resolved by the plan. No code changed; documentation
  only.
- 2026-10-14: **Built Phase 3** — wired `referenced_output_keys()` into
  `HelmIntegration.deploy_namespace()` (`helm.py`) and
  `ComposeIntegration.deploy_namespace()` (`compose.py`). Found the two
  integrations genuinely need different diagnostic granularity rather
  than identical code: Helm's per-module loop reports a missing key and
  skips only that module, letting every other module in the namespace
  still deploy; Compose has already merged all modules into one
  `docker-compose.yml` by this point, so a missing key aborts the whole
  namespace — matching each integration's own existing `ValueError`-catch
  severity at the same call site, not a new inconsistency. One new test
  per integration, each proving the respective granularity empirically
  (Helm: the good module's `helm upgrade` really ran, naming only 'good';
  Compose: zero `docker stack deploy` calls happened at all). Full check
  suite green: mypy (155 files), ruff check + format, import-linter (1
  kept, 0 broken), pytest (2535 passed, up from 2533). Both `.v2-haven`
  and `.v2-cfg` re-validated clean, no regression. Phase 4 (docs only)
  still to come.
- 2026-10-14: **Built Phase 2** — wired `referenced_output_keys()` into
  `deploy_controller.py`'s Terraform delivery path. `step_payload`
  (previously computed only inside the `ENV_VAR_PREFIX` tfvars-rewrite
  branch) hoisted to run unconditionally; a new pre-flight check right
  after it unions `referenced_output_keys()` over `provisioner.backend.
  configuration` and `step_payload`, reports every key missing from
  `tokens` as its own diagnostic (naming the real keys available from
  that upstream step), then returns before any resolution/file-write is
  attempted. One pre-existing test's assertion updated (the pre-flight
  check now catches its scenario earlier, with a better message, than the
  old generic `ValueError`-catch path it used to exercise). One new test
  proves the comprehensiveness this phase adds: two distinct missing keys
  (one via `backend.configuration`, one via a claimed DNS document) both
  reported in a single `deploy run`. Full check suite green: mypy (155
  files), ruff check + format, import-linter (1 kept, 0 broken), pytest
  (2533 passed, up from 2532). Both `.v2-haven` and `.v2-cfg` re-validated
  clean, no regression. Phases 3-4 still to come.
- 2026-10-14: **Built Phase 1** — `referenced_output_keys()` added to
  `strata/utils/value_tokens.py` (recursive dict/list/str walk, collects
  every `${output:STEP.KEY}` token's `"STEP.KEY"` string, ignores every
  other token kind). 6 new tests in `test_utils_value_tokens.py`. Full
  check suite green: mypy (155 files), ruff, import-linter (1 kept, 0
  broken), pytest (2532 passed). Phases 2-4 still to come.
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
- 2026-10-12: Researched real HCL2 parsing options for the (now
  secondary, alternative-direction-only) auto-derivation idea, prompted by
  a direct question about library stability/capability. Found
  `python-hcl2>=8.0` is already a real dependency of this repo, already
  used for a structurally adjacent `.tf`-scanning task
  (`sbom_collectors/terraform_collector.py`) — confirmed its real shape
  (plain syntax-tree dict, no semantic query API; labeled blocks keyed by
  their own label; quoted string values; broad-exception parse-failure
  handling, directly tested) by reading that real code and its test,
  not assumed. Also corrected an overcautious claim: output block *names*
  (unlike variable/resource names) are always static string literals in
  real Terraform syntax — no `for_each`/`count` dynamic-naming risk
  applies to them at all, unlike the concern Interface's own abandoned
  auto-derivation idea had for *input* variables. No code changed;
  documentation only.
