# Uniform missing-data handling for policies and gates

- Status: implemented — all four phases (shared mechanism, policy engine, gate
  engine, docs) done
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
    ai_risk: Optional[str] = None                    # already nullable — reuse as the presence signal
    ...
```

`DeploymentGateModel` gains `on_missing_data: MissingDataPolicy = MissingDataPolicy.SKIP`
(one setting per gate, not per condition — a gate combines conditions with AND logic
already, and per-condition granularity isn't something operators have asked for).

Unlike `PolicyResult`, `should_trigger` has no result object to carry a warning
string in — but it doesn't need one. `strata.logger.get_logger()` defaults to
console-visible `INFO`-level output, so `logger.warning(...)` already satisfies
"must be surfaced, not just a debug log" without inventing a new channel or
changing `should_trigger`'s return type at all. `cost_delta_monthly` and
`ai_risk` are already nullable today, and `_eval_numeric_expr()`/`_eval_risk_expr()`
already special-case `actual is None` — so the one change needed is teaching
those two shared functions to consult `resolve_missing_data` instead of
unconditionally returning `False` for "missing":

```python
def _eval_numeric_expr(
    expr: str, actual: Optional[float],
    on_missing_data: MissingDataPolicy, gate_name: str, reason: str,
) -> bool:
    if actual is None:
        outcome = resolve_missing_data(on_missing_data, reason)
        if outcome.warning:
            logger.warning("gate.missing_data", gate=gate_name, reason=outcome.warning)
        return outcome.blocked
    ...  # unchanged below this point
```

`_eval_risk_expr()` (for `ai_risk`) gets the identical treatment. `should_trigger`
passes `gate.on_missing_data`, `gate.name`, and a condition-specific reason string
through at each of its four now-aware call sites (`cost_delta_monthly`,
`cve_critical`, `cve_high`, `ai_risk`) — `time_utc` is unaffected, since current
wall-clock time is never "missing." Full detail, including the corrected scope
(the original sketch here excluded `ai_risk` and proposed changing
`should_trigger`'s return type — both corrected after re-reading the actual code)
is in Phase 3 under Remaining Work below.

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

## Implementation

All four phases below are complete (`Status: implemented` above) — kept as a
phased record of what was built and why, not a pending-work list. Originally
structured as phases so Phase 1 (the shared mechanism) could be built,
reviewed, and merged fully independently of either engine adopting it —
nothing in Phase 2/3 could start until Phase 1 existed, but Phase 1 had zero
dependency on either of them.

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

### Phase 2 — Policy engine integration ✅ Done

Design finalized 2026-09-17, based on the actual current call sites in both
target policies (re-read, not assumed) — every existing skip in both files was
classified below so the migration was mechanical, not judgment-call-per-edit.

| Item                                                                                                  | Status |
| ----------------------------------------------------------------------------------------------------- | :----: |
| `PolicyModel.on_missing_data: MissingDataPolicy = MissingDataPolicy.SKIP`                             |   ✅    |
| `BasePolicy._missing_data(reason: str) -> PolicyResult`                                               |   ✅    |
| `cost_threshold_policy.py` — 2 call sites migrated (see table)                                        |   ✅    |
| `cve_max_severity_policy.py` — 1 call site migrated (see table)                                       |   ✅    |
| Tests: `BasePolicy._missing_data()` unit tests (5) + updated tests for both migrated policies (9 new) |   ✅    |

#### `PolicyModel` change

```python
# strata/models/policy_model.py
from strata.models.missing_data_model import MissingDataPolicy

class PolicyModel(PlatformBaseModel):
    ...
    on_missing_data: MissingDataPolicy = Field(
        MissingDataPolicy.SKIP,
        description=(
            "How this policy behaves when its required input data was never "
            "produced (as opposed to simply not applying here): skip (default, "
            "today's behavior) | warn (skip, but surface a visible warning) | "
            "block (treat as a violation, per `enforcement`)."
        ),
    )
```

Backward compatible by construction — a new `Optional`-defaulted field on a
`PlatformBaseModel` (`extra="forbid"`) doesn't break existing YAML that predates
this field; it just wasn't there to set.

#### `BasePolicy._missing_data()`

```python
# strata/validators/policies/base_policy.py
from strata.models.missing_data_model import resolve_missing_data

class BasePolicy(ABC):
    ...
    def _missing_data(self, reason: str) -> PolicyResult:
        """Required input data was never produced for this ACTIVE policy — as
        opposed to the policy simply not applying here (which stays a plain,
        unconditional pass; see the distinction table above). Delegates the
        skip/warn/block decision to the one shared `resolve_missing_data`
        function (ADR-0082) instead of hardcoding today's silent-skip behavior.
        """
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

