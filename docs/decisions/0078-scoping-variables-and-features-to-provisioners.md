# Scoping Variables and Features to Provisioners — Allow- vs Deny-by-Default

- Status: proposed
- Date: 2026-09-11
- Related: [ADR 0075 — Unified Terraform/Helm value-expression syntax](./0075-unify-terraform-helm-value-expression-syntax.md), [ADR 0077 — Repo map & cross-repo path resolution](./0077-repo-map-cross-repo-path-resolution.md)

## Context and Problem Statement

A deployment's environment YAML declares three kinds of input — `spec.variables`,
`spec.features`, `spec.secrets`. A deployment may run several provisioners
(Terraform, Ansible, Helm, Compose, script). **Not every input is meant for every
provisioner.**

Strata handles one of these three correctly and two of them not at all:

| Input type    | Scoped per stage at deploy time?       | Cross-checked against Terraform's `variables.tf` at build time? |
| ------------- | -------------------------------------- | --------------------------------------------------------------- |
| **Secrets**   | ✅ via `stage.secrets` allowlist        | Only for stages that resolve to *this* provisioner              |
| **Variables** | ❌ injected into every stage unfiltered | **Always**                                                      |
| **Features**  | ❌ injected into every stage unfiltered | **Always**                                                      |

This is stated explicitly in
[`TerraformBuilder._collect_declared_input_keys()`](../../src/strata/builders/terraform_builder.py#L1553):

> *"Variables and features are never stage-scoped at deploy time (they're injected
> into every stage unfiltered — see `ResolvedValues.for_stage()`), so they are
> collected unconditionally here too. Secrets ARE stage-scoped at deploy time via
> each stage's `secrets:` allowlist."*

