# Declarative Stage Gating for Deployment Stages

- Status: implemented
- Date: 2026-09-17
- Related: [ADR-0075](0075-unify-terraform-helm-value-expression-syntax.md) (`${var:}`/`${secret:}`/`${feature:}` resolver), [ADR-0064](0064-deployment-metrics-record.md) (deployment record / audit trail), [ADR-0066](0066-audit-event-routing-policy-model.md) (manifest.recorded), [ADR-0073](0073-embedded-string-syntax-inventory-and-creep-prevention.md) (embedded-string syntax inventory, `ExpressionModel`), [ADR-0034](0034-diagram-visualization-in-vscode-extension.md) (closed-grammar-over-raw-Jinja precedent)

## Implementation Notes

**Completed 2026-09-18.** All eight phases implemented in order; full suite 6981
passed, 16 skipped, with ruff/mypy/Sphinx green at the end of each phase.

| Phase | Delivered                                                                                               |
| ----- | ------------------------------------------------------------------------------------------------------- |
| 1     | `parse_bool()` + `stage_selection.py`; the two duplicated `--stage`/`--scope` filters extracted (D7)    |
| 2     | `stages[].enabled` field, `${secret:}` rejection, undeclared-key validation                             |
| 3     | Gate evaluation before pre-flight; `--stage` on a disabled stage errors (D2/D3/D4)                      |
| 4     | Skip recording — manifest `status="skipped"` + `skip_reason`, mirrored into the deploy-log (D6)         |
| 5     | `depends_on` ordering + transitive skip cascade (D5)                                                    |
| 6     | Non-gated-surface guards, `build plan` `would_skip` marker, `condition`→`enabled` synonym hint          |
| 7     | Cross-schema cleanup: `condition` removed; `resources[].enabled` and `modules[].enabled` made real (D8) |
| 8     | `StageSelectionMixin` + `StageSelectionMode`; all seven `--stage` copies consolidated                   |

Three findings surfaced during implementation that the original plan did not
anticipate, each recorded in place above:

- The `--stage` filter had **seven** copies, not the two D7 first identified, with
  three divergent messages between them.
- `resources[].enabled` / `modules[].enabled` were inert, and `resources[].condition`
  was inert *and* redundant — the whole test suite passed unchanged after removing
  it, which is itself the evidence.
- A `StageController` was designed and then **rejected** on review (Phase 8): three
  lines of delegation, and its testability argument proved false.

### Deliberately left open

Neither is part of this decision; both are recorded so they are not rediscovered:

