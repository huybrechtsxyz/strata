# Uniform missing-data handling for policies and gates

- Status: partially-implemented — Phase 1 (shared mechanism) done; Phases 2-4
  (policy engine, gate engine, docs) not started
- Date: 2026-09-17
- Related: ADR-0006 (policy engine for deployment guardrails), ADR-0057 (deployment
  workflow orchestration / gates), ADR-0031 (cost estimation and visibility — section
  3a/3b fixed this defect for cost specifically), ADR-0051 (checkov integration —
  `PolicyResult.warnings` precedent)

## Context and Problem Statement

Reviewing ADR-0031's cost gate (2026-09-17) found that `cost_threshold` was a
structural no-op: `cost.json` was only ever produced by a separate, easy-to-forget
manual command, and the policy's own graceful-degradation path treated "no data" as
"pass" — silently, with no console-visible signal. ADR-0031 sections 3a/3b fixed this
for cost specifically (compute at the right time, merge correctly, record once per
run).

Checking whether this was a one-off or a class of defect found a second, independent
instance: `security_review`/`cost_review` **gates** (a different subsystem —
`DeploymentGateModel`/`GateWhenConditionsModel`/`GateConditionEvaluator`, not
`BasePolicy`) have the same shape. `GateContext.cve_critical_count` and
`cve_high_count` default to `0`, not `Optional` — so when `cve-audit.json` doesn't
exist (because `--audit` wasn't passed to `build run`, which is opt-in), a configured
`cve_critical: ">= 1"` condition evaluates `0 >= 1` → `False` → the gate never
triggers. Nothing errors, nothing warns. The
[approval-gates guide](../guides/deployment-approval-gates.md) documents this
condition with no mention that `--audit` is a prerequisite for it to mean anything.

A third data point: `cve_max_severity_policy` (a `BasePolicy`, not a gate) handles the
*identical* missing-data situation completely differently — it re-runs the CVE
scanner itself rather than assuming zero findings. Three call sites, three different
answers to "what does 'no data' mean," none of them declared, none of them
consistent. That inconsistency is itself the defect this ADR addresses — not any one
instance of it.

**The general shape**: strata's guardrails (both policies and gates) are built to
"fail open" by default — reasonable for legitimate not-applicable cases (`max_monthly`
not configured, `environment_pattern` doesn't match). But **when a guardrail is
actively configured and its required input simply wasn't produced**, failing open
silently is indistinguishable from "checked and clean" to anyone reading the deploy
output — exactly the "looks enforced and is not" trap the SBOM/policy docs already
warn about for other cases.

## Decision Drivers

- Same defect found independently in two separate mechanisms (policies, gates) — a
  one-off fix in one place would leave the other unpatched and the next occurrence
  undetected until someone re-derives all of this from scratch.
- Per the repo's "introducing a new convention" rule (`docs/decisions/README.md`):
  check for reuse before inventing, and keep one implementation, not per-call-site
  copies.
- Must not silently change behavior for existing deployed configurations — the
  current (inconsistent) fail-open behavior is what production pipelines already run
  against.
- Security-flavored guardrails (`security_review`, `cve_max_severity`) arguably
  warrant a different default posture than cost-flavored ones (`cost_threshold`,
  `cost_review`) — "assume clean" is a materially worse default for vulnerabilities
  than for a cost estimate.

## Reuse check (per docs/decisions/README.md "Introducing a new convention")

- `PolicyResult` already has `enforcement: deny | warn | audit` and a `warnings: list`
  field (added for checkov, ADR-0051) — but both describe what to do with a
  **violation that was found**, not with the separate question of whether the
  evaluation had any real data to check in the first place. Not reusable as-is.
- `GateWhenConditionsModel` has no equivalent concept at all today — every condition
  silently assumes "no data" means "matches nothing."
- No existing strata mechanism distinguishes "not applicable" from "should have run,
  didn't." A new, small, explicit field is justified — kept to one shared concept
  applied to two models, not two independently invented ones.

## Considered Options

- **Option A** — fix each occurrence ad hoc as found (what ADR-0031 3a/3b did for
  cost). Rejected as the *only* response: doesn't prevent the third, fourth instance,
  and this session already found one in the gate system that 3a/3b didn't touch.
- **Option B** — a single `on_missing_data: skip | warn | block` field, added to both
  `PolicyModel` (policy engine) and `DeploymentGateModel` (gate engine), sharing one
  enum and one meaning, each engine applying it through its own existing result type.
  **Chosen.**
- **Option C** — unify policies and gates into a single mechanism. Rejected as
  out of scope: they have different evaluation phases, different result shapes
  (`PolicyResult` vs. work-item creation), and different resolution paths (policy
  enforcement vs. human approval). Forcing a merge to fix a missing-data corner case
  is a much larger, riskier change for no added benefit here.

## Decision Outcome

Chosen: **Option B.**

### The shared mechanism

Not just a shared enum — a **shared decision function**, per "one implementation,
not copies." Both engines have different result shapes (`PolicyResult` vs. a bare
`bool` from `should_trigger`), so the shared piece can't be one of those types
directly. Instead it's a small neutral outcome, computed once, that each engine
translates into its own shape at the call site — the translation is a one-line
`if`, not reimplemented decision logic.

```python
# strata/models/missing_data_model.py (new, small — shared by PolicyModel and
# DeploymentGateModel so there is exactly one definition of what these three
# values mean, per the "one implementation, not copies" rule)

class MissingDataPolicy(str, Enum):
    SKIP = "skip"    # today's behavior everywhere — treat as not-applicable, pass
    WARN = "warn"    # treat as not-applicable, but surface a visible warning
                     # (PolicyResult.warnings / gate manifest — never just a debug log)
    BLOCK = "block"  # treat missing data as a failure signal:
                     #   policy  -> passed=False, violation recorded, per `enforcement`
                     #   gate    -> condition counts as MATCHED (fail closed — the
                     #              gate fires and requires its configured resolution,
                     #              e.g. human approval, exactly as if the threshold
                     #              had actually been exceeded)


@dataclass(frozen=True)
class MissingDataOutcome:
    """Neutral result of resolving one missing-data event against a configured
    ``MissingDataPolicy`` — engine-agnostic, translated by each caller into its
    own result type. This is the one piece of decision logic; nothing below this
    layer is reimplemented per engine."""

    blocked: bool             # True => treat as a failure/trigger signal
    warning: Optional[str]    # non-None => must be surfaced (never silent debug-only)
    reason: str               # always populated, for audit/detail fields either way


def resolve_missing_data(mode: MissingDataPolicy, reason: str) -> MissingDataOutcome:
    """The one shared function both BasePolicy and GateConditionEvaluator call.

    skip  -> blocked=False, warning=None    (today's behavior, unchanged)
    warn  -> blocked=False, warning=reason  (still doesn't fail anything, but visible)
    block -> blocked=True,  warning=None    (blocking IS the surfaced signal — no
                                             redundant warning text alongside a
                                             denial/gate-trigger that already explains itself)
    """
    if mode == MissingDataPolicy.BLOCK:
        return MissingDataOutcome(blocked=True, warning=None, reason=reason)
    if mode == MissingDataPolicy.WARN:
        return MissingDataOutcome(blocked=False, warning=reason, reason=reason)
    return MissingDataOutcome(blocked=False, warning=None, reason=reason)
```

`resolve_missing_data` has no dependency on `PolicyResult`, `GateContext`, or any
engine-specific type — it is pure `(mode, reason) -> outcome`, which is what makes
it safely importable by both `validators/policies/base_policy.py` and
`controllers/gate_controller.py` without either depending on the other.

**Default: `skip` everywhere.** This is a deliberate compatibility decision, not an
endorsement — existing configured pipelines run against today's fail-open behavior,
and changing that silently under an unrelated code change would be its own "looks
enforced and is not" surprise, just inverted. Docs will explicitly recommend `block`
for `security_review` / `cve_max_severity` specifically, since "assume clean" is a
worse default for vulnerabilities than for cost.

### What counts as "missing data" vs. "not applicable"

Not every existing `_skip()` call becomes an `on_missing_data` check. Two genuinely
different situations get conflated in today's code and must stay distinguished:

| Situation                                                              | Example                                                     |  Governed by `on_missing_data`?   |
| ---------------------------------------------------------------------- | ----------------------------------------------------------- | :-------------------------------: |
| Policy/gate isn't configured to apply here                             | `max_monthly` not set; `environment_pattern` doesn't match  | No — always skip, unconditionally |
| Policy/gate *is* active, but its required external artifact is missing | `cost.json` absent; `cve-audit.json` absent, no scanner run |              **Yes**              |

Only the second row is this ADR's concern. Getting this distinction wrong (making
"not configured" respect `on_missing_data: block`) would make every policy fail
closed the moment someone forgets an unrelated `max_monthly` field — a self-inflicted
outage, not a safety improvement.

