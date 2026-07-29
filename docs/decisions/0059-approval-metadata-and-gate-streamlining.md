# Approval metadata and gate streamlining — clarify `spec.approvals` vs. ADR-0057 gates

- Status: proposed
- Date: 2026-07-28

> **Update 2026-07-29:** The "Option 2 docs-only" recommendation below was the
> original scope. Further design discussion concluded a full breaking-change
> unification is worth doing now instead — `spec.approvals`, ADR-0032, and
> ADR-0057's `spec.gates` converge into a single deployment-level `spec.gates`
> list. See the **"Unified Schema (2026-07-29 addendum)"** section at the end of
> this ADR for the actual design and implementation plan; treat it as superseding
> Option 2 as the shipped decision, not merely a future direction.

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

---

## Unified Schema (2026-07-29 addendum)

**This section supersedes Option 2 as the shipped decision.** Discussion after the
initial docs-only recommendation concluded that, since the two mechanisms are never
legitimately used together and a breaking change is acceptable here, a full
unification removes more real confusion than a docs note alone — and does so with
less total complexity than maintaining two shapes forever.

### Decision

Delete `spec.approvals`, `DeploymentApprovalModel`, `DeploymentStageApprovalModel`,
and ADR-0032 as separate concepts. Extend ADR-0057's gate model
(`DeploymentGateModel`) to absorb everything `spec.approvals` did, and **move it from
the environment to the deployment**, living next to `stages`. No backward-compat
shim — existing `spec.approvals`/environment-level `spec.gates` YAML fails validation
immediately once this ships.

### Why the deployment, not the environment

Environments are explicitly designed to be reused across multiple deployments
(ADR-0024 environment composition). Deployments already have a proven, simple
inheritance mechanism deployments use for exactly this kind of shared-base-plus-
override config:

```python
# deployment_model.py — already exists, unchanged by this ADR
partial: bool        # True = reusable base, not deployable standalone
extends: Optional[str]  # @repo/path to a base deployment; top-level fields replaced,
                         # stages merged by name, environments appended
```

Gates need to reference stage names to be scoped (`scope: [production]`) — but stage
names only exist on the deployment, not the environment. Keeping gates on the
environment would mean a gate's `scope` could silently stop matching if a second
deployment reused that environment with different stage names. Moving gates to the
deployment removes that cross-file ambiguity entirely: stages and gates resolve in the
same pass, from the same `extends` chain.

### Schema

```yaml
# deployment.yaml
spec:
  extends: "@platform/base/deploy-tenant-base.yaml"   # optional — partial base

  gates:
    - name: prod-approval              # merge key — same semantics as stages[].name
      type: approval                   # approval | cost_review | security_review |
                                        # verify | scheduled | incident | cab
      mode: enforce                    # declare (record only, never blocks) | enforce
      scope: [production]              # stage name(s), or "all"
      when: always                     # "always" | a GateWhenConditionsModel object
      approvers:
        platform-team:
          type: github-team
          value: "org/platform-team"
        devops-lead:
          type: user
          value: "devops@example.com"
      min_approvals: 1
      timeout_minutes: 60

    - name: cost-guard
      type: cost_review
      mode: enforce
      scope: all
      when:
        cost_delta_monthly: ">= 1000"
      approvers:
        finance:
          type: user
          value: "finance@example.com"

  stages:
    - name: staging
      secrets: [deploy_token]
    - name: production
      secrets: [deploy_token, db_password]
```

```yaml
# base/deploy-tenant-base.yaml  (partial: true)
spec:
  partial: true
  gates:
    - name: cost-guard          # same name → child's cost-guard entry overrides this one
      type: cost_review
      mode: enforce
      scope: all
      when:
        cost_delta_monthly: ">= 500"   # base default; child above overrides with 1000
```

### Field-by-field resolution of prior open questions

| Question | Resolution |
| --- | --- |
| Where do gates live? | Deployment level, next to `stages` (was: environment level) |
| How is a gate identified for `extends` override? | Explicit `name:` field, merged by name — identical semantics to `stages[].name`, not a new pattern |
| Approver shape | `Dict[str, ApproverRef]` (typed `github-team`/`user`/`ado-group` refs) everywhere — `spec.approvals`' richer shape wins over `spec.gates`' plain `List[str]` |
| Declare vs. enforce | `mode: declare \| enforce` on every gate entry, not just `approval` — generalizes cleanly to `cost_review`/`security_review` teams who want strata to log-only, not block |
| Duplicate/ambiguous config | A single `spec.gates` list — the old "both `spec.approvals` and an explicit `spec.gates` approval entry" conflict can't occur anymore because there's only one list |
| Stage scoping | `scope: [stage names] \| "all"` on each gate — replaces the old per-stage nested `approval:` override block on `DeploymentStageModel` |