#### Call-site classification — `cost_threshold_policy.py`

Every existing `_skip()`/`PolicyResult` call in the file, checked against the
"not applicable" vs. "missing data" table above:

| Call site                                                       | Classification                                                 | Action                       |
| --------------------------------------------------------------- | -------------------------------------------------------------- | ---------------------------- |
| `max_monthly not configured`                                    | Not applicable (misconfiguration)                              | Leave as plain `_skip()`     |
| `max_monthly is not a valid number`                             | Not applicable (misconfiguration)                              | Leave as plain `_skip()`     |
| `environment '...' does not match pattern '...'`                | Not applicable (scoping)                                       | Leave as plain `_skip()`     |
| `cost.json not found — declare a cost estimator integration...` | **Missing data** — the whole point of this ADR                 | Migrate to `_missing_data()` |
| `cost.json contains no parseable cost data`                     | **Missing data** — estimator ran but produced nothing usable   | Migrate to `_missing_data()` |
| `cost.json total is zero — skipping threshold check`            | Not applicable — a real computed value of zero, not an absence | Leave as plain `_skip()`     |

The existing `_skip()` helper itself is untouched — it keeps meaning "not
applicable, unconditional pass," exactly as it does today.

#### Call-site classification — `cve_max_severity_policy.py`

This file constructs `PolicyResult` inline rather than through a `_skip()`
helper (it has none), so both existing "give up" paths are call sites to check
directly:

| Call site                                                                            | Classification                                                                                                                                                                                                            | Action                        |
| ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------- |
| `max_severity not configured or invalid`                                             | Not applicable (misconfiguration)                                                                                                                                                                                         | Leave as plain `PolicyResult` |
| `no SBOM available or scanner not found` (after `_run_scan` self-heal attempt fails) | **Missing data** — this policy already tries to self-heal (re-running the scan) before giving up; migrating it means an operator can *also* choose `block` for the case where even self-healing couldn't produce a result | Migrate to `_missing_data()`  |

Migrating this one is arguably the most valuable single change in Phase 2: it
turns the policy that already does the "right" thing (self-heal first) into one
that also has an explicit, configurable answer for when self-healing genuinely
can't (no SBOM ever built, scanner never installed) — instead of silently
defaulting to "pass."

#### Acceptance criteria for Phase 2

1. Existing behavior is unchanged for every policy YAML that doesn't set
   `on_missing_data` — default `SKIP` reproduces today's `_skip()`/inline-pass
   output byte-for-byte (`passed=True`, same `details={"skipped": ...}` shape).
   Regression-tested by running the existing `test_cost_threshold_policy.py` and
   `cve_max_severity_policy` test suites unmodified against the migrated code —
   they must still pass with zero changes to their assertions.
2. `on_missing_data: warn` surfaces the reason in `PolicyResult.warnings` (not
   just a debug log) — new test per migrated policy.
3. `on_missing_data: block` produces `passed=False` with a violation message
   containing the original reason, and respects the policy's own `enforcement`
   level exactly like a normal violation would (a `warn`-enforcement policy with
   `on_missing_data: block` still only warns on missing data, it doesn't
   escalate to deny — `enforcement` and `on_missing_data` are orthogonal knobs).
4. The three "not applicable" skips in `cost_threshold_policy.py` and the one in
   `cve_max_severity_policy.py` are verified, by test, to **ignore**
   `on_missing_data` entirely — i.e. a policy configured with
   `on_missing_data: block` still passes silently when `max_monthly` isn't
   configured. This is the one criterion most likely to regress silently if the
   distinction table above is misapplied during implementation, so it gets its
   own explicit test per not-applicable call site, not just "it still passes."

### Phase 3 — Gate engine integration ✅ Done