### Policy engine (`BasePolicy`) — translating the shared outcome

`BasePolicy` gets one thin adapter method that calls `resolve_missing_data` and maps
its neutral outcome onto `PolicyResult`. This replaces each policy's ad hoc `_skip()`
for the "required data missing" case specifically (the "not applicable" case keeps
calling a plain skip — see table above):

```python
def _missing_data(self, reason: str) -> PolicyResult:
    outcome = resolve_missing_data(self.policy.on_missing_data, reason)
    return PolicyResult(
        passed=not outcome.blocked,
        policy_name=self.name,
        enforcement=self.enforcement,
        policy_type=self.policy.type,
        violations=[f"Required data unavailable: {reason}"] if outcome.blocked else [],
        warnings=[outcome.warning] if outcome.warning else [],
        details={"skipped": reason} if not outcome.blocked else None,
    )
```

Initial migration targets (policies whose skip is genuinely "required data didn't
exist," not "not applicable" — see table above): `cost_threshold_policy` (`cost.json`
missing) and `cve_max_severity_policy` (no scanner, no SBOM to scan). Other built-in
policies keep their existing not-applicable skips untouched; they can adopt
`_missing_data()` individually later if a real missing-data case is identified for
them, per the same distinction.

### Gate engine (`GateConditionEvaluator`) — translating the shared outcome

