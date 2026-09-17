# Declarative Stage Gating for Deployment Stages

- Status: proposed
- Date: 2026-09-17
- Related: [ADR-0075](0075-unify-terraform-helm-value-expression-syntax.md) (`${var:}`/`${secret:}`/`${feature:}` resolver), [ADR-0064](0064-deployment-metrics-record.md) (deployment record / audit trail), [ADR-0066](0066-audit-event-routing-policy-model.md) (manifest.recorded), [ADR-0073](0073-embedded-string-syntax-inventory-and-creep-prevention.md) (embedded-string syntax inventory, `ExpressionModel`), [ADR-0034](0034-diagram-visualization-in-vscode-extension.md) (closed-grammar-over-raw-Jinja precedent)

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
  (ADR-0073) already defines a `kind="jinja"` variant — boolean/comparison
  expression evaluation via Jinja2's `Environment.compile_expression()` — but per
  its own module docstring it is "defined for completeness," **not wired to any
  real call site today**. So a GitHub-Actions-style raw expression (`enabled:
  "{{ features.enable_dispatcher_api }}"`) would be a genuinely new pattern for
  an authored condition field in this codebase, going against the one existing
  decision on this exact question — whereas `${feature:KEY}` (ADR-0075) is
  already a closed, single-purpose, fully-implemented token that fits the
  original proposal's own example (`enabled: ${feature:enable_dispatcher_api}`)
  with zero new grammar and zero new code.

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
6. **Expression syntax — how open should the condition be?** Three shapes were
   surfaced when comparing this to how other tools (GitHub Actions `if:`) and how
   strata itself (`ExpressionModel`, diagram highlight conditions) solve the same
   "should this run?" question:
   - **`${feature:KEY}` / `${var:KEY}` tokens (ADR-0075)** — already fully
     implemented, closed (single reference, resolves to a string compared
     truthy/falsy), matches the original proposal's own example verbatim.
     Cannot express compound conditions (`feature A AND environment == prod`).
   - **A closed `<field> <op> <value>` grammar**, matching
     `diagram_expressions.py`'s existing precedent — more expressive than a bare
     token (supports `==`/`!=`/`in`) while keeping the "typo → validation error,
     not a silent false" property that motivated that precedent in the first
     place. Would be new code, but reuses an established *pattern*, not a new
     one invented from scratch.
   - **Raw Jinja expression** (`ExpressionModel(kind="jinja")`, or GitHub
     Actions-style `${{ ... }}`) — most expressive, but goes against strata's
     own stated precedent of avoiding raw Jinja for authored conditions
     (silent-false-on-typo risk), and `kind="jinja"` has no real call site to
     copy from today despite existing in the model.
   No option is chosen here — the original proposal's own example only ever
   needed the first (simplest) shape, so the question is whether compound
   conditions are a real, current need or a hypothetical one worth deferring.

## Considered Options

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

Not yet decided. This ADR intentionally stops at the problem statement, verified
facts, and open questions above per explicit request to separate problem-framing
from solutioning. A follow-up revision of this ADR (or a superseding one) should
record the chosen field shape, skip/manifest/deploy-log schema changes, and the
`depends_on` scope decision once Open Questions 1–5 are answered.

### Consequences

- Good: no design work is thrown away — the verified facts above (feature
  resolution already works, `ResolvedValues` timing, the manifest/deploy-log skip
  gap, the `depends_on` non-enforcement gap) are exactly the inputs the eventual
  decision needs, and won't need re-discovering.
- Bad: nothing is implemented yet; the audit-trail gap described in the Problem
  Statement remains open until a follow-up decision is made.

## Remaining Work

- Answer Open Questions 1–5 above.
- Decide `depends_on` scope (Open Question 3) — this is the single biggest scope
  multiplier for implementation effort and should be settled before estimating.
- Once answered, record the Decision Outcome (field shape, gating location,
  manifest/deploy-log schema changes) and implement:
  - Gating field on `DeploymentStageModel` (or wherever decided).
  - Skip path through `_record_stage_result()` / manifest / deploy-log, including
    the `DeployLogStageModel` tri-state gap.
  - CLI precedence behavior and any warning on forced-disabled-stage execution.
  - Docs: `docs/config/deployment.md` — new section analogous to the existing
    `namespace` vs `helm_namespaces` comparison.
