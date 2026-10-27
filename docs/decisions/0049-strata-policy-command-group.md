# A general `strata policy` command group backed by a dispatch table

- Status: accepted
- Date: 2026-10-27
- Related: [ADR-0047](0047-deploy-security-gates-as-vendor-neutral-capabilities.md),
  [docs/work/security-gates-design.md](../work/security-gates-design.md) D7 (command table, exit codes),
  [docs/work/policy-engine-architecture.md](../work/policy-engine-architecture.md) ("bespoke, no generic engine"),
  [docs/work/security-gates-implementation-plan.md](../work/security-gates-implementation-plan.md) Phases 8-10

## Context and Problem Statement

The deploy-time gates of ADR-0047 need commands a pipeline can run on their own: evaluate the gates before
deploying, and list an image's open issues before adopting the gate. The first draft named them
`strata security gate | findings`. But strata already has a shared concept for these: four workspace fields
(`cve_policy`, `checkov_policy`, `release_gate_policy`, `image_gate_policy`), one lifecycle (configured,
evaluated, enforced `deny`/`warn`, recorded in `policy_results.json`), and an audit model whose policy phase is
`validate | build | plan | deploy`. A future OPA policy (tags, naming, regions, cost) would not be "security". The
repo also decided against a generic policy engine (policy-engine-architecture.md).

## Considered Options

- **`strata security ...`** - accurate for the gates, wrong for an OPA policy, and a second home for CVE/Checkov.
- **`strata gate`, `strata compliance`, `strata check`** - "gate" reads deploy-only, "compliance" reads like
  reporting, and `check` collides with `deploy check` and `integration check`.
- **`strata policy list | check | findings`**, with a small dispatch table in the controller: one row per
  workspace policy field (phase, what backs it, its check function, whether it has findings). A new policy adds
  its field, its evaluator and one row.

## Decision Outcome

Chosen: **`strata policy`**, because "policy" is already strata's word for these fields and their audit record, and
the table keeps it within the "bespoke, no engine" decision: it routes to each policy's own evaluator rather than
interpreting a generic policy model. The scope starts small - `policy check` evaluates the two gates and
`policy findings` lists the image gate's issues - while `policy list` shows every configured policy with its last
result; `cve_policy`, `checkov_policy` and a future `opa_policy` join one row at a time. A gate that cannot be
confirmed under `on_missing_data: block` exits 3 in `policy check` exactly as it fails a deploy.
`strata integration check` (credentials and reachability of one integration) is a separate group, since it applies
to any integration, not to policies.

### Consequences

- Good: one place to list, run and inspect every workspace policy, independent of which tool backs it.
- Good: adding a policy is additive (one row), never a new command.
- Bad: until their rows gain check functions, `cve_policy` and `checkov_policy` are listed but only evaluated by
  `build run`; `policy check --policy cve_policy` is a usage error pointing there.