### Impact inventory (full breaking-change scope)

**Models**
- `deployment_model.py` — delete `DeploymentApprovalModel`, `DeploymentStageApprovalModel`, the `approvals` field, `DeploymentStageModel.approval`, and the stage-key cross-validation `@model_validator`. Relocate `ApproverRef` to a shared location (it's currently deployment-only; gates need it too).
- `gate_model.py` — add `name: str`, `mode: Literal["declare", "enforce"] = "enforce"`, `scope: Union[Literal["all"], List[str]] = "all"`; change `approvers` from `Optional[List[str]]` to `Optional[Dict[str, ApproverRef]]`.
- `environment_model.py` — remove `EnvironmentSpecModel.gates` (moves to deployment).
- `platform_artifact_model.py` — remove its `approvals` mirror field.
- `.strata/schemas/*.json` — auto-regenerated from `model_json_schema()`; no manual edits, but existing users must re-run `sln update`.

**Controllers**
- `gate_controller.py` (`GateConditionEvaluator`, `WorkItemGateController.evaluate_and_create()`) — add a `mode` branch (declare → audit-log only, never call `WorkItemController.request()`) and scope filtering against the current stage; re-source gates from the deployment instead of the environment.
- `run_deploy_command.py` — delete `_check_approvals()` (lines ~1830–1869); its audit-only behavior is absorbed into the gate evaluator's `mode: declare` branch. Re-wire all 3 existing gate-evaluation phases (pre-plan / post-plan / post-apply) to read `deployment.spec.gates` instead of `environment.spec.gates`.

**Tests**
- Rewrite `tests/data/deployments/deployment-with-approvals.yaml` onto the new schema.
- Update the 7 `_check_approvals` mock call sites in `test_commands_deploy.py`.
- Delete model tests for `DeploymentApprovalModel`/`DeploymentStageApprovalModel`; add tests for the extended `DeploymentGateModel` (`name`, `mode`, `scope`, dict-shaped `approvers`).
- **New coverage needed, not just migrated:** `gate_controller.py` and `WorkItemController` currently have no dedicated unit tests at all — this change should add them.

**VS Code extension**
- `workItemsViewProvider.ts`, `strataChatParticipant.ts` (`/approvals` handler) — audit for any code assuming `approvers` is a flat list (e.g. `.join(', ')`) — breaks silently once it's a dict.

**Docs**
- ADR-0032 — mark superseded/closed (absorbed into ADR-0057 via this ADR).
- ADR-0057 — pointer added (this session); its own Detailed Design section should eventually inline the extended model rather than just link out.
- `docs/help/gates.md`, `docs/help/workitem.md`, `docs/guides/deployment-approval-gates.md`, `docs/config/deployment.md` (remove approvals section), `docs/config/environment.md` (remove gates section), `docs/platform/workflow.md` §7.9, `docs/GLOSSARY.md`, MCP docs mentioning approval gates.

### Implementation Plan (supersedes the old Phase 1/Phase 2 below)

1. **Models first, in isolation.** Extend `DeploymentGateModel` (`name`/`mode`/`scope`/dict `approvers`) and relocate `ApproverRef`. Delete the old approval models. Get `model_json_schema()` generating cleanly before touching any controller.
2. **Gate controller + deploy command rewiring.** Move gate sourcing from environment to deployment; add the `mode: declare` branch; re-point all 3 evaluation phases. This is the highest-risk step — it touches the one piece of infrastructure that works today.
3. **New test coverage.** Add `gate_controller.py`/`WorkItemController` unit tests (real gap, not just migration) before relying on them to catch regressions in step 2's rewiring.
4. **Fixture + existing test migration.** Rewrite `deployment-with-approvals.yaml` and the `_check_approvals` mock call sites.
5. **VS Code extension audit.** Check approver-shape assumptions in the two identified files; fix if broken.
6. **Docs sweep.** ADR-0032 superseded note, ADR-0057 inline update, guide/help/config doc rewrites, `Check.ps1`'s existing docs checks should catch any dangling references.
7. **Full `Check.ps1` + full test suite green before merge** — same verification bar as every other change this session.