- **`deploy show` does not disclose gating** — see
  [Adjacent finding](#adjacent-finding--deploy-show-previews-a-deployment-without-disclosing-gating).
  The preview lists every stage with no indication which will not run, and
  separately its `--stage` appears inert.
- **The `condition` deprecation shim** (`workspace_model.py`,
  `environment_model.py`) is marked for removal in the next minor release,
  alongside ADR-0078's `references` shim it sits beside.

## Context and Problem Statement

Deployment files declare a fixed list of `spec.stages[]`, each bound to a workspace
provisioner or topology. Today there is no declarative way to say "this stage does
not apply in this environment/estate/ring" — the only levers are:

- `strata deploy run --stage NAME` / `--scope LABEL` — **imperative, per-invocation**
  CLI filters. They express operator intent for a single run, not a durable fact
  about an environment.
- Maintaining separate deployment files per estate, hand-duplicating the stage list
  and manually removing the stages that don't apply.

Neither lets an author write, once, in the deployment file itself: "the
`dispatcher_api` stage only runs where `enable_dispatcher_api` is true for this
environment" — resolved automatically per-environment from the same
variable/feature machinery every other per-environment value in strata already
uses.

The absence of this has a real audit-trail consequence, not just an ergonomics one:
today, a stage that doesn't apply to an estate is either (a) never listed at all
(hand-maintained per-file duplication), or (b) forced through `--stage`/`--scope`
filtering, which means it's simply **absent** from the resulting manifest and
deploy-log — indistinguishable from "the deployment file itself doesn't know this
stage exists." There is currently no recorded fact of the form "stage X was
deliberately not deployed to ring Y at version v1.2.0" as opposed to "stage X
doesn't exist for ring Y" or "stage X deployed with no changes."

## Goals

1. A stage can declare, in the deployment file, an environment-resolved condition
   under which it applies — reusing existing resolution machinery, not inventing a
   new one (see ADR-0073's rule: prefer reuse before a new convention).
2. When a stage does not apply, that fact is **recorded**, not silently omitted —
   in both the deployment manifest and the deploy-log — so the audit trail can
   distinguish "deliberately not deployed at this version" from every other
   possible absence.
3. A stage that does not apply does not cause any backend/deployer initialization
   work for it (no empty state blob, no wasted init cost, per disabled
   estate/ring/environment).
4. Existing CLI-level overrides (`--stage`, `--scope`) keep their current
   precedence semantics rather than gaining new, different-shaped precedence rules.

## Facts on the ground (verified against the current code, not assumed)

These are load-bearing for any solution eventually chosen here, so recording them
now before anyone designs against a wrong assumption:

- **`${feature:KEY}` is already a fully supported, shared resolver token** —
  [`EXPR_PATTERN`](../../src/strata/utils/resolved_values.py#L211) /
  `resolve_expr_string()` in `resolved_values.py`, used today by both the
  Terraform backend-config path and the Helm values path (ADR-0075). It resolves
  against `ResolvedValues.features`, which is built per-deployment from the active
  environment's `spec.features[]` (see
  [environment.md](../config/environment.md#features)). Nothing new needs to be
  built to make a stage-level field resolve `${feature:...}`/`${var:...}` — this
  reuses the existing mechanism outright, satisfying the "no new resolution
  machinery" premise of the original proposal.
- **`self._resolved_values` is populated before the stage-execution loop starts**
  ([`run_deploy_command.py`](../../src/strata/commands/deploy/run_deploy_command.py#L1051)),
  well ahead of `_run_stages()`. Any gating check has everything it needs available
  up front, per stage, with no reordering of the existing resolve → lock → execute
  sequence.
- **`ManifestStageModel.status` already documents `"skipped"` as a valid value**
  ([`deployment_manifest_model.py`](../../src/strata/models/deployment_manifest_model.py#L175))
  — but nothing in the codebase ever constructs one with that status today. It is
  an aspirational/unused value, not a working feature. The plumbing that would
  make it real already exists, though:
  [`_record_stage_result()`](../../src/strata/commands/deploy/base_deploy_command.py#L950)
  accepts an arbitrary status string and appends to `self._stage_results`, and the
  deploy-log is *built from* `self._stage_results`
  ([`_write_deploy_log_and_forward()`](../../src/strata/commands/deploy/base_deploy_command.py#L1449)) —
  so recording `"skipped"` once would flow to both the manifest and the deploy-log
  for free. One real gap: `DeployLogStageModel.success` is a plain bool computed as
  `sr.status == "success"` — a skipped stage would currently render as
  `success=False`, indistinguishable from an actual failure. That model has no
  tri-state today.
- **`depends_on` has no effect on deploy-time execution order today.** It is
  declared on [`DeploymentStageModel`](../../src/strata/models/deployment_model.py#L244)
  and is consumed *only* by
  [`diagram_source_controller.py`](../../src/strata/controllers/diagram_source_controller.py#L404)
  and [`graph_controller.py`](../../src/strata/controllers/graph_controller.py#L87)
  to draw visualization edges. `RunDeployCommand._run_stages()` iterates
  `stages_to_run` in plain file-declaration order — no topological sort, no
  dependency check, no cycle detection. This means any "does a dependent stage
  skip when its dependency is skipped" semantics would be **the first time
  `depends_on` gains any runtime enforcement power at all** for deployment stages
  — a materially bigger change than "add a gating field," and arguably a
  separate decision in its own right.
- **The `helm_namespaces` CLI-override precedent is real**: its docstring states
  "Overridden per-run by `strata deploy run --namespace NAME`, which takes
  precedence over this declarative list" — an established, working
  declarative-plus-CLI-override pattern already shipped in this exact model.
- **`enabled` + `condition` already exist on workspace resources — and both are
  inert.** [`WorkspaceResourceModel`](../../src/strata/models/workspace_model.py#L333)
  declares `enabled: bool = True` and
  `condition: Optional[str]` ("Conditional expression for resource inclusion
  (e.g., `'{{ environment }} == production'`)"), mirrored as overrides on
  [`EnvironmentResourceOverrideModel`](../../src/strata/models/environment_model.py#L116).
  Neither is consumed anywhere: the only code that touches them is the override
  merge in [`deployment_service.py`](../../src/strata/services/deployment_service.py#L805),
  which copies the values, and **nothing reads the result** — every builder
  iterates `spec.resources` with no `.enabled` guard. Setting `enabled: false` on
  a workspace resource changes nothing today. Three consequences for this ADR:
  1. `enabled` is **not** new syntax — but the existing precedent is a broken
     one. Shipping a working `stages[].enabled` alongside a silently-inert
     `resources[].enabled` would be actively misleading.
  2. `condition`'s documented example syntax (`'{{ environment }} == production'`)
     matches **no** grammar implemented anywhere in the codebase — it is a fifth
     shape, never parsed, and it is missing from ADR-0073's expression inventory.
  3. This is the same defect class [ADR-0078](0078-scoping-variables-and-features-to-provisioners.md)
     removed the `references` field for ("never consumed… nothing reads the
     result"). `enabled`/`condition` were simply not reviewed at that time.
     Neither field is documented in `docs/config/workspace.md` or
     `docs/config/environment.md`.
- **The `--stage`/`--scope` filter is already duplicated** between
  [`run_deploy_command.py`](../../src/strata/commands/deploy/run_deploy_command.py#L1142)
  and [`destroy_deploy_command.py`](../../src/strata/commands/deploy/destroy_deploy_command.py#L186)
  — identical logic, and an identical `--scope` error message, but the two
  `--stage` not-found messages have **drifted apart**: run says `"Stage 'x' not
  found in deployment definition. Available: […]"`, destroy says `"Stage 'x' not
  found. Available: […]"`. A textbook illustration of why the repo's
  "one implementation, not copies" rule exists. Adding gating to each would make a
  third and fourth copy.
- **String→bool coercion is already duplicated too** — `raw.lower() not in
  ("false", "0", "no", "")` appears twice within
  [`base_builder.py`](../../src/strata/builders/base_builder.py#L157) alone
  (constant-store and environment-store feature branches). Any gating check needs
  the same predicate, since `resolve_expr_string()` returns a *string*
  (`${feature:x}` → `"true"`/`"false"`), which would make it a third copy.

- **GitHub Actions' `needs:`/`if:` model is a well-known, directly analogous prior
  art** for the exact shape of problem this ADR describes: a job's `if:` condition
  gates whether it runs at all; a skipped job gets a first-class, recorded
  `conclusion: skipped` (never silently absent); and — most relevant to Open
  Question 3 — a job whose `needs:` dependency was skipped/failed is **itself
  skipped by default**, with an explicit opt-out (`if: always()`, or checking
  `needs.<job>.result`) required to run anyway. This doesn't reduce the
  `depends_on`-enforcement scope gap noted above — it still requires building
  real dependency-aware execution for the first time — but it gives Open
  Question 3 a concrete, battle-tested candidate default instead of an
  open-ended shape question.
- **Strata has already answered "raw Jinja vs. closed grammar" once, and chose
  closed grammar.** [`diagram_expressions.py`](../../src/strata/utils/diagram_expressions.py#L1)
  (`spec.style.highlight[].condition`, ADR-0034) deliberately parses a tiny
  `<field> <op> <value>` grammar instead of accepting raw Jinja, and says why in
  its own docstring: "A closed grammar means a typo produces a validation error
  naming the problem, instead of a Jinja expression that silently evaluates to
  false and leaves the author wondering why nothing is highlighted."
  `gate_controller.py`'s cost/CVE threshold conditions independently made the
  same choice (a hand-rolled `_compare()` over `>=`/`<=`/etc., not Jinja).
  Separately, [`ExpressionModel`](../../src/strata/models/expression_model.py#L1)
  (ADR-0073) defines a `kind="jinja"` variant — boolean/comparison expression
  evaluation via Jinja2's `Environment.compile_expression()` — which **is** wired
  to a real call site ([`gate_controller._compare()`](../../src/strata/controllers/gate_controller.py#L61)).
  Note carefully *how*: the author writes a closed expression (`">= 1000"`), and
  strata compiles that down to a machine-built Jinja fragment
  (`f"actual {op} threshold"`). Jinja is the **execution engine**, never the
  authored surface. `diagram_expressions.py` does the same thing. So a
  GitHub-Actions-style raw authored expression (`enabled: "{{ features.enable_dispatcher_api }}"`)
  would still be a new pattern for an *authored* condition field — whereas
  `${feature:KEY}` (ADR-0075) is already a closed, single-purpose, fully-implemented
  token that fits the original proposal's own example
  (`enabled: ${feature:enable_dispatcher_api}`) with zero new grammar and zero new
  code. See [ADR-0073's 2026-09-17 addition](0073-embedded-string-syntax-inventory-and-creep-prevention.md#addition-2026-09-17-impact-of-converging-every-expression-on-jinja2-syntax)
  for the full impact analysis of converging every expression on Jinja syntax, and
  why it concluded against it for existing sites.

## Non-goals (for this ADR)

- This ADR does not decide whether `depends_on` gains general execution-order
  enforcement. That is flagged above as a discovered gap and a likely-necessary
  companion decision, not assumed to be in scope here.
- This ADR does not pick the final field name, type, or validator shape for a
  gating condition — see Open Questions.
- This ADR does not decide whether gating belongs on the stage or on the workspace
  provisioner. See Open Questions — there is a real architectural argument
  (`ResolvedValues` is a per-deployment/environment concept; workspace provisioners
  are deployment-agnostic) that pre-selects one direction, but it is recorded as an
  open question rather than a settled decision.

## Open Questions

**All resolved 2026-09-17 — see [Decision Outcome](#decision-outcome). Retained as
the record of what had to be settled, and why each mattered.**

These need explicit answers — each has a distinct audit-trail or execution
consequence — before a solution is designed, not left to emerge implicitly during
implementation:

1. **Skipped-stage recording.** Confirmed gap: `status: "skipped"` exists in the
   manifest model's docstring but is never produced. Needs: (a) wiring a real
   skip path into `_record_stage_result()`, and (b) a schema change to
   `DeployLogStageModel` (currently a plain `success: bool`) so a skipped stage
   is distinguishable from both success and failure in the deploy-log, not just
   the manifest.
2. **No backend initialization for a skipped stage.** Structurally satisfied by
   construction if the gating check happens before `_execute_stage_provisioning()`
   is called (that call is what constructs the deployer/backend) — needs
   confirmation this ordering is preserved wherever the final check is placed.
3. **`depends_on` interaction.** Given `depends_on` currently has zero runtime
   effect (see above), this question is really: *do we want to introduce
   dependency-aware execution as part of this change, or explicitly declare that
   a gated stage's dependents are unaffected (author's responsibility to keep
   `enabled` expressions consistent across a dependency chain) for a first
   version?* Needs an explicit answer either way — not something that should be
   allowed to fall out implicitly from whatever the implementation happens to do.
   A concrete, precedented candidate for "yes, cascade" exists: GitHub Actions'
   `needs:` default (a job whose dependency was skipped is itself skipped, with
   an explicit `if: always()`-style opt-out) — see the Facts section above. This
   is recorded as a candidate answer, not a decision: adopting it still means
   building topological/dependency-aware execution for `depends_on` for the
   first time, which is the scope question that actually needs settling.
4. **Precedence with `--stage`/`--scope`.** Working assumption, matching the
   `helm_namespaces` precedent: CLI-level selection wins over the declarative
   condition (an operator explicitly naming a stage should be able to force it to
   run regardless of `enabled`). Needs confirmation this is desired here too, and
   whether forcing a disabled stage to run via `--stage` should surface a warning
   given it deviates from the declared condition.
5. **Field shape** (raised during initial discussion, leaning answer recorded but
   not finalized): a gating field should accept either a native YAML boolean or a
   resolvable expression string (`${feature:...}` / `${var:...}`), coerced/resolved
   at the same point `self._resolved_values` becomes available — not a
   boolean-only field, since that would preclude the `${feature:}`/`${var:}` reuse
   that is the entire premise of the original proposal.
6. **Expression syntax — how open should the condition be?** Every candidate maps
   onto an expression mechanism strata already has (ADR-0073's expression-system
   inventory), so this is a reuse choice, not a greenfield design. See the
   dedicated section below; the real fork is whether **compound** conditions
   (more than one flag, `AND`/`OR`, negation) are a current need or a
   hypothetical one.

## Candidate condition syntaxes, mapped onto strata's existing expression system

ADR-0073 inventoried every embedded-string expression in the codebase and split
them into kinds. A stage-gating condition is a new consumer of that system, not a
new system — so the table below places each candidate next to the existing
mechanism it would reuse, with the concrete YAML it produces for the running
`dispatcher_api` example.

| Candidate                            | Existing mechanism it reuses                                                                                             | Status today                                                  | Expressiveness                                                          | New code needed                                                        |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------- | ----------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| **A.** `${feature:KEY}`/`${var:KEY}` | [`resolved_values.py`](../../src/strata/utils/resolved_values.py) — `resolve_expr_string()` / `EXPR_PATTERN` (ADR-0075)  | Fully implemented, 2 live call sites (Terraform, Helm)        | Single reference, truthy/falsy. No `AND`/`OR`/negation.                 | None for resolution; only the consuming gate check                     |
| **B.** `<field> <op> <value>`        | [`diagram_expressions.py`](../../src/strata/utils/diagram_expressions.py#L24) — closed grammar → compiled Jinja fragment | Implemented for diagrams (ADR-0034); pattern, not shared code | `==`, `!=`, `in`. Still no `AND`/`OR` at the existing precedent's scope | A new grammar module mirroring the existing one (different vocabulary) |
| **C.** `op value` threshold string   | [`gate_controller.py`](../../src/strata/controllers/gate_controller.py#L57) — `_compare()` over `>=`/`<=`/etc.           | Implemented for gates (cost/CVE/time)                         | Numeric/ordinal comparison                                              | n/a — see "why C doesn't transfer" below                               |
| **D.** raw Jinja / GH-Actions style  | [`ExpressionModel(kind="jinja")`](../../src/strata/models/expression_model.py#L81) — `Environment.compile_expression()`  | Wired, but only as an *engine* for machine-built fragments    | Arbitrary boolean logic                                                 | Making Jinja an **authored** surface for the first time                |

### A — `${feature:KEY}` / `${var:KEY}` (ADR-0075)

```yaml
stages:
  - name: dispatcher_api
    provisioner: dispatcher_api
    enabled: ${feature:enable_dispatcher_api}
```

Resolution is already built — this is the same call `TerraformDeployer` makes for
backend config and `HelmDeployer` makes for chart values. It matches the original
proposal's own example verbatim. Limitation: one reference, evaluated
truthy/falsy. A "disabled when X" case has no negation form today
(`${feature:!x}` does not exist), so it would need either a second field or an
inverted flag in the environment.

### B — closed `<field> <op> <value>` grammar (ADR-0034 pattern)

```yaml
stages:
  - name: dispatcher_api
    enabled: "features.enable_dispatcher_api == true"
  - name: reporting
    enabled: "variables.tier in [gold, platinum]"
```

Reuses the *pattern* (anchored regex grammar compiled down to a Jinja fragment,
values always emitted as quoted literals so an authored value can never become
code), not the diagram module itself — the field vocabulary differs
(`features.x`/`variables.x` against `ResolvedValues` vs. a diagram node's
attributes). Keeps ADR-0034's stated motivation intact: a typo produces a named
validation error instead of an expression that silently evaluates false.

### C — gate-style `op value` (why it doesn't transfer)

`GateWhenConditionsModel`'s operator strings (`">= 1000"`, `">= high"`,
`"02:00-04:00"`) exist because gates compare **numeric/ordinal/time** thresholds,
where an operator carries real meaning. A boolean "is this feature on" needs no
operator — Option A already expresses it directly. Listed here for completeness
so the inventory is exhaustive, not as a contender.

### D — raw Jinja / GitHub Actions style

```yaml
stages:
  - name: dispatcher_api
    enabled: "{{ features.enable_dispatcher_api and variables.region == 'eu' }}"
```

The most expressive option and the one most authors would recognise (GH Actions'
`if: ${{ ... }}`). It is also the only candidate that would make Jinja an
*authored* surface: today Jinja is used purely as an execution engine for
fragments strata itself constructs (`gate_controller._compare()`,
`diagram_expressions.parse_condition()`), never for a string a user typed.
ADR-0034 explicitly rejected raw authored Jinja for conditions, and
[ADR-0073's impact analysis](0073-embedded-string-syntax-inventory-and-creep-prevention.md#addition-2026-09-17-impact-of-converging-every-expression-on-jinja2-syntax)
concluded against converging authored syntaxes on Jinja generally. Choosing it
means overturning that precedent knowingly — which is legitimate, but should be
recorded as such rather than arrived at by default.

### Where this actually forks

A and B are the real contenders; C does not apply and D is precedent-breaking.
The deciding question is narrow: **does stage gating need compound conditions?**
The originating example (`enabled: ${feature:enable_dispatcher_api}`) never asked
for more than a single flag. If single-flag gating is the requirement, A ships
with no new expression machinery at all. If compound conditions are a real
near-term need, B is the smallest step that keeps ADR-0034's safety property; D
is only warranted if even B proves insufficient.

## Considered Options — where the gate lives

(Syntax options are covered in their own section above; this section is only about
*which schema position* carries the condition.)

- **Gate on the stage** (`spec.stages[].<field>`) — resolves per-deployment against
  `ResolvedValues`, which is itself already deployment/environment-scoped. Matches
  where every other per-environment value in a stage already lives (`secrets`,
  `helm_namespaces`).
- **Gate on the workspace provisioner** (`spec.provisioners[].<field>`) — the
  provisioner owns `source`/`backend`, so this reads naturally as "don't even
  initialize this provisioner's backend." But a workspace provisioner has no
  environment context of its own — `ResolvedValues` (and therefore
  `${feature:}`/`${var:}`) is only meaningful once a deployment + environment are
  known, which a workspace definition alone does not carry. Gating here would need
  either a second, workspace-scoped resolution mechanism (violates "no new
  resolution machinery") or would only be resolvable indirectly through whichever
  stage references the provisioner — which collapses back to stage-level gating
  through an extra layer of indirection.

## Decision Outcome

Chosen: **a `stages[].enabled` field on `DeploymentStageModel`, accepting a YAML
boolean or an existing `${feature:}`/`${var:}` expression (candidate A), evaluated
before pre-flight, with skip propagating through `depends_on`.**

```yaml
stages:
  - name: core_iac
    provisioner: platform_iac

  - name: dispatcher_api
    provisioner: dispatcher_api
    enabled: ${feature:enable_dispatcher_api}   # or a plain true / false
    depends_on: [core_iac]
    on_failure: stop
```

### D1 — Field and syntax

`enabled: Optional[Union[bool, str]] = None`, where `None` ≡ enabled (non-breaking
for every existing deployment file). The field name is fixed by D9 below —
`enabled`, the same word on every schema that gates inclusion, with no alias. A
string value is resolved with the existing `resolve_expr_string()`; an
unresolvable reference is a **hard error**, never a silent "disabled"
(ADR-0075's fail-loud driver). Syntax candidate A is chosen over B/C/D per the
candidate-syntax section above: the requirement is single-flag gating, and A needs
no new expression machinery. Compound conditions are deliberately deferred — B
remains the upgrade path if they become a real need.

### D2 — Where the gate is evaluated

Immediately **after** the `--stage`/`--scope` filters and **before**
`_preflight_check_provisioners()`. This is not an arbitrary placement: pre-flight
calls `_create_deployer(stage)` plus `validate_workspace()`/`validate_environment()`
for *every* stage it is given, so gating any later would still require a disabled
estate's tooling and cloud auth to be present. Gating here makes Goal 3 (no backend
initialisation for a skipped stage) true by construction rather than by assertion.

### D3 — `deploy run` only; `deploy destroy` is never gated

`enabled` gates `deploy run`. `deploy destroy` always considers every stage.

Rationale: if a flag is flipped `true → false` *after* a stage has deployed real
infrastructure, a gated destroy would silently skip tearing that infrastructure
down — leaving orphaned resources that still bill, still present a security
surface, and are no longer visible to strata. The converse is harmless: destroying
a never-deployed stage is a no-op against empty state. Asymmetry is the safe
direction here.

**Corollary that must be documented prominently:** `enabled: false` means *"stop
deploying this"*, **not** *"remove this"*. Flipping a flag off does not destroy
anything.

### D4 — `--stage` cannot select a disabled stage (CLI does **not** override)

`strata deploy run --stage dispatcher_api` against a deployment where that stage
is disabled is a **hard error** naming the stage and the condition that disabled
it — not a silent skip, and not a forced run.

This deliberately departs from the `helm_namespaces` precedent, and the difference
is substantive: `helm_namespaces` is a *scoping* list (which of N namespaces to
act on), whereas `enabled` is a *correctness condition* tied to an environment.
Forcing a stage that an environment declares inapplicable creates infrastructure
that should not exist there — a materially larger blast radius than narrowing a
namespace list. The restriction can be relaxed later behind an explicit opt-out
flag; the reverse (tightening a permissive default after people depend on it)
cannot.

### D5 — Skip propagates through `depends_on`

A stage whose `depends_on` includes a skipped stage is itself skipped,
transitively, and recorded as such. This follows GitHub Actions' `needs:` default,
which is the behaviour most operators will already expect.

This is the largest part of the change, because it requires giving `depends_on`
runtime meaning for the first time (today it only draws diagram edges). Required:
dependency-ordered execution, cycle detection, and a validation error for a
`depends_on` naming a stage that does not exist. The skip-propagation pass runs
once, up front, alongside D2's gate evaluation — not lazily inside the stage loop
— so the full skip set is known before pre-flight.

### D6 — Skipped stages are recorded, never omitted

Each gated-out stage is recorded via the existing `_record_stage_result()` with
`status="skipped"` — finally making real the value `ManifestStageModel.status` has
documented all along. Because the deploy-log is assembled *from*
`self._stage_results`, this reaches both artifacts from one call site.

The one schema gap must be closed: `DeployLogStageModel` carries only
`success: bool` (computed as `sr.status == "success"`), so a skipped stage would be
indistinguishable from a failed one. Add a `status` field mirroring the manifest's,
carried straight through as `status=sr.status`, leaving `success` untouched for
backward compatibility.

The recorded skip should also carry *why* — the condition expression and its
resolved value — so the audit trail answers "deliberately not deployed at v1.2.0"
rather than merely "absent".

### D7 — Shared implementations, not copies (ADR-0073 rule)

Two extractions are **required** by this change, not optional cleanups — without
them this ADR adds a third and fourth copy of logic that is already duplicated:

1. **Stage selection.** The `--stage`/`--scope` filter block is currently
   copy-pasted between
   [`run_deploy_command.py`](../../src/strata/commands/deploy/run_deploy_command.py#L1142)
   and [`destroy_deploy_command.py`](../../src/strata/commands/deploy/destroy_deploy_command.py#L186).
   It becomes one shared helper that both commands call, taking a flag for
   whether gating applies (per D3: on for run, off for destroy). Gating,
   skip-propagation and the D4 error all live inside it — one place, one set of
   semantics, one set of error messages.
2. **String→bool coercion.** `raw.lower() not in ("false", "0", "no", "")` already
   appears twice inside [`base_builder.py`](../../src/strata/builders/base_builder.py#L157).
   It becomes one shared predicate that both existing call sites and the new
   gating check import, so "what counts as false" is defined once.

#### Follow-up discovered during implementation — five more copies

The `--stage` filter turned out to have **seven** copies, not two. Beyond the
run/destroy pair above, the same block is reimplemented in five more commands,
each with its own terser wording (`"Stage 'X' not found. Available: …"`) — the
exact drift D7 fixed between run and destroy, repeated five times over:

| Command                                                                          | Base class             | Filter | `--scope`? | Message                                    |
| -------------------------------------------------------------------------------- | ---------------------- | ------ | ---------- | ------------------------------------------ |
| `deploy run`                                                                     | `BaseDeployCommand`    | shared | yes        | "…not found **in deployment definition**." |
| `deploy destroy`                                                                 | `BaseDeployCommand`    | shared | yes        | same                                       |
| [`build plan`](../../src/strata/commands/builders/plan_build_command.py#L346)    | **`BaseBuildCommand`** | inline | no         | "…not found."                              |
| [`deploy drift`](../../src/strata/commands/deploy/drift_deploy_command.py#L82)   | `BaseDeployCommand`    | inline | no         | "…not found."                              |
| [`deploy health`](../../src/strata/commands/deploy/health_deploy_command.py#L83) | `BaseDeployCommand`    | inline | no         | "…not found."                              |
| [`deploy plan`](../../src/strata/commands/deploy/plan_deploy_command.py#L60)     | `BaseDeployCommand`    | inline | no         | "…not found."                              |
| [`deploy status`](../../src/strata/commands/deploy/status_deploy_command.py#L81) | `BaseDeployCommand`    | inline | no         | "…not found." + `str(s.name)`              |

Two things this surfaced that are worth more than the duplication itself:

- **The repeated code is mostly plumbing, not the filter.** Every site repeats
  spec-lookup → filter → `self._errors.append(...)` → `return False`. Routing to
  `select_stages` alone makes call sites *longer* (5 lines vs 4); the win only
  appears if the plumbing is absorbed too.
- **`--scope` is supported on `run`/`destroy` only.** The five read-only commands
  accept `--stage` but not `--scope` — arguably a larger user-facing
  inconsistency than the duplicate message. Consolidation makes closing it nearly
  free, but it is new feature surface and is deliberately **not** part of Phase 8.

Nothing was consolidated during Phases 1–6: all five are correctly **non-gated**
(D11) and none calls `select_stages`, so there is no correctness bug today, and
changing their error text does not belong inside a gating change. Tracked as
Phase 8 rather than left to be rediscovered.

### D8 — Cross-schema cleanup: making `enabled` mean the same thing everywhere

Shipping a working `stages[].enabled` while `resources[].enabled` remains silently
inert is a trap: the same word would mean "gates deployment" in one file and
"nothing at all" in another. Worse, the fields are not merely dead — the scaffold
template shipped by `strata sln init`
([`templates/solution/dot.strata/templates/workspace.yaml`](../../src/strata/templates/solution/dot.strata/templates/workspace.yaml#L91))
actively advertises both to every new user:

```yaml
- name: template_resource
  file: config/{platform}/resources/template-resource.yaml
  # enabled: true
  # condition: "{{ environment }} == production" # conditional inclusion
```

No ADR, doc, or code comment records why they exist, and git history offers
nothing — the only commit touching the `condition` description is the bulk
`Rename xyz-platform to strata (#30)`, so both fields predate strata's current
identity. The intent is nonetheless legible from their placement: they sit under a
`# Conditional inclusion` header beside `count` and `depends_on`, and
`EnvironmentResourceOverrideModel` mirrors both so an environment can flip them.
The design intent was "include/exclude a resource, statically or per-environment";
the override plumbing was built, the consumer never was.

This ADR does not fix them, but makes them a blocking follow-up — with a different
disposition for each:

- **`condition`: remove.** It is not only inert, it is *redundant*.
  `EnvironmentResourceOverrideModel` already lets an environment set
  `enabled: false` for a named resource, so per-environment inclusion is fully
  expressible with plain `enabled` plus the existing override mechanism — no
  expression language needed. That redundancy is the most likely reason nobody
  ever implemented it. Its phantom `'{{ environment }} == production'` example
  must go regardless: a fifth expression shape that no engine has ever parsed,
  absent from ADR-0073's inventory, and advertised in the scaffold. Removal is
  also required by D9 — `condition` must not survive as a second spelling of
  `enabled`.
- **`enabled`: implement rather than remove.** Unlike ADR-0078's `references`, it
  has a coherent meaning, an existing per-environment override path, and is
  already in the template users copy from. Removing it would break a reasonable
  (if currently ineffective) mental model; making it live delivers what the
  schema has always claimed — and completes the D9 convention, taking `enabled`
  from six live positions out of nine to all nine.

Note also that `modules[].enabled` is only marginally live: read in exactly one
place ([`workspace_model.py`](../../src/strata/models/workspace_model.py#L809)),
and only to decide which modules count when validating the "exactly one `main`
slot" rule — no builder filters modules by it either.

#### Full cross-schema inventory — every `enabled` / `condition` / `when` in the codebase

A complete sweep of `src/strata/models/` for these three field names, with each
one's verified consumer (or absence of one) and the disposition this ADR assigns.
This is the actionable cleanup list — nothing below is left to be rediscovered.

| #   | Model / field                                | Declared                                                                           | Consumed by                                                                                         | State     | Disposition                              |
| --- | -------------------------------------------- | ---------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- | --------- | ---------------------------------------- |
| 1   | `PolicyModel.enabled`                        | [policy_model.py#L36](../../src/strata/models/policy_model.py#L36)                 | [policy_engine.py#L30](../../src/strata/validators/policies/policy_engine.py#L30)                   | live      | keep as-is                               |
| 2   | `IntegrationModel.enabled`                   | [integration_model.py#L81](../../src/strata/models/integration_model.py#L81)       | [integration_service.py#L128](../../src/strata/services/integration_service.py#L128)                | live      | keep as-is                               |
| 3   | `AuditEventPolicyModel.enabled`              | [audit_config_model.py#L94](../../src/strata/models/audit_config_model.py#L94)     | [audit_config_model.py#L166](../../src/strata/models/audit_config_model.py#L166)                    | live      | keep as-is                               |
| 4   | audit sink `.enabled`                        | [audit_config_model.py#L178](../../src/strata/models/audit_config_model.py#L178)   | [audit_controller.py#L644](../../src/strata/controllers/audit_controller.py#L644)                   | live      | keep as-is                               |
| 5   | `ConfigurationOutputsModel.enabled`          | [configuration_model.py#L428](../../src/strata/models/configuration_model.py#L428) | [base_deploy_command.py#L1209](../../src/strata/commands/deploy/base_deploy_command.py#L1209)       | live      | keep as-is                               |
| 6   | `DeploymentLockingModel.enabled`             | [deployment_model.py#L385](../../src/strata/models/deployment_model.py#L385)       | [base_deploy_command.py#L151](../../src/strata/commands/deploy/base_deploy_command.py#L151)         | live      | keep as-is                               |
| 7   | `WorkspaceModuleModel.enabled`               | [workspace_model.py#L211](../../src/strata/models/workspace_model.py#L211)         | [workspace_model.py#L809](../../src/strata/models/workspace_model.py#L809) — *validation rule only* | marginal  | **implement** — must filter builds too   |
| 8   | `EnvironmentModuleOverrideModel.enabled`     | [environment_model.py#L189](../../src/strata/models/environment_model.py#L189)     | [deployment_service.py#L879](../../src/strata/services/deployment_service.py#L879) — *copy only*    | marginal  | follows #7 automatically                 |
| 9   | `WorkspaceResourceModel.enabled`             | [workspace_model.py#L333](../../src/strata/models/workspace_model.py#L333)         | [deployment_service.py#L805](../../src/strata/services/deployment_service.py#L805) — *copy only*    | **inert** | **implement**                            |
| 10  | `EnvironmentResourceOverrideModel.enabled`   | [environment_model.py#L116](../../src/strata/models/environment_model.py#L116)     | [deployment_service.py#L805](../../src/strata/services/deployment_service.py#L805) — *copy only*    | **inert** | follows #9 automatically                 |
| 11  | `WorkspaceResourceModel.condition`           | [workspace_model.py#L337](../../src/strata/models/workspace_model.py#L337)         | [deployment_service.py#L807](../../src/strata/services/deployment_service.py#L807) — *copy only*    | **inert** | **remove**                               |
| 12  | `EnvironmentResourceOverrideModel.condition` | [environment_model.py#L120](../../src/strata/models/environment_model.py#L120)     | [deployment_service.py#L807](../../src/strata/services/deployment_service.py#L807) — *copy only*    | **inert** | **remove** (with #11)                    |
| 13  | `DiagramHighlightModel.condition`            | [diagram_model.py#L146](../../src/strata/models/diagram_model.py#L146)             | [diagram_service.py#L100](../../src/strata/services/diagram_service.py#L100)                        | live      | **keep** — different concept (see below) |
| 14  | `DeploymentGateModel.when`                   | [gate_model.py#L94](../../src/strata/models/gate_model.py#L94)                     | [gate_controller.py](../../src/strata/controllers/gate_controller.py#L61)                           | live      | **keep** — different concept (see below) |
| 15  | `DeploymentStageModel.enabled`               | *new — this ADR*                                                                   | *new — D2*                                                                                          | new       | **add**                                  |

Summary: 10 `enabled` positions today (6 live, 2 marginal, 2 inert) → 11 with #15,
all live. 3 `condition` positions (1 live, 2 inert) → 1, the diagram one. 1 `when`,
unchanged.

**Rows 13 and 14 are deliberately excluded from the `enabled` convention** — they
are different concepts, not different spellings:

- `style.highlight[].condition` is a predicate evaluated **per diagram node** to
  decide styling. It is not a property of the rule's own existence. If a highlight
  rule ever needs include/exclude, it would gain `enabled` *alongside* `condition`.
- `gates[].when` decides **when an approval gate fires**, not whether the gate is
  configured. A gate with `when: {cost_delta_monthly: ">= 1000"}` is always
  present; `when` governs triggering. Same rule: a gate needing include/exclude
  would gain `enabled` alongside `when`.

**Rows 7/8 carry a behavioural risk worth calling out.** `modules[].enabled`
currently *looks* implemented — it is read, and a `main`-slot validation rule
honours it — but no builder filters modules by it. Making it actually filter is a
behaviour change for any workspace that already sets `enabled: false` on a module
expecting it to work (it does not) or expecting it to be ignored (it is). That
needs a changelog entry, unlike rows 9–12 where nothing can depend on current
behaviour because there is none.

### D9 — The field is named `enabled`, on every schema that gates inclusion, with no alias

`enabled` is the single word strata uses for "is this thing included", everywhere
it applies — `stages[]`, `resources[]`, `modules[]`, and the positions that
already use it. No alias, no synonym accepted.

**`if` is eliminated on a technical ground, not a stylistic one:** it is a Python
keyword and cannot be a model attribute. It would require
`if_: str = Field(alias="if")`, forcing `by_alias=True` through every
`model_dump()` and breaking the direct-attribute-access style used throughout the
codebase. GitHub Actions can afford `if:` because its schema is not bound to
Python identifiers; ours is.

**Industry precedent does not settle it** — there is no consensus word:
Azure Pipelines uses `condition:`, GitHub Actions `if:`, Jenkins/Ansible/Tekton/Argo
`when:`, GitLab `rules: - if:`, CloudFormation `Condition:`. Notably Helm — the
most-copied pattern in the Kubernetes ecosystem — uses `enabled:` in values, and
its `Chart.yaml` `condition:` field takes a *pointer to an `enabled` key*
(`condition: subchart.enabled`).

**Strata's own footprint decides it.** `enabled` is already the de facto
convention, in six live positions: `policy_model`
([`policy_engine.py`](../../src/strata/validators/policies/policy_engine.py#L30)),
`integration_model` ([`integration_service.py`](../../src/strata/services/integration_service.py#L128)),
audit event + sink ([`audit_controller.py`](../../src/strata/controllers/audit_controller.py#L644)),
`configuration_model` outputs, and `deployment_model` locking — plus the two
inert/marginal ones this ADR fixes (D8). By comparison `condition` appears twice
(one live, for diagrams; one inert) and `when` once (gates).

**The cross-schema test is what makes it decisive.** `condition`/`when` are
conjunctions: they read correctly with an expression but absurdly with a literal
(`condition: false`, `when: false`). `enabled` reads correctly with both, and most
of the positions that need this word hold a plain boolean:

| Position                                | `enabled:`                | `condition:` | `when:` |
| --------------------------------------- | ------------------------- | ------------ | ------- |
| `stages[]`                              | `enabled: ${feature:x}` ✓ | ✓            | ✓       |
| `resources[]`                           | `enabled: false` ✓        | ✗            | ✗       |
| `modules[]`                             | ✓                         | ✗            | ✗       |
| `policies[]`/`integrations[]`/`sinks[]` | ✓ (already)               | ✗            | ✗       |

#### No `condition` alias for Azure Pipelines familiarity

Considered and rejected. **Every alias in strata today exists because the YAML word
is unusable as a Python identifier** — `as`
([`diagram_model.py`](../../src/strata/models/diagram_model.py#L111)), `from`
([`firewall_model.py`](../../src/strata/models/firewall_model.py#L70)), and
`validate` ([`configuration_model.py`](../../src/strata/models/configuration_model.py#L247),
which shadows Pydantic's own method). A `condition` → `enabled` alias would be the
first alias added for ergonomics rather than necessity, breaking a rule that is
currently clean and self-explaining.

It also costs real things: grepping `enabled:` would no longer find every gate;
`model_dump()` would emit one spelling while the user's file says the other; and a
validator plus error message would be needed for "both set" — a problem that only
exists because the alias created it.

**Instead, the error message teaches the word.** `_generate_fix_suggestions()`
([`run_validate_command.py`](../../src/strata/commands/validate/run_validate_command.py#L717))
already handles `extra_forbidden` with `difflib.get_close_matches(..., cutoff=0.5)`.
`condition` versus `enabled` scores far below that cutoff, so it currently falls
through to the generic "Valid fields: …" list. Adding a small known-synonym map
(`condition`/`when`/`if` → `enabled`) ahead of the difflib pass makes it explicit:

> Unknown field `'condition'`. Did you mean `'enabled'`? strata uses `enabled` for
> conditional inclusion on every schema; it accepts `true`/`false` or
> `${feature:KEY}`.

Azure Pipelines users get the discovery benefit on first encounter, strata keeps
one spelling permanently, and the same hint generalises to `when:`/`if:`.

### D10 — Plain `${...}` via `resolve_expr_string()`, **not** `ExpressionModel`

`enabled` holds a bare value (`true`, `false`, or `"${feature:KEY}"`) resolved by
[`resolve_expr_string()`](../../src/strata/utils/resolved_values.py#L211). It is
**not** an [`ExpressionModel`](../../src/strata/models/expression_model.py), and
gains no `kind:` discriminator. Three independent reasons, any one sufficient:

1. **ADR-0073's own scope rule forbids it.** That ADR states `kind:` is warranted
   only where *the same schema position* can validly hold more than one kind of
   expression — and explicitly names over-application as the same "creep" it exists
   to prevent, "just pointed the other direction," citing
   `GateWhenConditionsModel.cost_delta_monthly` and
   `style.highlight[].condition` as fields that correctly do *not* get one.
   `enabled` is exactly that case: the position always means one thing, so the
   field name already carries everything a discriminator would.
2. **`ExpressionModel` has no kind that fits.** Its kinds are `path`, `yaml`,
   `regex`, `jinja`. `${var:}`/`${secret:}`/`${feature:}` substitution is **not**
   one of them — it lives in `resolved_values.py` and appears as a *separate row*
   in ADR-0073's inventory table, outside the `ExpressionModel` family. Routing
   `enabled` through `ExpressionModel` would mean inventing a fifth kind, i.e.
   new machinery — directly contradicting this ADR's "no new resolution
   machinery" premise (Goal 1).
3. **The YAML shape is wrong for this field.** `ExpressionModel` serialises as a
   nested mapping (`{kind: …, expression: …}`, as in
   `PathConventionModel.rules`). That would force
   `enabled: {kind: …, expression: "${feature:x}"}` instead of
   `enabled: ${feature:x}`, and a plain `enabled: true` could not be expressed at
   all without wrapping — breaking D1's requirement that one field accept both a
   boolean and an expression, and breaking D9's cross-schema uniformity (every
   other `enabled` in strata is a bare bool).

If compound conditions later arrive (candidate B), that decision is revisited on
its own merits — a closed `field op value` grammar would still be a plain string,
and would still not need a `kind:` unless the position becomes genuinely
ambiguous.

### D11 — Which commands `enabled` affects

`enabled` is a **deploy-time** gate only. Stated explicitly per command, because
several other surfaces also enumerate `spec.stages[]` and would otherwise acquire
the behaviour by accident:

| Surface                                                                       | Gated?  | Why                                                                                                                                                                                                                                                 |
| ----------------------------------------------------------------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `deploy run`                                                                  | **Yes** | The originating requirement (D2).                                                                                                                                                                                                                   |
| `deploy destroy`                                                              | **No**  | D3 — gating destroy orphans already-deployed infrastructure.                                                                                                                                                                                        |
| `build run`                                                                   | **No**  | Artifacts are generated per provisioner, not per stage. Building an artifact is inert until applied, and not building it would make `--stage` recovery of a re-enabled estate impossible.                                                           |
| [`build plan`](../../src/strata/commands/builders/plan_build_command.py#L340) | **No**  | A plan is a *preview*. Showing "this stage is disabled and would be skipped" is more useful than hiding the stage; suppressing it would make a disabled stage indistinguishable from a deleted one — the exact confusion this ADR exists to remove. |
| [drift detection](../../src/strata/controllers/drift_controller.py#L152)      | **No**  | Same reasoning as D3, and more acute: drift against a *disabled* stage is precisely how an operator discovers infrastructure orphaned by a flag flip. Gating it would hide the failure mode D3 is designed to prevent.                              |
| gates (`_evaluate_deployment_gates`)                                          | **Yes** | Falls out of D2 for free — gates are evaluated after the filter, so a disabled stage cannot trigger an approval gate for work that will not happen.                                                                                                 |

The consistent principle: **gating suppresses *making changes*, never *observing
state*.** Anything that reports, plans, or detects continues to see every stage.

**Corollary, easy to miss:** "not gated" is not the same as "ignores `enabled`".
A read-only surface must never *filter* on it — but a human-facing one should
*disclose* it, the way `build plan` marks a stage `would_skip`. `deploy show`
currently does the first and not the second; see
[Adjacent finding](#adjacent-finding--deploy-show-previews-a-deployment-without-disclosing-gating).

### Consequences

- Good: no new expression syntax, no new resolution machinery — one additional
  call site for `resolve_expr_string()` (D1/D10) and one additional consumer for
  the `status="skipped"` value the manifest model already declares (D6).
- Good: fixes two pre-existing duplications rather than extending them (D7), and
  surfaces two inert fields that were quietly lying to users (D8).
- Good: the audit trail can finally distinguish "deliberately not deployed" from
  "absent" and from "deployed, no changes" — the originating requirement.
- Bad: D5 is a genuine scope increase. `depends_on` gains execution semantics for
  the first time, which brings ordering, cycle detection, and dangling-reference
  validation with it — meaningful new surface with its own test burden.
- Bad: D3's asymmetry (run gated, destroy not) is a rule people must learn. It is
  the safe asymmetry, but it is still one more thing that is not obvious from the
  schema alone, so it needs explicit documentation.
- Bad: D4 knowingly diverges from the `helm_namespaces` precedent, so "CLI always
  wins" ceases to be a blanket rule and becomes per-field. Justified above, but it
  is a consistency cost.
- Bad: D9 accepts a familiarity cost. For someone arriving from Azure Pipelines,
  `condition:` on a stage would feel more native than `enabled:`. Internal
  consistency across nine schema positions was judged worth more than matching one
  CI tool's vocabulary at one of them — mitigated, not erased, by the synonym hint.

## Design

Concrete shape of the implementation. Two behaviours below (§3.1 and §4) are
decisions in their own right that only surface once the algorithm is written out;
both are called out explicitly rather than left to the implementer.

### 1. New module — `strata/utils/stage_selection.py`

All selection logic lives in one module that both deploy commands import. A new
module rather than an addition to the existing
[`provisioner_resolution.py`](../../src/strata/utils/provisioner_resolution.py)
(which answers *"which namespaces/secrets does this stage touch"*) — "which stages
run at all" is a distinct question, and the module name should say so.

```python
@dataclass(frozen=True)
class StageSkip:
    """A stage that will not run, and why — the audit record for D6."""
    stage_name: str
    reason: Literal["disabled", "dependency_skipped"]
    detail: str                          # human-readable, persisted verbatim
    expression: Optional[str] = None     # raw `enabled` source, when reason == "disabled"
    resolved_value: Optional[str] = None # what it resolved to, when reason == "disabled"


@dataclass(frozen=True)
class StageSelection:
    to_run: List[DeploymentStageModel]   # dependency-ordered (§4)
    skipped: List[StageSkip]             # declaration order


def select_stages(
    all_stages: List[DeploymentStageModel],
    *,
    stage: Optional[str] = None,
    scope: Optional[str] = None,
    resolved: Optional[ResolvedValues] = None,
    apply_gating: bool = True,
) -> Tuple[StageSelection, List[str]]:
    """Resolve which stages run. Returns (selection, errors).

    Errors are returned, not raised — matching the commands' existing
    ``self._errors.append(...)`` convention.
    """
```

`apply_gating=False` is how `destroy` gets D3's behaviour: identical `--stage`/
`--scope` semantics, no `enabled` evaluation.

### 2. Shared predicate — `parse_bool()` in `resolved_values.py`

```python
_FALSE_TOKENS = frozenset({"false", "0", "no", ""})

def parse_bool(value: Any) -> bool:
    """One definition of 'what counts as false', shared by feature-flag parsing
    and stage gating (D7)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in _FALSE_TOKENS
    return bool(value)
```

Behaviour is byte-identical to the two existing copies in
[`base_builder.py`](../../src/strata/builders/base_builder.py#L153) (bool → as-is;
str → token test; else → `bool()`), so replacing them is a pure refactor.
`resolved_values.py` is the right home: it already owns `${...}` resolution, and
it is a **pure leaf module** (zero `strata` imports), so no call site can create an
import cycle by depending on it.

### 3. Selection algorithm

```
1. evaluate `enabled` for every stage in all_stages   → disabled set
2. propagate skip transitively through depends_on     → skipped set        (D5)
3. apply --stage filter    → error if unknown, or if it names a skipped stage (D4)
4. apply --scope filter    → error if no match
5. to_run = (filtered stages) - (skipped set), dependency-ordered            (§4)
```

Steps 1–2 run over **`all_stages`**, never the CLI-filtered subset — the
dependency graph must be complete for cascade to be correct.

Step 1 per stage:

| `enabled` value           | Result                                                   |
| ------------------------- | -------------------------------------------------------- |
| absent / `None`           | enabled (default — non-breaking for every existing file) |
| `bool`                    | used directly                                            |
| `str` with no `${...}`    | `parse_bool()` on the literal                            |
| `str` with `${...}`       | `resolve_expr_string()`, then `parse_bool()`             |
| unresolvable `${...}`     | **error** — never silently disabled (ADR-0075 fail-loud) |
| `str`, `resolved is None` | **error** — gating requested without resolved values     |

#### 3.1 Cascade applies to `enabled`, **not** to `--stage`/`--scope`

A stage skipped by `enabled` cascades to its dependents (D5). A stage merely *not
selected* by `--stage`/`--scope` does **not**.

This distinction is essential, not incidental: cascading CLI filtering would make
`--stage B` fail or self-skip whenever `B` declares `depends_on: [A]`, since `A`
is not in the filtered set — breaking the partial-run workflow `--stage` exists
for. The two are different statements: `enabled: false` is *"this does not apply
here"* (a durable fact that dependents must respect), while `--stage B` is *"run
only this, now"* (a deliberate operator-scoped action).

### 4. Dependency ordering must be **stable**

`depends_on` gains execution semantics for the first time (D5), so ordering is new
behaviour applied to files that already exist. Kahn's algorithm, with ready nodes
dequeued in **original declaration order**.

This guarantees: *if a deployment file is already in a valid order, the output
order is identical to the input.* Without that property, giving `depends_on`
meaning would silently reorder stages in every existing deployment — a breaking
change disguised as a feature. Any implementation that does not preserve this is
wrong, and it deserves a dedicated test.

Two failure modes, both surfaced as validation errors naming the offenders:
a cycle (`a → b → a`), and a `depends_on` naming a stage that does not exist.

### 5. Model changes

| Model                                                                             | Change                                                                                                     |
| --------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| [`DeploymentStageModel`](../../src/strata/models/deployment_model.py#L244)        | `enabled: Optional[Union[bool, str]] = None`                                                               |
| [`ManifestStageModel`](../../src/strata/models/deployment_manifest_model.py#L169) | `skip_reason: Optional[str]` — `status="skipped"` already valid, just never emitted                        |
| [`DeployLogStageModel`](../../src/strata/models/deploy_log_model.py#L31)          | `status: str` (mirrors the manifest) + `skip_reason: Optional[str]`; `success` unchanged for compatibility |

`DeployLogStageModel.success` stays `sr.status == "success"`, so a skipped stage
reads `success=false, status="skipped"` — unambiguous, and no existing consumer
breaks. A skipped stage keeps the existing timestamp fallbacks (deploy start,
duration 0); no new optionality is needed there.

### 6. Call-site changes

| Site                                                                                                       | Change                                                                                                                                                                                   |
| ---------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`run_deploy_command._execute_provisioning`](../../src/strata/commands/deploy/run_deploy_command.py#L1142) | replace the inline filter with `select_stages(..., apply_gating=True)`; record each `StageSkip` via `_record_stage_result(status="skipped", skip_reason=...)`; pass only `to_run` onward |
| [`destroy_deploy_command`](../../src/strata/commands/deploy/destroy_deploy_command.py#L186)                | replace the inline filter with `select_stages(..., apply_gating=False)`                                                                                                                  |
| [`base_builder._build_template_context`](../../src/strata/builders/base_builder.py#L153)                   | both truthiness copies → `parse_bool()`                                                                                                                                                  |
| [`DeploymentService._validate_dynamic`](../../src/strata/services/deployment_service.py)                   | new `_validate_stage_depends_on()` (dangling refs + cycles), sibling to the existing `_validate_helm_stage_namespaces()`                                                                 |
| `TerraformBuilder._validate_inputs`-equivalent                                                             | build-time check that every `${...}` in an `enabled` resolves against a declared variable/feature, mirroring the existing backend-config check                                           |

`self._resolved_values` is confirmed available: `_resolve_values()` runs at
[run_deploy_command.py#L161](../../src/strata/commands/deploy/run_deploy_command.py#L161),
`_execute_provisioning()` at
[#L178](../../src/strata/commands/deploy/run_deploy_command.py#L178).

Ordering within `_execute_provisioning` is load-bearing (D2): selection must
precede `_preflight_check_provisioners()`, which calls `_create_deployer()` per
stage.

### 7. Test matrix

| Area            | Cases                                                                                                                                     |
| --------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `parse_bool`    | bool passthrough; `"false"`/`"0"`/`"no"`/`""`/whitespace; `"true"`; non-string; **parity with the pre-refactor `base_builder` behaviour** |
| gate evaluation | absent; bool; literal string; `${feature:}` true/false; `${var:}`; unresolvable → error; string with `resolved=None` → error              |
| cascade         | direct; transitive (a→b→c); diamond; dependent of an *enabled* stage still runs                                                           |
| ordering        | **already-ordered file is unchanged** (§4); out-of-order file is reordered; cycle → error; dangling `depends_on` → error                  |
| CLI             | `--stage` on a disabled stage → error (D4); `--stage` on a stage whose dependency is disabled → **runs** (§3.1); `--scope` likewise       |
| recording       | manifest `status="skipped"` + `skip_reason`; deploy-log mirrors it; skipped ≠ failed                                                      |
| non-gated paths | `destroy`, `build run`, `build plan`, drift all still see a disabled stage (D11) — the regression tests from Phase 6                      |

### 8. Deliberately out of scope

- No `enabled` on `provisioners[]` — D-options section: a workspace provisioner has
  no environment context to resolve against.
- No compound conditions — candidate B remains the upgrade path (D1/D10).
- No parallel stage execution. `depends_on` gains *ordering*, not concurrency;
  the existing single-threaded loop is preserved so this change stays reviewable.

## Implementation Phases

**All eight phases implemented — see [Implementation Notes](#implementation-notes)
for the completion summary.** Retained as the record of how it was built: the
sequence, each phase's exit criteria, and the two stop-and-reconsider points.

Eight phases, each independently landable and reviewable. Every phase ends green
on `scripts/Check.ps1` (ruff, `mypy ./src ./tests`, pytest, docs build) — never
"green after the next phase".

Phases 1–4 deliver stage gating **with its full audit trail** — a complete,
shippable feature. Phase 5 (`depends_on` cascade) is an additive enhancement on
top. Phase 6 is optional polish. Phases 7–8 are cleanup and are **not**
prerequisites for anything above them.

Skip recording precedes the cascade deliberately: the reverse order would ship
cascade-skipping while the manifest and deploy-log still could not record a skip,
leaving cascade-skipped stages silently absent — the exact audit gap this ADR
exists to close.

### Phase 1 — Shared helpers (pure refactor, no behaviour change)

Lands the D7 extractions *before* anything depends on them, so the gating diff
that follows contains only new behaviour.

| Change                                                                                                                                                                             | Detail                                                                                                                                                                                                                                                       |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `parse_bool()` → [`resolved_values.py`](../../src/strata/utils/resolved_values.py)                                                                                                 | Design §2                                                                                                                                                                                                                                                    |
| [`base_builder.py`](../../src/strata/builders/base_builder.py#L153)                                                                                                                | both truthiness copies call it                                                                                                                                                                                                                               |
| new [`stage_selection.py`](../../src/strata/utils/stage_selection.py)                                                                                                              | `select_stages()` with `--stage`/`--scope` only; **no gating, no ordering**. `StageSkip`/`StageSelection` are defined in full here — including `reason="dependency_skipped"`, unreachable until Phase 5 — so the return shape never changes in a later phase |
| [`run_deploy_command`](../../src/strata/commands/deploy/run_deploy_command.py#L1142) + [`destroy_deploy_command`](../../src/strata/commands/deploy/destroy_deploy_command.py#L186) | inline filters replaced by the helper                                                                                                                                                                                                                        |

**Exit criteria:** existing deploy/destroy tests pass **unmodified** — the proof
this phase changed nothing. Plus `parse_bool` parity tests (Design §7).

**Two known snags, both verified against the code:**

1. *The two filters have already drifted.* Their `--stage` not-found messages
   differ (see Facts). No test asserts on either string, so unifying is safe —
   but it is still a user-visible change to `destroy`'s error text and belongs in
   the changelog, not silently in a "pure refactor". Unify on the more
   informative `run` wording.
2. *`_make_stage()` is a bare `MagicMock`.* The shared test helper in
   [`test_commands_deploy.py`](../../tests/strata/commands/test_commands_deploy.py#L343)
   sets `name`/`provisioner`/`topology`/`scope`/`on_failure`/`approval` but not
   `enabled` or `depends_on`. An unset `MagicMock` attribute is **truthy**, and
   iterates as **empty** rather than raising — so from Phase 3 onward a stage
   would look gated-by-expression, and from Phase 5 its `depends_on` would look
   like an empty list. Set both to `None` in this phase, while the helper is
   already being touched, rather than debugging it later.

### Phase 2 — `enabled` field + validation (schema only, still inert)

| Change                                                                             | Detail                                                                                                                  |
| ---------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| [`DeploymentStageModel.enabled`](../../src/strata/models/deployment_model.py#L244) | `Optional[Union[bool, str]] = None` (D1)                                                                                |
| build-time reference check                                                         | every `${...}` in an `enabled` resolves to a declared variable/feature, mirroring `TerraformBuilder._validate_inputs()` |
| `docs/config/deployment.md`                                                        | field documented                                                                                                        |

**Exit criteria:** a deployment declaring `enabled` validates; an `enabled`
referencing an undeclared feature fails `strata validate` with a message naming
the key. Nothing gates yet — deliberately.

### Phase 3 — Gate evaluation (the feature becomes real)

| Change                            | Detail                                                                        |
| --------------------------------- | ----------------------------------------------------------------------------- |
| `select_stages(apply_gating=...)` | Design §3 steps 1, 3–5; **no cascade yet**                                    |
| `run_deploy_command`              | `apply_gating=True`, positioned before `_preflight_check_provisioners()` (D2) |
| `destroy_deploy_command`          | `apply_gating=False` (D3)                                                     |
| D4 error                          | `--stage` naming a disabled stage                                             |

**Exit criteria:** a disabled stage does not deploy, **and** no deployer is
constructed for it — assert on `_create_deployer` not being called, not merely on
the stage being absent from results. That is the only test that actually proves
Goal 3.

Ship-able here: gating works end to end without Phase 5.

### Phase 4 — Skip recording (closes the audit-trail requirement)

| Change                                                                                        | Detail                                                                                        |
| --------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| [`ManifestStageModel.skip_reason`](../../src/strata/models/deployment_manifest_model.py#L169) | `status="skipped"` finally emitted                                                            |
| [`DeployLogStageModel`](../../src/strata/models/deploy_log_model.py#L31)                      | `+ status`, `+ skip_reason`; `success` unchanged                                              |
| `run_deploy_command`                                                                          | records each `StageSkip` via `_record_stage_result()`                                         |
| docs                                                                                          | D3 corollary (`enabled: false` ≠ destroy) stated prominently; D9 keyword rationale; D11 table |

**Exit criteria:** manifest and deploy-log both distinguish skipped from failed,
and the recorded reason names the expression and its resolved value. This is the
originating requirement — not optional.

Only `reason="disabled"` is reachable at this point; `StageSkip.reason`'s
`Literal` carries `"dependency_skipped"` from Phase 1 regardless, so Phase 5 adds
no model or signature churn — its cascade skips are recorded by this machinery for
free.

**Changelog + `.github/HISTORY.md`** entry lands with this phase (1–3 lines in
CHANGELOG pointing at this ADR; full narrative in HISTORY).

**Phases 1–4 are a complete, coherent feature**: gating plus a full audit trail.
Everything after this point is additive.

### Phase 5 — `depends_on` runtime semantics (largest, highest risk)

Gives `depends_on` execution meaning for the first time. Worth re-confirming it is
still wanted once Phase 4 has shipped, since gating is complete without it.

| Change                                | Detail                                                                                               |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| stable topological order (Design §4)  | Kahn, ready nodes in declaration order                                                               |
| cycle + dangling-reference validation | new `DeploymentService._validate_stage_depends_on()`, sibling to `_validate_helm_stage_namespaces()` |
| transitive skip propagation           | Design §3 step 2                                                                                     |

**Exit criteria:** the stability test (already-ordered file → identical order) is
the gate for this phase. Additionally, assert a `dependency_skipped` entry reaches
the manifest — Phase 4's recording should cover it with no new code, and a test is
what proves that rather than assumes it.

**Risk:** this is the only phase that can silently alter existing deployments'
behaviour; if stability cannot be demonstrated, stop and reconsider rather than
shipping. Sequencing it here means that risk lands on top of a fully tested
gating-plus-recording baseline, so any ordering regression is diffed against a
known-good state.

### Phase 6 — Polish (independent, any order)

- **Regression tests for non-gated surfaces (D11)** — `destroy`, `build run`,
  `build plan`, drift each still see a disabled stage. These guard the paths most
  likely to acquire gating by accident now the shared helper exists; without them
  the D3 orphan scenario regresses invisibly. *Arguably belongs in Phase 3 — move
  it there if the reviewer prefers.*
- **`build plan` disabled marker (D11)** — label a stage `enabled` would gate, so a
  preview distinguishes "disabled, would be skipped" from "deleted".
- **Synonym hint (D9)** — `condition`/`when`/`if` → `enabled` map ahead of the
  `difflib` pass in `_generate_fix_suggestions()`.

### Phase 7 — Cross-schema cleanup (D8) — separate change set

Per the inventory table's disposition column. Independent of Phases 1–6 and of
each other. Reuses `parse_bool()` from Phase 1 but is otherwise unrelated to
stage gating — **do not bundle these into the gating PR.**

- **Remove `condition`** (rows 11–12) from `WorkspaceResourceModel` and
  `EnvironmentResourceOverrideModel`, plus its phantom
  `'{{ environment }} == production'` example in the `sln init` scaffold
  ([workspace.yaml#L92](../../src/strata/templates/solution/dot.strata/templates/workspace.yaml#L92)).
  Deprecation shim in the `model_validator(mode="before")` that already handles
  ADR-0078's `references`, so an upgrade warns rather than hard-fails.
- **Implement `resources[].enabled`** (rows 9–10) — filter resources in the
  builders. Nothing can depend on current behaviour, so no changelog risk.
- **Implement `modules[].enabled`** (rows 7–8) — make it filter builds, not only
  the `main`-slot validation rule. **Needs a changelog entry**: unlike the rows
  above, this changes behaviour for any workspace already setting
  `enabled: false` on a module.
- **Leave rows 13–14 alone** (`highlight[].condition`, `gates[].when`) — different
  concepts, explicitly out of the convention.

### Phase 8 — `StageSelectionMixin`: one answer to "which stages?" (D7 follow-up)

Discovered during implementation, not in the original plan. Seven commands
hand-roll the `--stage` filter (see D7's follow-up table). **Designed, not yet
implemented.**

#### Layering fixes where the algorithm lives

ADR-0003's table is binding: `commands → controllers → services → … → utils`,
one-way, with `utils/` holding *pure functions*.

This **forbids** moving the algorithm anywhere higher:
`DeploymentService._validate_stage_depends_on()` calls
`validate_stage_dependencies`, and a service importing a controller would be an
upward dependency. **No logic moves** — every pure function stays in
`utils/stage_selection.py`; Phase 8 adds only command-side plumbing.

#### `StageSelectionMode` replaces the boolean pair

`select_stages`' `apply_gating`/`apply_ordering` booleans admit four
combinations, only two of which are meaningful, and a wrong pairing fails
silently. Replaced by an enum **in `utils/`, on `select_stages` itself** — at the
lowest level, so invalid states are unrepresentable for every caller including
tests, not only for callers who go through the shared helper:

| Mode      | Gating | Ordering    | Why                                                                                           |
| --------- | ------ | ----------- | --------------------------------------------------------------------------------------------- |
| `DEPLOY`  | yes    | dependency  | `deploy run` — the feature.                                                                   |
| `DESTROY` | no     | declaration | D3: gating strands infrastructure. Declaration order because *reverse* ordering is undecided. |
| `INSPECT` | no     | declaration | D11: read-only surfaces never gate and never reorder.                                         |

`DESTROY` and `INSPECT` behave identically today but exist separately because
their *reasons* differ. When destroy eventually gains reverse ordering, that is
one line in the helper rather than an audit of seven call sites.

**`INSPECT` is load-bearing for a Phase 6 feature, not merely descriptive.**
`build plan`'s `would_skip` marker is computed in `_plan_stage`, which only runs
for stages the selection returns. Passing `DEPLOY` there would filter disabled
stages out first and silently turn the marker into dead code. The Phase 6 guards
in `test_commands_gating_not_applied.py` catch exactly this.

#### One mixin, no controller

```python
# src/strata/commands/stage_mixin.py

class StageSelectionMixin:
    """Shared stage resolution for commands that act on deployment stages.

    Collapses four blocks duplicated across seven commands: the null-service
    guard, the spec.stages extraction, the --stage/--scope filter, and the
    error routing.

    Opt-in, not automatic: eleven commands set ``_stage`` but only seven filter
    by it this way, so inheriting this mixin does not change a command's
    behaviour until it actually calls ``_resolve_stages``.
    """

    # Declared, not read via getattr: a command that renamed these would
    # otherwise silently select every stage instead of failing.
    #
    # The split is deliberate. `_stage`/`_scope` default to None because they are
    # genuinely optional — most commands have no --scope at all. `_deployment_service`
    # and `_errors` are annotation-only, so a subclass that fails to set them raises
    # AttributeError: that is a programming error and should fail loudly rather than
    # be masked as a user-facing "not loaded" message.
    _stage: Optional[str] = None
    _scope: Optional[str] = None
    _deployment_service: Optional[DeploymentService]
    _errors: List[str]

    def _resolve_stages(
        self,
        mode: StageSelectionMode,
        *,
        resolved: Optional[ResolvedValues] = None,
    ) -> Optional[StageSelection]:
        """Return the selection, or None when the caller should abort."""
        model = self._deployment_service.model if self._deployment_service else None
        if model is None:
            self._errors.append("Deployment service not loaded")
            return None

        selection, errors = select_stages(
            model.spec.stages or [],
            stage=self._stage,
            scope=self._scope,
            resolved=resolved,
            mode=mode,
        )
        if errors:
            self._errors.extend(errors)
            return None
        return selection
```

Inherited by **both** `BaseDeployCommand` and `BaseBuildCommand` — both already
declare `_deployment_service` and `_errors` — so all seven sites collapse to:

```python
selection = self._resolve_stages(StageSelectionMode.INSPECT)
if selection is None:
    return False
stages = selection.to_run
```

A mixin rather than a method on `BaseCommand`: stage selection is not a concern
of every command, and `BaseCommand` should not learn about deployment stages.

**The mixin must absorb the null-service guard, not just the filter.** That guard
is a *separate* duplication at the same seven sites — and `status_deploy_command`
carries a second variant of it (`"Deployment model not loaded"`). Without it the
helper would save one line per site and would not be worth having.

#### Considered and rejected: a `StageController`

A `StageController(BaseController)` was designed first, on the reasoning that
"orchestrating services for one operation" is a controller's job. Rejected on
review:

- **Its body would be three lines of delegation.** The mixin has to exist anyway
  to get two-line call sites, so the controller would add a layer without adding
  behaviour — two new abstractions for one operation.
- **The testability argument was false.** It was justified partly by Phase 6's
  pain (`__new__` plus reverse-engineering `_output_quiet`/`_output_verbose` to
  reach `_run_drift_detection`). But a mixin is testable with a five-line stub
  class — *easier* than a controller test, not harder.
- **The layering objection was theoretical.** `BaseDeployCommand` already hosts
  `_create_deployer`, `_record_stage_result` and `_write_deployment_manifest`,
  all of which orchestrate services. A `_resolve_stages` helper is consistent
  with that; the controller would have been the outlier.

Recorded here so it is not re-proposed: the deciding factor is that the
*algorithm* already lives in `utils/`, leaving only plumbing, and plumbing
belongs with the commands that need it.

#### Scope

Seven sites, with an explicitly excluded eighth (below).

- Migrate all seven sites; **unify the message** on the richer
  `"…not found in deployment definition. Available: …"` wording. Five commands
  change error text, and `status` additionally changes its guard text →
  changelog entry, as in Phase 1.
- `build plan` and drift must be passed `INSPECT` (see above).
- **`--scope` is not added** to the five read-only commands. Nearly free once
  shared, but new feature surface does not belong in a consolidation change.
- `DeploymentService._validate_stage_depends_on()` **keeps calling the util
  directly** — the trap most likely to be walked into later, hence stating it.
- `status_deploy_command` still re-reads `self._deployment_service.model` after a
  successful resolve, for `meta.name` in its header. The *guard* collapses; the
  model access does not.
- **Churn to budget for:** replacing the boolean pair with `mode=` touches the
  test call sites that pass those flags in `test_utils_stage_selection.py`.
  Mechanical, and smaller than it first appears — only three of that module's
  tests pass the flags explicitly; the rest rely on the default.

#### Explicitly out of scope: `output_deploy_command`

[`output_deploy_command.py`](../../src/strata/commands/deploy/output_deploy_command.py#L124)
is an eighth site that **must not** be migrated. It filters by stage *type*
before applying `--stage`:

```python
terraform_stages = [s for s in all_stages if self._is_terraform_stage(s)]
if self._stage:
    terraform_stages = [s for s in terraform_stages if s.name == self._stage]
    if not terraform_stages:
        self._errors.append(
            f"Stage '{self._stage}' not found or is not a terraform stage. …"
        )
```

Its error covers a case the shared helper has no concept of — *"exists, but is
the wrong kind"*. Routed through `_resolve_stages`, a named non-terraform stage
would be returned happily, the type filter would then empty the list, and the
command would fall through reporting **nothing at all**. Left alone deliberately;
recorded here so a later tidy-up does not "finish the job" and silently remove
that error.

The seven-site inventory is verified complete: a search for `name == self._stage`
returns exactly the six inline sites plus `output`, matching D7's table.

Independent of Phase 7 and of the gating work; safe to defer indefinitely, since
no command is mis-gated today — this is duplication, not a defect.

### Adjacent finding — `deploy show` previews a deployment without disclosing gating

Surfaced while inventorying the stage-filter copies. **Out of scope for this
ADR**, recorded because it is a gap this decision creates rather than one it
found.

`deploy show` is the *preview* surface: it renders the resolved deployment so an
operator can see what a run would do. After this ADR, that preview is
incomplete — it lists every stage with no indication that some of them will not
run.

The gap is unusually cheap to close, because the command already has both halves
and simply does not join them:

- it **already resolves values**, including feature flags
  ([`show_deploy_command.py#L185`](../../src/strata/commands/deploy/show_deploy_command.py#L185)),
  and displays them in its own section;
- it **already lists stages**
  ([`#L132`](../../src/strata/commands/deploy/show_deploy_command.py#L132)),
  emitting `name`/`provisioner`/`scope`/`depends_on` per row.

So today a user can read `enable_dispatcher_api: false` in one section and
`dispatcher_api` in another, and is left to join them by hand — exactly the
"deliberately not deployed vs. absent" confusion this ADR exists to remove, just
relocated from the audit trail to the preview.

The pattern to follow already exists: Phase 6 added `would_skip`/`skip_reason` to
`build plan`'s per-stage result for the same reason. `deploy show` would add the
same two fields to its stage rows.

#### This sharpens D11

D11 says gating suppresses *making changes*, never *observing state*. Correct,
but incomplete as written: "not gated" is not the same as "ignores `enabled`".
The fuller rule the `build plan` marker actually embodies is:

> A read-only surface must never **filter** on `enabled` — and a human-facing one
> should always **disclose** it.

`build plan` follows both halves. `deploy show` follows only the first. Worth
stating explicitly whenever a new stage-listing surface is added, so the second
half is not forgotten again.

#### Separately: `--stage` looks inert here

[`show_deploy_command.py#L54`](../../src/strata/commands/deploy/show_deploy_command.py#L54)
stores `self._stage`, but the stage-list code iterates *every* stage and never
reads the attribute back — written and never used, so `deploy show --stage X`
appears to accept `X` and ignore it. A different question from the disclosure gap
above (what is `--stage` *meant* to do here — filter the listing, or scope some
other section?), and worth its own issue rather than a guess.

### Decision points during implementation

Two places where the plan should stop and re-confirm rather than proceed on
momentum:

1. **After Phase 4** — is Phase 5 (`depends_on`) still wanted? Gating and its audit
   trail are complete without it, and Phase 5 carries the only breaking-change
   risk in this ADR.
2. **During Phase 5** — if stable ordering cannot be demonstrated against existing
   deployment files, stop. Reordering stages silently is worse than not having
   cascade.