`GateWhenConditionsModel`'s conditions currently read numeric defaults
(`cve_critical_count: int = 0`) that make "missing" and "measured zero"
indistinguishable by construction. Fix: track presence separately.

```python
@dataclass
class GateContext:
    cost_delta_monthly: Optional[float] = None       # already nullable — reuse as the presence signal
    cve_critical_count: Optional[int] = None         # changed from `int = 0`
    cve_high_count: Optional[int] = None             # changed from `int = 0`
    ...
```

`DeploymentGateModel` gains `on_missing_data: MissingDataPolicy = MissingDataPolicy.SKIP`
(one setting per gate, not per condition — a gate combines conditions with AND logic
already, and per-condition granularity isn't something operators have asked for).

Unlike `PolicyResult`, `should_trigger` today returns a bare `bool` with nowhere to
carry a warning string — so its return type changes to a small result the caller can
unpack, rather than inventing a second, parallel notification channel:

```python
@dataclass
class GateTriggerResult:
    triggered: bool
    warnings: List[str] = field(default_factory=list)  # surfaced by the caller
                                                          # (console echo today; the
                                                          # manifest's existing
                                                          # `warnings: List[str]`
                                                          # field per stage — see
                                                          # deployment_manifest_model.py — later)

@staticmethod
def should_trigger(gate: DeploymentGateModel, context: GateContext) -> GateTriggerResult:
    ...
    if cond.cve_critical is not None:
        if context.cve_critical_count is None:
            outcome = resolve_missing_data(gate.on_missing_data, "no cve-audit.json — CVE scan was not run")
            checks.append(outcome.blocked)
            if outcome.warning:
                warnings.append(outcome.warning)
        else:
            checks.append(_eval_numeric_expr(cond.cve_critical, float(context.cve_critical_count)))
    ...
    return GateTriggerResult(triggered=all(checks) if checks else False, warnings=warnings)
```

`WorkItemGateController.evaluate_and_create` — the only caller of `should_trigger`,
at two call sites within that one method (the `scheduled`/`auto_resolve` early-exit
branch, and the main per-gate loop) — unpacks `.triggered` where it currently uses
the bare bool, and forwards `.warnings` to whatever the deploy command already uses
for gate-related console output. No new surfacing mechanism invented — reuses the
existing warning-echo pattern the deploy command already has for policy warnings.

### Consequences

- Good: one declared, documented answer to "what happens when a guardrail's input
  doesn't exist," instead of three independently-invented ones.
- Good: no behavior change for existing pipelines (`skip` stays the default).
- Good: `block` gives security-conscious teams a real fail-closed option that does
  not exist today in either engine.
- Good: reuses each engine's existing result/resolution machinery — no new
  enforcement or approval mechanism invented.
- Bad: two Pydantic models to touch (`PolicyModel`, `DeploymentGateModel`) instead of
  one — accepted per "Option C rejected" above; a merged model would be a much larger
  change for no missing-data-specific benefit.
- Bad: requires auditing existing `_skip()` call sites one by one to classify
  "not applicable" vs. "missing data" correctly — mechanical but not zero-effort.

## Remaining Work

Structured as phases so Phase 1 (the shared mechanism) can be built, reviewed, and
merged fully independently of either engine adopting it — nothing in Phase 2/3
can start until Phase 1 exists, but Phase 1 has zero dependency on either of them.

### Phase 1 — Shared mechanism ✅ Done

Deliberately had no behavior to verify beyond its own unit tests — it isn't
imported by any production code yet, so it carried zero risk to ship ahead of
Phase 2/3.

| Item                                                                                                                  | Status |
| --------------------------------------------------------------------------------------------------------------------- | :----: |
| New file `src/strata/models/missing_data_model.py`                                                                    |   ✅    |
| `MissingDataPolicy(str, Enum)` — `SKIP`, `WARN`, `BLOCK`                                                              |   ✅    |
| `MissingDataOutcome` — frozen dataclass: `blocked: bool`, `warning: Optional[str]`, `reason: str`                     |   ✅    |
| `resolve_missing_data(mode: MissingDataPolicy, reason: str) -> MissingDataOutcome` — pure function, no engine imports |   ✅    |
| New test file `tests/strata/models/test_missing_data_model.py` (9 tests)                                              |   ✅    |

**Acceptance criteria for Phase 1** (what "done" means before Phase 2/3 can build on it):

1. `resolve_missing_data` has no import of `PolicyResult`, `GateContext`,
   `PolicyModel`, or `DeploymentGateModel` — verified by the module having zero
   imports from `strata.validators.*` or `strata.controllers.*` (grep-checkable,
   not just a design intent).
2. All 3×2 = 6 input combinations are covered by tests: each of `SKIP`/`WARN`/`BLOCK`
   × (a reason string present). Table:

   | mode  | `.blocked` | `.warning`   | `.reason`    |
   | ----- | :--------: | ------------ | ------------ |
   | SKIP  |  `False`   | `None`       | echoes input |
   | WARN  |  `False`   | echoes input | echoes input |
   | BLOCK |   `True`   | `None`       | echoes input |

3. `MissingDataOutcome` is frozen/immutable (`@dataclass(frozen=True)`) — it's a
   pure value handed across two otherwise-unrelated subsystems; accidental mutation
   by one engine must not be able to leak into the other's interpretation of the
   same outcome object (defensive, since Python doesn't otherwise stop a caller
   from doing `outcome.blocked = False`).
4. `MissingDataPolicy` is a `str, Enum` (not a plain `Enum`) so it round-trips
   through Pydantic/YAML the same way `enforcement: deny | warn | audit` already
   does elsewhere in `PolicyModel` — no new YAML-parsing behavior to design, it
   reuses the existing str-enum convention.
5. `strata schema`/model docs generation (if it walks `strata/models/*.py`
   automatically) picks up `MissingDataPolicy` without extra registration —
   **checked**: `strata schema list` is keyed off top-level `PlatformKind`
   entries (`common_models.py`), not nested field enums, so a plain enum used by
   a *field* on `PolicyModel`/`DeploymentGateModel` needs no separate schema
   registration. Re-verify once Phase 2/3 actually add the field — this only
   confirms there's no *extra* step required, not that the field is documented
   yet (that's Phase 4).

Phase 1 does **not** touch `PolicyModel`, `DeploymentGateModel`, `BasePolicy`, or
`GateConditionEvaluator` — those are Phase 2 and Phase 3, tracked below, and are
each their own follow-up once Phase 1 is merged.

### Phase 2 — Policy engine integration ⏳ Not started (blocked on Phase 1)

- Add `on_missing_data: MissingDataPolicy = MissingDataPolicy.SKIP` to `PolicyModel`.
- Add `BasePolicy._missing_data()` — calls `resolve_missing_data`, translates to
  `PolicyResult` (`passed=not outcome.blocked`, `violations`/`warnings` populated
  per the mapping in "Decision Outcome" above).
- Migrate `cost_threshold_policy` and `cve_max_severity_policy` to use it for their
  genuine missing-data skips only (leave their "not applicable" skips as plain
  skips — see the distinction table above).

### Phase 3 — Gate engine integration ⏳ Not started (blocked on Phase 1)

- Add `on_missing_data: MissingDataPolicy = MissingDataPolicy.SKIP` to
  `DeploymentGateModel` (sibling of `when`, not nested inside
  `GateWhenConditionsModel` — one setting per gate).
- Change `GateContext.cve_critical_count`/`cve_high_count` from `int = 0` to
  `Optional[int] = None`; update every place that reads them
  (`gate_context_builder.py`'s `build()`, the `gate_context` dict built for
  work-item payloads around `gate_controller.py` line 284) to treat `None` as
  "not measured" rather than defaulting to zero.
- Change `GateConditionEvaluator.should_trigger`'s return type from bare `bool` to
  `GateTriggerResult` (`triggered: bool`, `warnings: List[str]`); call
  `resolve_missing_data` for whichever of `cost_delta_monthly`, `cve_critical`,
  `cve_high` has no backing data (`ai_risk` and `time_utc` are computed inline and
  have no "missing" state to represent — excluded). Update both call sites in
  `WorkItemGateController.evaluate_and_create` (the `scheduled`/`auto_resolve`
  branch and the main per-gate loop) to unpack `.triggered`/`.warnings` instead of
  the bare bool.
- Regression test: a `security_review` gate with `on_missing_data: block` fires
  (`GateTriggerResult.triggered is True`) when `cve-audit.json` is absent — the
  exact bug found 2026-09-17.

### Phase 4 — Docs ⏳ Not started (blocked on Phase 2 and 3)

- Update `docs/guides/deployment-approval-gates.md` and the `cost_threshold`/
  `cve_max_severity` policy docstrings to document `on_missing_data`, and
  explicitly recommend `block` for `security_review`/`cve_max_severity`.

