# Approval metadata and gate streamlining — clarify `spec.approvals` vs. ADR-0057 gates

- Status: proposed
- Date: 2026-07-28

## Context and Problem Statement

During a global project review (`_lesson.md`, item C2), it was found that strata has
**three different things all called "approval"**, with no doc anywhere clarifying which
one actually blocks a deploy and which is audit-only:

1. **`spec.approvals`/`approvers`** — a plain metadata block on `DeploymentModel`
   (`DeploymentApprovalModel`, `DeploymentStageApprovalModel` in `deployment_model.py`),
   wired into `platform_builder.py` and logged by `run_deploy_command.py`'s
   `_log_approval_metadata` before stage execution, and displayed by the VS Code chat
   participant — but its own schema description explicitly says *"enforcement is done
   by the CI/CD system, not the CLI."* It never blocks a deploy itself; it is
   audit/declaration only. Confirmed real usage: test fixture
   `tests/data/deployments/deployment-with-approvals.yaml`, and cross-validation of
   stage-level approver keys against the top-level dict.
2. **ADR-0032** (approval workflows, status: proposed, never built) — explicitly
   designed to layer *on top of* ADR-0057, not compete with it (its own file says "read
   ADR-0057 first — this ADR only covers what is specific to `type: approval`").
3. **ADR-0057** (gates/`WorkItem` framework, status: implemented) — the real generic
   orchestration/hand-off layer; `type: approval` is one of its gate types alongside
   `cost_review`/`security_review`/`verify`/`scheduled`/`incident`/`cab` (confirmed in
   `gate_model.py`).

The policy engine (ADR-0006, completed) is a related-but-distinct concern — hard
declarative pass/fail rules (e.g. CVE severity) that run at a separate, earlier
pipeline phase (Phase 1 "PLAN") than gates (Phase 2 "GATE CHECK"). No evidence of
runtime conflict between policy and gates was found — they are properly sequenced.
**This ADR is not about the policy engine**; it is specifically about the confusing
three-way "approval" naming overlap.

This ADR records the recommended direction only. Option 2 (docs-only) is the immediate
action; the rest is recorded so it is not re-litigated later.

## Decision Drivers

- `spec.approvals` is **not** dead code — it has real tests, real model validation,
  real callers, and serves a genuinely different use case than ADR-0057 gates: teams
  whose enforcement already happens externally (Azure DevOps environment approvals,
  GitHub Actions environment protection rules) who just want strata to declare/audit
  who is expected to approve, with zero in-strata blocking. Removing it outright would
  regress that use case.
- ADR-0057 gates are heavier by design (a real `WorkItem`, exit code 5, `--resume`
  flow) — not a drop-in replacement for teams who only want a lightweight audit
  annotation.
- The actual problem found is naming/clarity confusion (three things called
  "approval," no doc says which is enforcing vs. audit-only), not functional
  redundancy or a runtime bug.
- Minimize blast radius — no user's existing `spec.approvals` YAML should break.

## Considered Options

### Option 1 — Remove `spec.approvals` entirely, force everyone onto ADR-0057 gates

Delete the metadata block and require all approval declarations to go through
ADR-0057's `type: approval` gate.

- Con: real usage found (model validation, builder wiring, deploy-time logging, VS
  Code display, test fixtures) would be lost.
- Con: would regress the "my CI/CD already gates, I just want an audit record" use
  case that ADR-0057's heavier `WorkItem`/exit-5/`--resume` machinery doesn't serve
  well.

**Rejected.**

### Option 2 — Docs-only clarification (RECOMMENDED, do now)

No code changes. Add an unambiguous note — in
[ADR-0057](0057-deployment-workflow-orchestration.md)'s references section, in
whatever guide covers deployment approvals, and in the `DeploymentApprovalModel`/
`DeploymentStageApprovalModel` docstrings/field descriptions themselves — stating
plainly: *"`spec.approvals` is declarative audit metadata only; it does not block a
deploy. For strata-enforced approval gating, use ADR-0057's `type: approval` gate
instead."* Cross-link both directions.

- Pro: zero migration risk.
- Pro: addresses the actual problem (confusion), ships immediately.
- Con: does not change the underlying architecture — the overlap in naming remains,
  only the confusion about it is resolved.

**This is the winning option, for immediate action.**

### Option 3 — Rename `spec.approvals` for self-documenting clarity

E.g. rename to `approval_metadata` or similar, so the field name itself signals
"metadata, not enforcement."

- Con: real blast radius (schema, existing YAML in the wild, tests, VS Code
  extension) for a naming fix alone.
- Con: the docs fix (Option 2) achieves the same clarity outcome without breaking
  anything.

**Rejected for now.**

### Option 4 — Bridge the two mechanisms with an opt-in `enforce: true` field

Add an opt-in `enforce: true` field on `spec.approvals` that, when set, internally
creates a real `type: approval` gate (via `WorkItemController`) instead of just
logging metadata — same YAML surface, optional escalation from "audit only" to
"actually enforced."

- Pro: the only option that is a genuine architectural streamline rather than a docs
  patch — a single YAML surface that can either declare (audit) or enforce
  (gate-backed), operator's choice.
- Con: more implementation work, spanning `deployment_model.py`, `gate_controller.py`,
  and `run_deploy_command.py`.
- Con: needs its own design pass before implementation — see open question in Detailed
  Design below.

**Not rejected — recorded as the real future streamlining direction, not scheduled.**

## Decision Outcome

Ship **Option 2 now** (docs-only clarification — no code changes, zero risk). Record
**Option 4 as the real future streamlining direction** — worth pursuing if/when a team
actually wants strata-enforced approvals without hand-authoring a separate
`spec.gates` block. Option 1 and Option 3 are rejected outright (documented above so
they are not re-litigated later).

### Consequences

- Good: the actual point of confusion (three things named "approval," no doc
  distinguishing enforcing vs. audit-only) is resolved immediately, with no code
  changes and no migration risk.
- Good: `spec.approvals` continues to serve its real, distinct use case (externally
  enforced CI/CD approvals that strata only needs to declare/audit) without
  regression.
- Good: ADR-0057's gate framework remains the single recommended mechanism for
  strata-enforced approval hand-offs — nothing about this decision competes with it.
- Neutral: the underlying architectural overlap (two YAML surfaces that can both
  express "an approval is involved here") is not removed, only clearly labeled. Option
  4 is the path to actually converge them, if ever prioritized.
- Bad (accepted): this ADR does not change any code — the docs edits themselves (the
  actual Option 2 work) are a separate, smaller follow-up task, not performed here.

## Detailed Design

### Option 2 (the immediate action)

- Add a short clarifying note to `DeploymentApprovalModel`'s docstring/field
  description in `deployment_model.py`, e.g.: *"Declarative approval metadata —
  enforcement happens externally (CI/CD) or, if you need strata to actually block on
  approval, see the `type: approval` gate in ADR-0057."*
- Add a cross-reference in ADR-0057's References section pointing back at this ADR
  and at `spec.approvals`, so a reader landing on either ADR sees the other.
- Add a one-paragraph note wherever deployment approvals are documented for end users
  (check `docs/guides/deployment-approval-gates.md` if it exists) distinguishing the
  two.
- No model/schema/behavior changes — this is intentionally the cheapest possible fix.

### Option 4 (sketch, future/not-yet-scheduled)

- `enforce: bool = False` field on `DeploymentApprovalModel`.
- When `True`, `run_deploy_command.py`'s pre-flight (same phase as
  `_log_approval_metadata` today) additionally calls
  `WorkItemController.request(type="approval", ...)` using the declared `approvers`
  as the gate's approver list, instead of (or in addition to) just logging.