Design finalized 2026-09-17, re-reading the actual current code (not the
earlier draft's assumptions) turned up a simpler and smaller design than
originally sketched — recorded below, replacing the original bullet list.

**Correction to the earlier draft**: `should_trigger`'s return type does **not**
need to change, and `WorkItemGateController.evaluate_and_create`'s two internal
call sites — nor any of its three external callers in `run_deploy_command.py`
(`_evaluate_deployment_gates`, `_evaluate_condition_gates_post_plan`,
`_evaluate_verify_gate_post_apply`) — need to change at all. Reasoning:
`strata.logger.get_logger()` defaults to `enable_console=True` at `INFO` level,
so `logger.warning(...)` is already surfaced to the console by default (not a
debug-only log) — exactly what `on_missing_data: warn` requires — the same
mechanism `cost_threshold_policy` already uses for its own "worth surfacing"
skip. No new warning-propagation channel needs to be invented or threaded
through three call sites.

**Second correction**: `_eval_numeric_expr()` already has an
`if actual is None: return False` short-circuit — it was already handling
"missing" for `cost_delta_monthly` (already `Optional[float]` on `GateContext`)
correctly-shaped, just with no way to choose anything other than "skip." Since
`cve_critical`/`cve_high` route through this exact same shared function once
their `GateContext` fields become nullable, and `_eval_risk_expr()` (for
`ai_risk`) has the identical `if actual is None: return False` shape, **one
function change to each of these two shared evaluators** covers all four
numeric/risk conditions — no per-condition branching needed in
`should_trigger` beyond passing through what's already computed.

**Scope correction**: the original draft excluded `ai_risk` ("computed inline,
no missing state") — checked against the actual model and that's wrong.
`GateContext.ai_risk: Optional[str] = None` is exactly as nullable as
`cost_delta_monthly`, and populated from AI plan analysis that may not have
run (`--ai`/`--strict-ai-review` not passed). Leaving it out would recreate,
inside this very fix, the exact inconsistency ("3 conditions handle missing
data, 1 silently doesn't") this ADR exists to eliminate. `time_utc` stays
excluded — current wall-clock time is always available, there is no "missing"
state to represent for it.

| Item                                                                                                                                 | Status |
| ------------------------------------------------------------------------------------------------------------------------------------ | :----: |
| `DeploymentGateModel.on_missing_data: MissingDataPolicy = MissingDataPolicy.SKIP`                                                    |   ✅    |
| `GateContext.cve_critical_count`/`cve_high_count`: `int = 0` → `Optional[int] = None`                                                |   ✅    |
| `_eval_numeric_expr()` gains `on_missing_data`/`gate_name`/`reason` params, calls `resolve_missing_data` on `actual is None`         |   ✅    |
| `_eval_risk_expr()` gains the same, for `ai_risk`                                                                                    |   ✅    |
| `should_trigger()` passes the new params through at its 4 call sites (`cost_delta_monthly`, `cve_critical`, `cve_high`, `ai_risk`)   |   ✅    |
| No changes to `evaluate_and_create`, or any of its 3 callers in `run_deploy_command.py` — confirmed, none were needed                |   ✅    |
| Regression test: `security_review` + `on_missing_data: block` fires when `cve-audit.json` is absent (the exact bug found 2026-09-17) |   ✅    |

Tests: `tests/strata/controllers/test_gate_controller.py`
(`TestGateConditionEvaluatorOnMissingData`, 7 new tests) — all 50 tests in the
file pass, including the pre-existing ones unmodified (acceptance criterion 1).
Full `Check.ps1` (6802 tests, lint, format, mypy, docs) passes.

#### `DeploymentGateModel` change

```python
# strata/models/gate_model.py
from strata.models.missing_data_model import MissingDataPolicy

class DeploymentGateModel(PlatformBaseModel):
    ...
    on_missing_data: MissingDataPolicy = Field(
        MissingDataPolicy.SKIP,
        description=(
            "How this gate's condition(s) behave when their required input data "
            "was never produced (e.g. cve-audit.json absent because --audit wasn't "
            "run, or cost.json absent because no cost estimator is declared): "
            "skip (default, condition never triggers) | warn (condition never "
            "triggers, but a visible warning is logged) | block (condition counts "
            "as MATCHED — the gate fires and requires its configured resolution, "
            "e.g. human approval, exactly as if the real threshold had been "
            "exceeded). One setting per gate, not per condition. See ADR-0082."
        ),
    )
```

#### `GateContext` change

```python
# strata/controllers/gate_controller.py
@dataclass
class GateContext:
    cost_delta_monthly: Optional[float] = None       # unchanged — already correctly nullable
    cve_critical_count: Optional[int] = None         # was: int = 0
    cve_high_count: Optional[int] = None             # was: int = 0
    ai_risk: Optional[str] = None                    # unchanged — already correctly nullable
    current_time_utc: Optional[datetime] = field(default_factory=lambda: datetime.now(timezone.utc))
    extra: Dict = field(default_factory=dict)
```

`gate_context_builder.py`'s `build()` already only overwrites `ctx.cve_critical_count`/
`cve_high_count` `if cve:` (truthy) — with the new `None` default this already
does the right thing (leaves them `None` when `cve-audit.json` is absent)
**with no code change needed there**. Likewise the `gate_context` dict built
for the human-facing work-item payload (`if context.cve_critical_count:`) is
already a truthy check, which already treats `None` the same as `0` (omit the
field) — also **no code change needed there**. The only two functions in the
whole gate engine that actually need new logic are the two shared evaluators.

#### `_eval_numeric_expr()` / `_eval_risk_expr()` change

```python
# strata/controllers/gate_controller.py
from strata.models.missing_data_model import resolve_missing_data

def _eval_numeric_expr(
    expr: str,
    actual: Optional[float],
    on_missing_data: MissingDataPolicy,
    gate_name: str,
    reason: str,
) -> bool:
    """Evaluate ">= 1000" style expression against a numeric value.

    When `actual` is None (required data was never produced), delegates the
    skip/warn/block decision to the shared `resolve_missing_data` (ADR-0082)
    instead of unconditionally returning False — `warn`/`block` need this
    call site to actually decide something, not silently short-circuit.
    """
    if actual is None:
        outcome = resolve_missing_data(on_missing_data, reason)
        if outcome.warning:
            logger.warning("gate.missing_data", gate=gate_name, reason=outcome.warning)
        return outcome.blocked
    m = _OPERATOR_RE.match(expr.strip())
    ...  # unchanged below this point
```

`_eval_risk_expr()` gets the identical treatment for its own `if actual is None:`
branch.

#### `should_trigger()` change

Each of the four call sites passes `gate.on_missing_data`, `gate.name`, and a
condition-specific reason string through to the (now missing-data-aware)
shared evaluator — no branching logic duplicated in `should_trigger` itself:

```python
if cond.cost_delta_monthly is not None:
    checks.append(_eval_numeric_expr(
        cond.cost_delta_monthly, context.cost_delta_monthly,
        gate.on_missing_data, gate.name, "no cost.json — cost was not estimated",
    ))

if cond.cve_critical is not None:
    actual = float(context.cve_critical_count) if context.cve_critical_count is not None else None
    checks.append(_eval_numeric_expr(
        cond.cve_critical, actual,
        gate.on_missing_data, gate.name, "no cve-audit.json — CVE scan was not run",
    ))

if cond.cve_high is not None:
    actual = float(context.cve_high_count) if context.cve_high_count is not None else None
    checks.append(_eval_numeric_expr(
        cond.cve_high, actual,
        gate.on_missing_data, gate.name, "no cve-audit.json — CVE scan was not run",
    ))

if cond.ai_risk is not None:
    checks.append(_eval_risk_expr(
        cond.ai_risk, context.ai_risk,
        gate.on_missing_data, gate.name, "no AI risk analysis — --ai/--strict-ai-review was not run",
    ))

if cond.time_utc is not None:
    checks.append(_eval_time_window(cond.time_utc, context.current_time_utc))  # unchanged — always available
```

`should_trigger`'s return type stays a bare `bool`; `evaluate_and_create` and
every one of its three callers are unaffected by this phase.

#### Acceptance criteria for Phase 3

1. Existing behavior is unchanged for every gate YAML that doesn't set
   `on_missing_data` — default `SKIP` reproduces today's exact behavior
   (`_eval_numeric_expr`/`_eval_risk_expr` returning `False` on `None`,
   condition never triggers).
2. **The regression test proving the original bug is fixed**: a
   `security_review` gate with `when: {cve_critical: ">= 1"}` and
   `on_missing_data: block`, evaluated with `GateContext(cve_critical_count=None)`
   (i.e. `cve-audit.json` was never produced) → `should_trigger()` returns
   `True`. This is the literal scenario found 2026-09-17: `--audit` wasn't
   passed to `build run`, and the gate must now fire instead of silently
   passing.
3. `on_missing_data: warn` calls `logger.warning(...)` with the condition's
   reason string — asserted via `caplog`/logger-capture, not just "doesn't
   raise."
4. Every one of the four newly-aware conditions (`cost_delta_monthly`,
   `cve_critical`, `cve_high`, `ai_risk`) gets its own missing-data test —
   not just the CVE one that motivated this ADR, since all four now share the
   identical code path and a regression in the shared function would affect
   all four identically.
5. `time_utc` is explicitly verified to be *unaffected* by this phase — no
   `on_missing_data` handling, since a maintenance-window check never has a
   "data missing" state.

### Phase 4 — Docs ✅ Done

- Updated `docs/guides/deployment-approval-gates.md`: fixed a pre-existing
  inaccuracy (`ai_risk`'s data source was documented as `cve-audit.json`; it
  actually comes from `--ai`/`--strict-ai-review` plan analysis), and added a
  new "When condition data is missing" section documenting `on_missing_data`
  with a worked `security_review` example and an explicit recommendation to
  set `block` for `security_review`/CVE conditions.
- Updated `cve_max_severity_policy.py`'s docstring with an explicit `block`
  recommendation and a configuration example showing it.
- Checked `docs/config/configuration.md` and confirmed `cost_threshold`/
  `cve_max_severity` have no separate entry there to update (only
  checkov/opa do) — their docstrings (updated in Phase 2/here) are the only
  doc surface for those two policies.
- Full `Check.ps1` (lint, format, mypy, tests, Sphinx build, docs-index
  coverage) passes.