And [`ResolvedValues.for_stage()`](../../src/strata/utils/resolved_values.py#L64)
confirms it — `allowed_secrets` filters `secrets` and `stage_outputs_sensitive`
only; `variables`, `features` and `stage_outputs` are copied through in every
branch.

### The triggering case

A workspace declares a variable that **Terraform never reads** — CI/bookkeeping
metadata, a service-connection identifier, a value consumed by a `script`
provisioner in a later stage.

Because variables are collected unconditionally,
`TerraformBuilder._validate_inputs()` cross-checks that key against the Terraform
root module's `variables.tf`, finds it undeclared, and **fails the build**.

The workaround in the field is to declare a dummy variable in every Terraform
root:

```hcl
# strata/CI bookkeeping — Terraform never reads this
variable "sc_override" {
  type    = string
  default = ""
}
```

There is already precedent for this in real workspaces — `enable_platform`,
`enable_aks`, `enable_udr_subnet_association`, `privatelink_zones` and
`layer_name` are all declared-but-unused for exactly this reason. Five dummy
declarations in one root, each existing solely to satisfy a validator.

### Why the dummy declaration is the wrong fix *specifically when another provisioner uses the value*

If the value were genuinely unused by anything, the dummy declaration would be
merely ugly. It isn't. When a `script` (or Ansible, or Helm) provisioner actually
consumes the variable, the dummy declaration is **false documentation**:

- Its comment says *"Terraform never reads this."* The truth is *"another
  provisioner's input."*
- A future reader doing dead-code cleanup deletes it and breaks the script
  provisioner, with nothing in Terraform to signal the dependency.
- The real consumer has no declared relationship to the value anywhere.
- It must be repeated in **every** Terraform root sharing that environment file —
  because the cross-check runs per-provisioner but the *collection* is
  unconditional.

### The validator is not the bug

`_validate_inputs()` faithfully mirrors deploy-time reality: variables really
*are* injected into every stage, so Terraform really does receive the value. The
coarseness is in the **injection and emission model**, not the check. Relaxing the
validator alone would make the build lie about what deploy does.

### Which paths are actually involved

The two provisioner types receive values by different routes, and only one of them
trips the check:

| Provisioner      | How it receives a variable                              | When      |
| ---------------- | ------------------------------------------------------- | --------- |
| Terraform        | `variables.auto.tfvars.json` (+ `TF_VAR_*` for secrets) | **build** |
| script / compose | process env via `ResolvedValues.as_compose_env()`       | deploy    |

So routing a variable away from Terraform means *not writing it into
`variables.auto.tfvars.json`* and excluding it from the cross-check. The script
path is untouched. **This is a narrower change than "overhaul stage scoping"** —
it is primarily build-time emission plus validation.

## Decision Drivers

The question posed is specifically about **usability and predictability for a
DevOps profile**. Concretely, that means four things:

1. **Predictability** — can an engineer determine what will happen without
   running it?
2. **Ceremony** — how many files must change for a routine edit (adding a
   variable)?
3. **Failure mode** — when it is got wrong, is the failure *loud and early*
   (build/validate) or *silent and late* (apply, or worse, a wrong-but-successful
   apply)?
4. **Migration cost** — what happens to existing workspaces on upgrade?

Criterion 3 carries the most weight here. The recent incidents in this codebase —
`PlanBuildCommand` silently omitting `repo_map` (ADR-0077, Problem B), and
`get_repo_map()` silently resolving against `os.getcwd()` — were expensive
precisely because they failed *silently* and were diagnosed hours later against a
misleading error. A design that reintroduces a silent-wrong-value path is
disqualifying regardless of how elegant it looks.

## Considered Options

### Option A — Dummy declaration in each Terraform root (status quo workaround)

Declare the variable in `variables.tf` with `default = ""` and a comment.

- **Ceremony:** one declaration per Terraform root that shares the environment file.
- **Predictability:** poor — the declaration lies about the variable's purpose.
- **Failure mode:** silent. Deleting the dummy as dead code breaks a different
  provisioner with no Terraform-side signal.
- **Migration:** none (it is what happens today).

Rejected as a durable answer, though it remains the only thing available until
this ADR is implemented.

### Option B — Deny-by-default: every stage enumerates its variables

Mirror `stage.secrets` exactly: `stage.variables: [...]`, omitted ⇒ no variables.

- **Ceremony:** severe. A workspace with 40 variables across 5 stages needs five
  lists totalling up to 200 entries, maintained by hand.
- **Predictability:** excellent in principle, poor in practice — nobody reads or
  reliably maintains a 40-entry allowlist, so lists drift toward `['*']` and the
  mechanism becomes decorative.
- **Failure mode:** moderately loud — a missing variable surfaces as Terraform
  "required variable not set" (a build warning today, an apply error otherwise).
- **Migration:** **breaking for every existing workspace.** Every stage must gain
  a list or `['*']` before it can deploy at all.

### Option C — Allow-by-default, stage narrows (`stage.variables: [...]`, omitted ⇒ all)

Keep today's behaviour, let a stage opt into a narrower list.

This looks like the natural compromise and is the one that most resembles
`stage.secrets`. **It is the worst option on criterion 3**, and that is decisive:

- To exclude **one** variable from Terraform, the Terraform stage must enumerate
  **all the others**. Narrowing is all-or-nothing.
- Therefore: add variable #41 later, forget to add it to that list, and it is
  **silently not injected**. Terraform falls back to a `default` or fails much
  later. There is no build-time signal, because "not in the allowlist" is
  indistinguishable from "deliberately excluded."
- The mechanism's cost scales with the number of variables you *keep*, while the
  benefit scales with the number you *exclude* — exactly backwards.

### Option D — Allow-by-default, stage excludes (`stage.exclude_variables: [...]`)

Targeted, avoids C's enumeration problem. Rejected on general grounds: exclusion
lists compose badly (two overlapping exclusions are ambiguous), invert the mental
model relative to `stage.secrets`, and answer "not Terraform" without ever saying
"yes, the script" — so the real consumer still has no declared relationship to the
value.

### Option E — Allow-by-default, the *variable* declares its routing (chosen)

Routing lives on the declaration, not on the consumer:

```yaml
spec:
  variables:
    - key: sc_override
      store: constant
      value: "sc-z01-s01"
      stages: [configure]        # ← only this stage receives it
      description: "Service connection used by the configure script"
```

Omitting `stages:` preserves today's behaviour exactly: the value goes everywhere.

- **Ceremony:** one line, on the object being added, at the moment it is added.
  No stage edits. No enumeration of unrelated variables.
- **Predictability:** the environment file becomes the single place that answers
  "where does this value go?" A stage's effective inputs are not locally readable
  from the stage — mitigated by making `strata values list --stage <name>` report
  them (it already accepts `--stage`).
- **Failure mode:** **loud, at build.** Forgetting to route leaves the current
  behaviour — the value goes everywhere — and the *existing* `variables.tf`
  cross-check flags it if undeclared. Forgetting produces today's error, not a new
  silent path. This is the property Options C and D lack.
- **Migration:** none. Absent `stages:` ⇒ current semantics.

## Decision Outcome

**Adopt Option E — allow-by-default, with optional per-variable routing.**

### On allow- vs deny-by-default

Deny-by-default is the right instinct for secrets and the wrong one here, and the
reason is that **`stage.secrets` and variable routing are not the same mechanism
wearing different defaults. They solve different problems:**

|                      | `stage.secrets`                                             | variable routing                                   |
| -------------------- | ----------------------------------------------------------- | -------------------------------------------------- |
| Purpose              | **Access control**                                          | **Dependency declaration**                         |
| The stage is…        | a trust boundary                                            | a consumer                                         |
| Cost of over-sharing | a leaked credential                                         | a validator false-positive                         |
| Correct default      | **deny** — least privilege                                  | **allow** — least ceremony                         |
| Belongs on           | the **stage** (read a stage, see exactly what it can touch) | the **variable** (read a value, see where it goes) |

An earlier draft of this analysis flagged the differing defaults as an
inconsistency to apologise for. On examination that was wrong: **the difference is
justified by purpose, and forcing symmetry would be the actual mistake.** Secrets
deny-by-default because the blast radius of over-sharing is a credential leak.
Variables allow-by-default because the blast radius is a build-time warning, and
because the ceremony of deny-by-default (Option B) is what drives teams to
`['*']`, which destroys the mechanism's value for secrets too by normalising the
escape hatch.

For a DevOps profile specifically: deny-by-default only produces predictability if
the allowlists are actually maintained. At 40+ variables across multiple stages
they will not be, and a decorative allowlist is worse than none — it looks like a
guarantee while providing none.

### On failure modes

The deciding property is that Option E's failure mode is *today's behaviour plus
today's error*. Every other option introduces a new way for a value to silently
not arrive. Given that this codebase has just spent significant effort removing
two silent-fallback bugs (ADR-0077), adding a third would be a poor trade for
syntactic symmetry with `stage.secrets`.

### Scope

Routing applies to `spec.variables` and `spec.features` (which have the identical
problem and the identical fix). `spec.secrets` is unchanged — `stage.secrets`
already handles it correctly and is an access-control mechanism that should stay
on the stage.

## Remaining Work

| Item | Description                                                                                                                                                                                               | Status |
| ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ |
| S-1  | Add optional `stages: Optional[List[str]]` to the variable and feature store models; absent ⇒ all stages (current behaviour)                                                                              | 🔲 TODO |
| S-2  | Validate declared stage names against `deployment.spec.stages[].name`; an unknown name is a **hard error** listing known stages — never a silently-ignored no-op                                          | 🔲 TODO |
| S-3  | `TerraformBuilder._collect_declared_input_keys()` honours routing when scoping keys to a provisioner's matching stages (reuse the existing `_stages_for_provisioner()`)                                   | 🔲 TODO |
| S-4  | Build-time emission: a variable routed away from a Terraform provisioner is not written into that provisioner's `variables.auto.tfvars.json`                                                              | 🔲 TODO |
| S-5  | Deploy-time: extend `ResolvedValues.for_stage()` to filter `variables`/`features` by routing, so build and deploy agree                                                                                   | 🔲 TODO |
| S-6  | `strata values list --stage <name>` reports the effective per-stage input set, restoring the stage-local readability Option E trades away                                                                 | 🔲 TODO |
| S-7  | Document the deliberate asymmetry (secrets deny-by-default on the stage; variables allow-by-default on the variable) in `docs/config/environment.md` — so it reads as a decision rather than an oversight | 🔲 TODO |
| S-8  | Regression test: a variable routed to a script stage does not trip the Terraform `variables.tf` cross-check, and *is* still injected into the script stage's env                                          | 🔲 TODO |

## Open Questions

1. **Route by stage name or by provisioner name?** `stages:` matches the
   `stage.secrets` model and stages are the user-facing unit in the deployment
   file. But the cross-check is per-*provisioner*, and one provisioner may serve
   several stages. `stages:` + the existing `_stages_for_provisioner()` mapping is
   the proposal; a `provisioners:` form would be more direct but introduces a
   second addressing scheme.
2. **Should routing support negation** (`stages: ["!terraform"]`)? It would make
   "everywhere except one" a one-liner, which is the actual shape of the
   triggering case. Against: it is a second syntax for the same idea, and
   ADR-0073 (embedded-string-syntax creep) argues for resisting exactly this.
3. **Does `features` need routing at all**, or only `variables`? Features are
   booleans typically consumed by Terraform `count`/`for_each`. The mechanism is
   free once variables have it, but if no real case exists it may be surface area
   with no demand.
4. **Should the existing dummy declarations be cleaned up** once routing exists?
   `enable_platform` and friends are genuinely unused — different problem, and
   possibly they should simply be deleted rather than routed.