- **Open question, unresolved — needs its own follow-up design pass before Option 4 is
  built:** does this reuse the existing `spec.gates` YAML surface under the hood, or
  does `spec.approvals` become a convenience shorthand that generates an equivalent
  gate at parse/resolve time? Also unresolved: whether `enforce: true` still allows
  stage-level `approval.approvers` overrides the same way it does today, and whether
  it needs its own `WorkItem` type or can reuse `type: approval` as-is. This ADR does
  not pick an answer — it only flags that the question exists.

## Implementation Phases

### Phase 1 (do now, this session if requested separately)

- Option 2 docs clarification: docstring/field description note in
  `deployment_model.py`, cross-reference in ADR-0057's References section, and a
  user-facing guide note distinguishing `spec.approvals` from ADR-0057's
  `type: approval` gate.

### Phase 2 (future, not scheduled)

- Option 4 bridge (`enforce: true` field), pending its own design resolution on the
  open question above (reuse `spec.gates` vs. generate an equivalent gate at
  parse/resolve time).

## References

- [ADR-0006: Policy engine for deployment guardrails](0006-policy-engine-for-deployment-guardrails.md) —
  the related-but-distinct hard pass/fail rule engine, sequenced earlier (Phase 1
  "PLAN") than gates (Phase 2 "GATE CHECK"); confirmed not in conflict with either
  `spec.approvals` or ADR-0057.
- [ADR-0018: Deployment audit traceability](0018-deployment-audit-traceability.md) —
  the audit trail concern that `spec.approvals`' logging (`_log_approval_metadata`)
  feeds into.
- [ADR-0032: Approval workflows and gates](0032-approval-workflows-and-gates.md) —
  proposed, never built; explicitly designed to layer on top of ADR-0057 rather than
  compete with it, and one of the three overlapping "approval" names this ADR
  clarifies.
- [ADR-0057: Deployment workflow orchestration — work items and hand-off gates](0057-deployment-workflow-orchestration.md) —
  the real generic gate/`WorkItem` mechanism; `type: approval` is one of its gate
  types. This ADR adds a cross-reference back from ADR-0057 to here.
- `_lesson.md`, item C2 — the global-review finding that prompted this ADR: four
  mechanisms initially appeared to gate/block a deploy (policy engine, `spec.approvals`,
  ADR-0032, ADR-0057), verdict was "architecturally coherent, not competing" but with a
  real naming/clarity problem, which this ADR addresses.
