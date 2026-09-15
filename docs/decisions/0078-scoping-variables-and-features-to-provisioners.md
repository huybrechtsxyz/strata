# Scoping Variables and Features to Provisioners — Allow- vs Deny-by-Default

- Status: **accepted** — Option F selected
- Date: 2026-09-11
- Revised: 2026-09-14 — Option E's addressing scheme found unworkable against shared/layered environment files; Option F (`spec.references`) added; decision withdrawn pending evaluation
- Revised: 2026-09-14 — Gap 2 (inert `references` field) resolved and **implemented** as a removal.
- Revised: 2026-09-15 — Option F reviewed against the code and reshaped. The target is **injection scoping** (the ADR's title), with the build-time cross-check derived from the injected set rather than designed separately. The blocking objection — that `kind: resource`/`kind: provider` have no in-document usage site, so `references` could not be verified complete — is resolved: **`variables.tf` is the usage site**, and Terraform's own required-variable declarations catch under-declaring. Sub-options F-a…F-d withdrawn as unnecessary. **Option F selected; status moved to accepted.**
- Revised: 2026-09-15 — Refined the "optional variable with a default" warning (rule 3) into two cases, so a warning fires only when the environment actually supplied a now-excluded value, not merely because a default exists. Added a section on extending injection scoping and the build check to non-Terraform provisioners.
- Related: [ADR 0073 — Embedded string syntax inventory and creep prevention](./0073-embedded-string-syntax-inventory-and-creep-prevention.md), [ADR 0075 — Unified Terraform/Helm value-expression syntax](./0075-unify-terraform-helm-value-expression-syntax.md), [ADR 0077 — Repo map & cross-repo path resolution](./0077-repo-map-cross-repo-path-resolution.md), [ADR 0068 — Cross-pipeline output publishing](./0068-cross-pipeline-output-publishing.md) (owns resource-to-resource / cross-pipeline output wiring, which this ADR removes inert schema for and explicitly leaves out of scope)

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

### Option E — Allow-by-default, the *variable* declares its routing

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

#### Objection — stages and variables do not live in the same file

Option E's addressing scheme does not survive contact with how environments are
actually composed:

- **Stages live in the deployment file; variables live in environment file(s).**
  A variable declaring `stages: [configure]` names something defined in a
  different document, with a different owner and lifecycle. Nothing about the
  environment file is locally checkable.
- **Environment files are shared across deployments.** A base such as
  `@config/stacks/core/environment.yaml` is included by many deployments, each
  with its own `spec.stages` and *different stage names*. A variable in a shared
  base therefore **cannot name a stage at all** — the name only becomes meaningful
  in files that include it.
- **Environment files layer, last-wins by `key`.** `EnvironmentService.merge_envfiles()`
  replaces whole entries: a leaf file redeclaring a variable to change its `value`
  silently drops any `stages:` the base had set, unless it repeats it.

The merge hazard is milder than it first appears — dropping routing *broadens*
the variable, which reverts to today's behaviour and trips the existing
`variables.tf` cross-check if undeclared. So the common mistake stays loud. But
the shared-base problem is fatal to the addressing scheme, not merely awkward:
**stage names are not stable across the files that would need to reference them.**

Of the three things a variable could name, only one is stable in a shared
environment file:

| Routing target       | Defined in        | Stable across deployments sharing the env file? |
| -------------------- | ----------------- | ----------------------------------------------- |
| Stage name           | `deployment.yaml` | ❌ varies per deployment                         |
| Provisioner name     | `workspace.yaml`  | ❌ varies if the env file spans stacks           |
| Provisioner **type** | universal enum    | ✅ always                                        |

This reframes the problem: the question is no longer primarily *allow vs deny*,
but **what can a shared, layered file stably address?**

### Option F — Scope the check to `spec.references` (existing mechanism)

Strata already has a first-class, widely-declared mechanism for "this document
requires these keys" — and it is the exact inverse of Option E: the **consumer**
declares its needs, rather than the value declaring its destinations.

#### The mechanism already exists

`spec.references` is present on **dns, resource, network, provider, tenant,
module and namespace**, plus `workspace` and `environment`, and is carried
through into the built `platform.json` artifact. The canonical shape, from
[`ResourceReferencesModel`](../../src/strata/models/resource_model.py#L265):

```python
class ResourceReferencesModel(PlatformBaseModel):
    """References to variables, secrets, and features required by this resource.

    Lists the keys that must be defined in the environment configuration.
    Actual values and store backends are defined at environment/workspace level.
    """
    variables: VariableRefs
    secrets:   SecretRefs
    features:  FeatureRefs
```

That docstring *is* the design statement for this option.

#### It is already enforced, and already computed

The DNS model enforces the chain **from the usage side** —
`DnsSpecModel.validate_references_declared()` errors if a record uses `var: X`
without declaring `X` in references:

```yaml
spec:
  references:
    variables: [SERVER_IP]
  zones:
    - records:
        - { name: "@", type: A, var: SERVER_IP }
```

And `references` already feeds `_track_variable()`/`_track_feature()`/
`_track_secret()` in `TerraformBuilder`, producing the `tf_required_*.json`
documentation files **with `used_by` provenance**. So strata already computes
"which component requires which key." It simply does not use that to scope
`_validate_inputs()`, which instead asks "what is declared in the environment" —
i.e. everything.

#### The proposal

Scope **injection** to the keys actually referenced by a provisioner's components,
and derive the build-time cross-check from the same set. A bookkeeping variable
that no resource, module, provider or dns document references is then neither
injected into that provisioner nor cross-checked against its `variables.tf`, and
needs no dummy declaration.

This is the ADR's title restated: variables and features stop being given to
everything, using the mechanism that already exists for saying what a component
needs.

#### Why it dodges every objection raised so far

| Objection                                                    | Option E (`variable.stages`) | Option F (references)                                                |
| ------------------------------------------------------------ | ---------------------------- | -------------------------------------------------------------------- |
| Stages defined in a different file                           | ❌ cross-file name reference  | ✅ references name **keys**, not stages                               |
| Shared env files span deployments with differing stage names | ❌ fatal                      | ✅ keys are already the shared vocabulary                             |
| Env files merge last-wins; routing silently dropped          | ⚠️ mitigated, not solved      | ✅ references live on the consumer doc, which is not layered that way |
| New syntax to learn                                          | ⚠️ new field                  | ✅ zero — existing idiom on 7+ kinds                                  |
| Bookkeeping variable forces a dummy `variable {}`            | fixed                        | ✅ referenced by nothing ⇒ not checked                                |

The reason it dodges the addressing problem entirely: **references name *keys*,
and keys are already the stable shared vocabulary between environment files and
consumer documents.** No stage names, no provisioner names, no new addressing
scheme that can go stale across shared files.

#### The risk that could kill it

`spec.references` is **sparsely populated in practice** — a grep across every
shipped example configuration under `config/` finds it **twice**. Switching the
cross-check to "only referenced keys" wholesale would therefore make it
*near-vacuous* for most real workspaces: it would silently stop catching the
typos it exists to catch. That is the classic "make the check pass by making it
check nothing" trap — and precisely the silent-weakening failure mode this
codebase has recently spent effort eliminating (ADR-0077).

#### The combination that resolves it — opt-in per provisioner, `variables.tf` as ground truth

- A provisioner whose components declare `references` is **scoped**: only those
  keys are injected, and every one of its components must declare — a component
  that does not is an error, not a silent skip.
- A provisioner whose components declare nothing is **unscoped**: current
  behaviour, unchanged.

Opting in makes the check *more* precise rather than weaker, because
`variables.tf` closes the loop in the other direction. Terraform already declares
what it consumes, so:

- a key referenced but absent from `variables.tf` is an **error** (typo);
- a **required** `variables.tf` variable absent from the references union is a
  **warning** — this is what catches under-declaring;
- a key that is neither is correctly ignored.

Crucially, that second check needs no in-document usage site, which is what makes
the option work for `kind: resource` and `kind: provider` — see
[problem 3](#3-resources-and-providers-have-no-usage-sites--resolved-by-using-variablestf).
Sparse `references` adoption therefore stops being fatal: it gates opt-in, and an
opted-in provisioner that under-declares fails loudly at build time.

#### Two gaps to close before this is viable

1. **Script provisioners have no component document.** Resources, modules and dns
   documents can carry references. The triggering case — a value consumed by a
   `script` provisioner — has nowhere to declare them today. The natural home
   would be `references` on the provisioner itself in the workspace file, sitting
   beside the existing `inputs_from`. ⇒ Will need to be designed.
2. **A name collision — which turns out to be dead schema.** Two unrelated things
   are called `references` in the schema:
   - `spec.references` on resource/dns/module ⇒ `{variables, secrets, features}` —
     declares required environment keys. **Live**, consumed by `TerraformBuilder`,
     `ModuleService`, `NetworkService`.
   - `references` on a *workspace resource entry* ⇒ `Dict[str, str]` — allegedly
     cross-resource value wiring
     (`{'storage_connection': 'contoso_storage.connection_string'}`). **Inert.**

   A read of the codebase shows the second is never consumed: the only code that
   touches it merges environment overrides into it
   ([`deployment_service.py`](../../src/strata/services/deployment_service.py#L815))
   and nothing reads the result. It has no validator, appears in no YAML under
   `config/` or `tests/data/`, and its own example cannot resolve — outputs are
   keyed by **provisioner** name in `ResolvedValues.stage_outputs`, and strata has
   no resource-name-keyed output registry at all. The same dead field exists a
   second time as `EnvironmentResourceOverrideModel.references`
   ([`environment_model.py`](../../src/strata/models/environment_model.py#L118)).

   ⇒ This is not a collision to rename around. It is **schema to delete**.

## Design — Option F

*(Design for the two gaps above. Does not itself select Option F — see [Status](#status).)*

### Vocabulary

One word, one meaning, across every kind:

| Term              | Lives on                                                                       | Shape                            | Means                                                       |
| ----------------- | ------------------------------------------------------------------------------ | -------------------------------- | ----------------------------------------------------------- |
| **`references`**  | `spec` of resource/module/dns/provider/… **and** a workspace provisioner entry | `{variables, secrets, features}` | *"I require these environment keys."*                       |
| **`inputs_from`** | a workspace **provisioner** entry                                              | `List[mapping]`                  | *"Wire in another provisioner's outputs."* (ADR-0063 Gap 4) |
| **`outputs`**     | produced by a provisioner run                                                  | `Dict[str, Any]`                 | *"Values I publish downstream."* (ADR-0063 Gap 5, ADR-0068) |

`references` = the **contract** (what a component needs from the environment).
`inputs_from` = the **wiring** (which upstream provisioner supplies it). These are
complementary, never interchangeable.

#### Considered and rejected — unifying `inputs_from` into `inputs`

An earlier draft proposed renaming the dead workspace-resource `references` field
to `inputs`, then absorbing `inputs_from` into it as a second accepted shape, on
the argument that they are one concept at two granularities. That argument does
not survive contact with the code:

| Claim                                                      | Reality                                                                                                                                                                                                                     |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| "Two fields, one concept"                                  | One **mechanism** (`inputs_from`) plus one **inert field** that was never wired up. Unifying merges a live wire into a dead one.                                                                                            |
| "`mapping` flattens to exactly the map form"               | `mapping` is `{upstream_output: downstream_name}`; the proposed map form was `{downstream_name: "producer.output"}` — **opposite direction**.                                                                               |
| "The list form desugars to the map form before validation" | False for `- provisioner: X` with no `mapping`/`select` — that means *all non-sensitive outputs*, a set unknown until the upstream run completes ([`apply_input_mapping`](../../src/strata/utils/resolved_values.py#L333)). |
| "`from:` can name a resource or a provisioner"             | Resource names and provisioner names are validated for uniqueness **only within their own list** — `from: infra` would be ambiguous.                                                                                        |
| "Wired keys must appear in `spec.references`"              | `references` means *keys required from the environment*. A value arriving from an upstream output does not come from the environment.                                                                                       |

Deleting the dead field removes the collision outright — no rename, no union type,
no deprecation cycle on a shipped field. `inputs_from` stays as-is and reads
correctly: *inputs from `infra`*.

Genuine resource-to-resource wiring would require a resource-name-keyed output
registry, which does not exist. That is a larger design, closer to
[ADR-0068](0068-cross-pipeline-output-publishing.md) than to this ADR, and is
explicitly **out of scope here**.

### Gap 1 — `references` on a provisioner

Add an optional `references` field to `WorkspaceIacModel`, reusing the existing
`ResourceReferencesModel` shape verbatim. No new model, no new syntax:

```yaml
spec:
  provisioners:
    - name: bootstrap
      provisioner: script
      source:
        repository: haven
        source_path: scripts
      # NEW — same shape as spec.references on a resource
      references:
        variables:
          - service_connection_id
          - build_pipeline_id
        secrets:
          - deploy_token
        features:
          - enable_preflight_checks
      inputs_from:
        - provisioner: infra
          select: [vnet_id]
```

This gives `script` (and every other provisioner without a component document) the
same declaration surface resources already have, and it sits beside `inputs_from`
so the contract and the wiring are visible together.

### Gap 2 — remove the inert `references` field ✅ implemented

Delete, rather than rename:

| Location                                                                                                  | Field                        | Action                                 |
| --------------------------------------------------------------------------------------------------------- | ---------------------------- | -------------------------------------- |
| `WorkspaceResourceModel` ([workspace_model.py](../../src/strata/models/workspace_model.py))               | `references: Dict[str, str]` | removed                                |
| `EnvironmentResourceOverrideModel` ([environment_model.py](../../src/strata/models/environment_model.py)) | `references: Dict[str, str]` | removed                                |
| `deployment_service.py`                                                                                   | override merge block         | removed                                |
| `.strata/templates/workspace.yaml`, `templates/solution/…`                                                | `references: {}`             | removed, with its explanatory comment  |
| `.strata/schemas/workspace.json`, `environment.json`                                                      | generated                    | regenerated via `strata schema export` |
| `docs/config/workspace.md`                                                                                | field table                  | line removed                           |

**Migration risk: low, but not zero.** Models use `extra="forbid"`, so a
workspace in the wild that *does* carry `references:` on a resource entry — copied
from the shipped template, which emits `references: {}` — would start failing
validation on upgrade. Mitigation, implemented on both models: a
`model_validator(mode="before")` that drops the key with a `DeprecationWarning`
rather than erroring. Unknown keys are still rejected. Remove the shim in the next
minor release.

Nothing is lost by deleting it. The field was never resolved, never validated, and
could not have worked: workspace resources are build-time metadata written into
`terraform.auto.tfvars.json` before any provisioner runs, while outputs are
post-apply and keyed by *provisioner* name, not resource name. Where both
resources share a Terraform root, HCL already wires them natively; across
provisioners `inputs_from` already does it. **The removed schema, the four reasons
it does not work, and the six things a revival would have to build first are
recorded in [ADR-0068](0068-cross-pipeline-output-publishing.md#prior-art--the-resource-level-wiring-schema-removed-by-adr-0078)** — that ADR owns output
publishing and is the right home for the idea if it ever returns. Reviving it is a
full mechanism (output registry, resource-attributed outputs, late binding), not a
field.

After removal, `references` has exactly one meaning schema-wide:
*"keys this component requires from the environment."* That is the property Option F
needs in order to scope the cross-check, and it now holds without qualification.

### Validation rules

Enforcement is **opt-in per provisioner**, all-or-nothing. A provisioner whose
components declare no `references` keeps today's behaviour exactly.

| #   | Rule                                                                                                                      | When it applies      | Severity |
| --- | ------------------------------------------------------------------------------------------------------------------------- | -------------------- | -------- |
| 1   | Every **injected** key must be declared in the provisioner's `variables.tf` (Terraform)                                   | always               | error    |
| 2   | Every **required** (`no default`) variable in `variables.tf` must be in the injected set                                  | always               | warning  |
| 3a  | Optional (has `default`) variable absent from the injected set **and** absent from the whole environment                  | always               | info     |
| 3b  | Optional (has `default`) variable absent from the injected set **but present** in the environment                         | scoped provisioner   | warning  |
| 4   | Under a scoped provisioner, a component that declares no `references` is an error — not a silent skip                     | scoped provisioner   | error    |
| 5   | Every key **used** by a component that *has* an in-document usage site (`var:`/`secret:`) must appear in its `references` | dns, network, module | error    |
| 6   | A key supplied by `inputs_from` is exempt from rules 1–2 — it arrives from an upstream output, not the environment        | always               | —        |

Rules 1–3 are `check_inputs()`'s three existing directions. The change is **what
set(s) they run against**: rules 1–2 use the injected set; rule 3 is split into
3a/3b by also checking the full environment set, so a warning fires only when a
supplied value is actually being dropped — not whenever a module default simply
goes unused, which is the common case and must stay quiet.

Rule 2 is the completeness check. It is what makes under-declaring `references`
fail loudly instead of silently narrowing the check, and it requires no
in-document usage site — `variables.tf` is the ground truth. See
[problem 3](#3-resources-and-providers-have-no-usage-sites--resolved-by-using-variablestf).

Rule 5 is no longer load-bearing; it stays where it already exists as a cheap
extra check on the kinds that can support it.

Rule 6 records existing behaviour: `_collect_declared_input_keys()` already folds
`collect_inputs_from_keys(prov.inputs_from)` into the supplied set
([terraform_builder.py](../../src/strata/builders/terraform_builder.py#L1452)).
Stated explicitly because the contract/wiring split makes the reason non-obvious:
`references` is about the environment, `inputs_from` is not.

`inputs_from` validation is unchanged — `WorkspaceSpecModel.validate_inputs_from()`
already checks producer existence, self-reference and cycles.

### Scoping injection, and deriving the check from it

The primary change is at **deploy time**, in `ResolvedValues.for_stage()`, which
today filters `secrets` by the stage allowlist and copies `variables` and
`features` through unconditionally:

```
for each provisioner P:
    components(P) = resources ∪ modules ∪ dns ∪ providers bound to P
                    ∪ P itself (via its own references)

    if no component of P declares references:
        injected(P) = all environment keys           # unscoped — today's behaviour
    else:
        injected(P) = ⋃ references of every component of P
                      # every component must declare — rule 4
```

The **build-time check follows from the same set** — no separate computation:
`_collect_declared_input_keys()` returns `injected(P)`, and `check_inputs()` runs
its three directions against it. A key that is not injected is not cross-checked,
which is exactly the triggering case.

Opt-in is per provisioner and binary. There is no partial state: within a scoped
provisioner every component declares its needs, so the injected set is complete by
construction, and a component that forgot is an error rather than a silent hole.
Unscoped provisioners are untouched, so no workspace breaks on upgrade.

### Worked example — the triggering case

```yaml
# environment-prd.yaml
spec:
  variables:
    vnet_cidr: "10.0.0.0/16"
    service_connection_id: "sc-prd-001"   # CI bookkeeping — Terraform never reads this
```

```yaml
# config/network.yaml  (kind: resource)
spec:
  references:
    variables: [vnet_cidr]
```

```yaml
# workspace.yaml
spec:
  provisioners:
    - name: infra
      provisioner: terraform
      source: { repository: haven, source_path: terraform }
    - name: bootstrap
      provisioner: script
      source: { repository: haven, source_path: scripts }
      references:
        variables: [service_connection_id]
  resources:
    - name: network
      file: config/network.yaml
```

Result: `infra`'s declared-key set is `{vnet_cidr}` (from `network`'s
`references`), so `service_connection_id` is never cross-checked against
`variables.tf`. **The dummy `variable {}` declaration disappears.** The key is
still validated — as a requirement of the `bootstrap` provisioner.

### Error messages

```
ERROR  [infra] Input 'vnet_cidr' is not declared in variables.tf
       → 'vnet_cidr' is referenced by resource 'network', but terraform/variables.tf
         has no matching 'variable "vnet_cidr"' block

WARN   [infra] Required variable 'location' (no default) is not supplied by any input
       → no component of provisioner 'infra' declares 'location' in spec.references

WARN   [infra] Optional variable 'node_count' not supplied — Terraform will use its
       default (3). 'node_count' has a value in the environment, but no component of
       this scoped provisioner references it, so it was not injected. (rule 3b)

ERROR  [infra] Provisioner is scoped (some components declare spec.references) but
       resource 'app_tier' (config/app.yaml) declares none
       → add spec.references to config/app.yaml, or remove it from the other components
```

### Extending beyond Terraform

Injection scoping and the build-time check are **two different mechanisms with
different reach**, and it matters which one is being extended:

**Injection scoping (`ResolvedValues.for_stage()`) is provisioner-agnostic.**
Every provisioner type — Terraform, Ansible, Helm, Compose, Bicep, script — reads
from the same `ResolvedValues` object. Once `for_stage()` scopes `variables`/
`features` the way it already scopes `secrets`, **every provisioner type is scoped
the moment its components declare `references`** — no per-provisioner-type work is
needed for rollout step 5.

**The build-time check (`check_inputs()`-style, rules 1–3) is Terraform-only
today**, because it depends on `parse_variables_tf()` — a ground truth specific to
Terraform. Whether an equivalent exists elsewhere varies by provisioner:

| Provisioner | Ground truth for "what does it actually consume"                                                           | Parseable today?                                                              |
| ----------- | ---------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Terraform   | `variable` blocks in `variables.tf`                                                                        | ✅ `parse_variables_tf()`                                                      |
| Bicep       | `param` declarations in `.bicep` files — same shape as Terraform (type, optional default)                  | ❌ not implemented; same approach would work                                   |
| Helm        | `values.schema.json`, if the chart ships one (JSON Schema `required`/`properties`)                         | ⚠️ real when present, but optional per chart — no universal guarantee          |
| Compose     | `${VAR}` interpolation directly in the compose YAML — an in-document usage site, same shape as DNS `var:`  | ❌ not implemented; a text scan would suffice, no external ground truth needed |
| Ansible     | none — `defaults/main.yml`/`vars/main.yml` are free-form YAML/Jinja, no typed declaration strata can parse | ❌ no equivalent exists                                                        |
| script      | none — arbitrary script content                                                                            | ❌ no equivalent exists (already acknowledged in Gap 1)                        |

**Consequence for rollout:** step 5 (injection scoping) ships once, for all
provisioner types simultaneously. Step 4 (the build check) ships for Terraform
first, as designed above; Bicep and Compose are natural, low-effort follow-ups
using the same pattern (`parse_bicep_params()`, a `${VAR}` scanner); Helm gets the
check conditionally, only for charts that ship `values.schema.json`; Ansible and
script remain **assertion-only** — `references` documents intent and drives
injection scoping, but nothing verifies it is complete, the same position Gap 1
already accepts for `script`. That gap is real but bounded: it is exactly the
Gap-1 case, not a new one, and injection scoping still narrows what those
provisioners receive even without a build-time completeness check.

### Files affected

| File                                          | Change                                                                                          |
| --------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `utils/resolved_values.py`                    | `for_stage()` — scope `variables`/`features` to the injected set, mirroring `secrets`           |
| `services/workspace_service.py`               | resolve provisioner ↔ component binding; compute `injected(P)`                                  |
| `builders/terraform_builder.py`               | `_collect_declared_input_keys()` returns `injected(P)`; rule 4 error; rule 3a/3b split          |
| `validators/terraform_input_validator.py`     | `check_inputs()` takes both the injected set and the full environment set, to distinguish 3a/3b |
| `models/workspace_model.py`                   | add `WorkspaceIacModel.references` (Gap 1)                                                      |
| `models/resource_model.py`, `module_model.py` | rule 5 usage-side validator where a usage site exists (optional, not load-bearing)              |
| `docs/config/workspace.md`, `resource.md`     | document `references` on provisioners; document the contract/wiring split                       |

Already done (Gap 2): `WorkspaceResourceModel.references` and
`EnvironmentResourceOverrideModel.references` removed, override-merge block
removed, templates and generated schemas updated.

Unchanged: `inputs_from`, `ProvisionerInputMappingModel`,
`apply_input_mapping()`, `validate_inputs_from()`, and every deployer.

### Rollout

1. ✅ **Done.** Remove the inert `references` field (both models + the merge block
   + templates + generated schemas + docs), with the drop-with-warning shim
   (no behaviour change — nothing read it). Regression tests in
   `tests/strata/models/test_models_workspace.py::TestRemovedReferencesField`.
2. Add `references` to `WorkspaceIacModel` — Gap 1 (additive, no behaviour change).
3. Compute `injected(P)` in `workspace_service` and expose it; no consumer yet
   (no behaviour change, testable in isolation).
4. Switch `_collect_declared_input_keys()` to return `injected(P)`, add rule 4,
   and split rule 3 into 3a/3b (`check_inputs()` gains the full-environment-set
   parameter). **First behaviour change — build only.**
5. Scope `ResolvedValues.for_stage()` to `injected(P)` for variables and features.
   **Second behaviour change — deploy.**
6. Optional: rule 5 usage-side validators for resource/module.
7. Next minor: remove the drop-with-warning shim.

Steps 1–3 are no-ops behaviourally and individually reversible. Step 4 changes
what the build reports; step 5 changes what a deploy injects. Both affect **only
provisioners whose components declare `references`** — every existing workspace is
unscoped and unaffected until it opts in.

Splitting 4 and 5 matters: shipping the build check first means a workspace can
opt in, see exactly what would and would not be injected, and fix its `references`
*before* anything changes at deploy time.

Step 1 is deliberately decoupled from the rest: it is correct regardless of
whether Option F is ultimately selected, because the field was inert under every
option.

### Review — problems found, and how they resolve

Three issues surfaced when the design was checked against
`terraform_input_validator.check_inputs()` and the component models. All three are
resolved, and resolving the third changes the shape of the option: **scope
injection, and let `variables.tf` be the ground truth.**

#### 0. The option was conflating two different mechanisms

"Only those are given" and "only those are checked" are not the same change:

|                            | What it changes                                                  | Where                                             |
| -------------------------- | ---------------------------------------------------------------- | ------------------------------------------------- |
| **A — injection scoping**  | stop passing every variable/feature into every stage             | `ResolvedValues.for_stage()`                      |
| **B — validation scoping** | stop cross-checking every environment key against `variables.tf` | `TerraformBuilder._collect_declared_input_keys()` |

This ADR's **title** is A. Its **triggering case** (dummy `variable {}`
declarations) is B. Earlier drafts designed B while describing A.

**A subsumes B.** If a key was never injected into the Terraform provisioner,
there is no reason to cross-check it against that provisioner's `variables.tf`.
Doing A and deriving B from it is one mechanism; doing B alone is a validation
patch that leaves the title's problem unsolved.

**Resolution: the target is A.** `references` declares what a component needs;
the union of a provisioner's components' references is what gets injected; the
build check follows from the injected set.

#### 1. `declared_keys` drives two opposite checks

[`check_inputs()`](../../src/strata/validators/terraform_input_validator.py#L113)
uses one set for two inverse assertions:

| Direction | Assertion                                                      | Catches                                  |
| --------- | -------------------------------------------------------------- | ---------------------------------------- |
| A (error) | every declared key must exist in `variables.tf`                | a key declared that Terraform lacks      |
| B (warn)  | every required `variables.tf` entry must be in `declared_keys` | a required Terraform variable unsupplied |

An earlier draft proposed narrowing the shared set, which would have made
direction B fire for variables that *are* supplied — reintroducing the exact
false-positive class just fixed for `spec.properties`/`spec.custom`.

**Resolution:** under injection scoping the set to use is the **injected** set,
for both directions — and it is then correct for both. A first draft of this fix
said direction B should keep the *full environment* set; that is wrong, and would
mask problem 3's error case. See below.

#### 2. The mixed-adoption fallback was not implementable

The proposed computation read:

```
declared_keys(P) = ⋃ references of components that declare them
                   ∪ all environment keys of components that do not
```

The second line has no meaning — environment keys are not attributable to a
component that declares nothing, so there is no "its keys" to fall back to.

**Resolution:** opt in at the **provisioner**, all-or-nothing. A provisioner is
either scoped (every one of its components must declare `references`; one that
does not is an error, not a silent skip) or unscoped (today's behaviour). Binary
and legible: reading a provisioner tells you whether its inputs are scoped, with
no per-component archaeology.

#### 3. Resources and providers have no usage sites — resolved by using `variables.tf`

The concern: Option F's defence against becoming vacuous was rule 2 — *a key used
must be declared* — which requires the component to have a usage site strata can
parse. Surveying the kinds:

| Kind           | Usage site in the document                                             | Rule 2 possible                                                      |
| -------------- | ---------------------------------------------------------------------- | -------------------------------------------------------------------- |
| `dns`          | `var:` / `secret:` on records                                          | ✅ already enforced (`DnsSpecModel.validate_references_declared`)     |
| `network`      | `var:` / `secret:` on CIDRs                                            | ✅ already enforced (`NetworkSpecModel.validate_references_declared`) |
| `module`       | `services[].environment[].var/secret/feature`                          | ✅ already enforced (`ModuleService`)                                 |
| **`resource`** | **none** — only a free-form `configuration` dict consumed by Terraform | ❌                                                                    |
| **`provider`** | **none** — same                                                        | ❌                                                                    |

`kind: resource` has no `var:` field and no `${var:}` support. Nothing in the
document names a variable key except `references` itself, so nothing in the
document can contradict it. Today that block is documentation only: it feeds
[`_track_resource_requirements()`](../../src/strata/builders/terraform_builder.py#L1890)
→ `tf_required_*.json` with `used_by` provenance, and gates nothing.

**This looked decisive. It is not — the usage site was being sought in the wrong
place.** For a resource, the consumer is the Terraform code, and that code already
declares what it consumes: **`variables.tf` is the usage site.** It is
authoritative, machine-readable, and already parsed by `parse_variables_tf()`.

With injection scoped, both error classes are caught at build time against
`variables.tf` — no in-document usage site required:

| Mistake                                             | Caught by                                                          | Severity today |
| --------------------------------------------------- | ------------------------------------------------------------------ | -------------- |
| Declared a key Terraform does not have (typo)       | direction A — `Input 'X' is not declared in variables.tf`          | error          |
| **Forgot** to declare a key Terraform *requires*    | direction B — `Required variable 'X' (no default) is not supplied` | warning        |
| Forgot a key that has a `default` in `variables.tf` | `check_inputs()` step 3 — "optional variable not overridden"       | info (verbose) |

Row 2 is the completeness check that rule 2 was supposed to provide. It falls out
of injection scoping for free: a forgotten `references` entry means the key is not
supplied, and Terraform has already declared it required. **This only works if
direction B compares `variables.tf` against the *injected* set** — using the full
environment set would mask precisely this error. Hence the correction in problem 1.

**Row 3 needs a second look — a default was chosen for a reason.** A module
author's `default` exists so the module works standalone, without every consumer
supplying a value. Most optional variables in a shared root are *never* meant to
be overridden by any given deployment — warning every time one is absent would be
noise, not signal, and would erode trust in the check the same way a decorative
allowlist does. The single "optional variable not overridden" bucket conflates two
situations that deserve different severities:

| Case                                                                                          | Meaning                                                                                                             | Severity                            |
| --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- | ----------------------------------- |
| **3a** — key absent from the injected set **and** absent from the environment entirely        | nobody supplied a value anywhere; the module's default is exactly what's intended                                   | info (unchanged, every provisioner) |
| **3b** — key absent from the injected set **but present** in `spec.variables`/`spec.features` | the environment *does* have a value; scoping excluded it because no component of a scoped provisioner references it | warning (scoped provisioner only)   |

3b is the case worth flagging: someone set a value in the environment, presumably
to override the default, and it silently never reaches Terraform because
`references` under-declares it. 3a is not a mistake and must stay quiet — the
default being unreferenced by anything is the common, unremarkable case for most
optional variables in a shared root.

This requires `check_inputs()` to see **two** sets, not one: the injected set
(for rules 1–2 and 3a/3b's first half) and the full environment set, scoped by
`stage.secrets` exactly as today but *not* narrowed by `references` (to tell 3a
from 3b). Both are cheap — `_collect_declared_input_keys()` already computes the
full environment set before this change; scoping produces the injected set as a
second, narrower value from the same inputs.

**Residual risk, narrow and nameable:** only 3b — a variable with a `default`
that the environment supplies a value for, but that no component's `references`
names, silently falls back to the module default instead of the supplied value.
That is now a warning under a scoped provisioner, not silence.

**Consequences for the rest of the design:**

- Rule 2 is no longer load-bearing. It stays where it already exists
  (dns/network/module) as a cheap extra check, but Option F does not depend on it.
- The earlier sub-options F-a … F-d are withdrawn. They were all workarounds for a
  gap that does not exist: F-a (accept an unenforced allowlist), F-b (add a
  `${var:}` expression surface to `kind: resource`), F-c (derive references
  instead of declaring them), F-d (provisioner-level scoping only, solving the
  triggering case without making anything more precise).
- `spec.references`' sparse population in shipped configs stops being fatal: it
  gates opt-in, and an opted-in provisioner whose components under-declare fails
  loudly at build time rather than silently checking nothing.

## Implementation Plan (executable)

This section specifies exact signatures, exact algorithms, and a test plan, so
each rollout step can be implemented directly from this doc without re-deriving
anything. File paths and line numbers are current as of 2026-09-15 and will
drift; class/function names will not.

### New model — `ProvisionerReferencesModel`

Every kind that carries `references` defines its own `<Kind>ReferencesModel`
(`ResourceReferencesModel`, `ProviderReferencesModel`, `NetworkReferencesModel`,
`DnsReferencesModel`, `TenantReferencesModel` — each a separate class, not shared
via import, per this codebase's existing convention). A provisioner gets the same
treatment, defined in `workspace_model.py` itself — **do not** import
`ResourceReferencesModel` from `resource_model.py`; that would be the first
cross-kind reference-model import and breaks the established pattern:

```python
class ProvisionerReferencesModel(PlatformBaseModel):
    """References to variables, secrets, and features required by this provisioner.

    For provisioners without a component document (script, compose) to declare
    references on. Same shape as ResourceReferencesModel and friends, duplicated
    per this codebase's existing per-kind convention — see ADR-0078.
    """

    variables: VariableRefs = Field(None, description="Variable keys this provisioner requires from environment")
    secrets: SecretRefs = Field(None, description="Secret keys this provisioner requires from environment")
    features: FeatureRefs = Field(None, description="Feature keys this provisioner requires from environment")
```

`VariableRefs`/`SecretRefs`/`FeatureRefs` are not currently imported into
`workspace_model.py` — add them to the existing
`from strata.models.common_models import (...)` block (they already exist in
`common_models.py`, used identically by `resource_model.py` et al.).

On `WorkspaceIacModel`, add the field next to `inputs_from` (matches the Gap 1
YAML example's field order):

```python
    references: Optional[ProvisionerReferencesModel] = Field(
        None,
        description=(
            "Keys this provisioner requires from the environment. Opts this "
            "provisioner into scoped injection (ADR-0078) when set on any of a "
            "workspace's provisioners or on any bound resource/module/dns/provider."
        ),
    )
```

### Computing `injected(P)`

**Architectural fact that constrains this algorithm:** `WorkspaceResourceModel`,
modules, dns and provider entries are **not bound to a specific provisioner**
today. `TerraformBuilder._build_resources_by_category()` builds one
`resources_by_category` dict for the whole workspace and every Terraform-type
provisioner receives the same tfvars content — there is no
`WorkspaceResourceModel.provisioner` field or equivalent.

This means `components(P)` cannot, today, be computed as "components bound to
*this* provisioner" for resource/module/dns/provider-level references — only
Gap 1's provisioner-level `references` are naturally 1:1. The implementable v1
algorithm therefore treats resource/module/dns/provider references as
**workspace-wide**, contributing identically to every Terraform-type provisioner,
which exactly matches how `resources_by_category` is already emitted uniformly
today:

```python
def compute_injected(
    workspace_model: WorkspaceModel,
    prov: WorkspaceIacModel,
    all_environment_keys: Dict[str, Set[str]],  # {"variables": {...}, "secrets": {...}, "features": {...}}
) -> Optional[Dict[str, Set[str]]]:
    """Return the injected key sets for `prov`, or None if `prov` is unscoped.

    None means: no component anywhere in the workspace declares `references`,
    and `prov` itself declares none either — today's unscoped behaviour.
    """
    workspace_components = (
        list(workspace_model.spec.resources or [])
        + list(workspace_model.spec.modules or [])
        + list(workspace_model.spec.dns or [])
        + list(workspace_model.spec.providers or [])
    )
    declaring = [c for c in workspace_components if getattr(c, "references", None)]

    scoped = bool(declaring) or bool(prov.references)
    if not scoped:
        return None  # unscoped — caller falls back to all_environment_keys

    # Rule 4: every workspace component must declare once ANY component does.
    missing = [c for c in workspace_components if not getattr(c, "references", None)]
    if missing:
        raise ScopingError(
            f"Provisioner '{prov.name}' is scoped (some components declare "
            f"spec.references) but {missing[0].name} declares none"
        )

    injected: Dict[str, Set[str]] = {"variables": set(), "secrets": set(), "features": set()}
    for c in declaring + ([prov] if prov.references else []):
        refs = c.references
        injected["variables"] |= set(refs.variables or [])
        injected["secrets"] |= set(refs.secrets or [])
        injected["features"] |= set(refs.features or [])

    # Rule 6: keys supplied by inputs_from are exempt from the completeness/typo
    # checks — they arrive from an upstream output, not the environment.
    injected["variables"] |= collect_inputs_from_keys(prov.inputs_from)

    return injected
```

This is a **stated v1 simplification**, not an oversight: for the common case
(one Terraform provisioner in the workspace) it is exactly correct — there is
nothing to disambiguate. For a workspace with two or more Terraform provisioners,
every one of them gets the same resource/module/dns/provider-derived injected
set; only their own `WorkspaceIacModel.references` differ. Giving resources a
`provisioner:` binding field to fix this is real future work, but is **out of
scope** — it is a new schema surface, not required by the triggering case (one
Terraform provisioner + one `script` provisioner), and should not be built
speculatively (see [Open Question 3](#open-questions)).

### Signature changes

**`terraform_input_validator.check_inputs()`** — add the full-environment-set
parameter needed to distinguish rule 3a from 3b:

```python
def check_inputs(
    injected_keys: Set[str],           # was: declared_keys — now the scoped/injected set
    environment_keys: Set[str],        # NEW — full environment set, unscoped, for 3a/3b
    module_variables: Dict[str, TerraformVariable],
    excluded_keys: Optional[Set[str]] = None,
) -> InputCheckResult:
    ...
    # Direction A (rule 1) and direction B (rule 2): unchanged, but run against
    # injected_keys instead of the old single `declared_keys` parameter.
    ...
    # Direction C (rule 3a/3b), replacing the old single "optional not overridden":
    for var_name, var in module_variables.items():
        if var_name in excluded or not var.has_default or var_name in injected_keys:
            continue
        if var_name in environment_keys:
            result.warnings.append(   # 3b
                f"Optional variable '{var_name}' not supplied — Terraform will use its "
                f"default ({var.default_value!r}). '{var_name}' has a value in the "
                "environment, but no component of this scoped provisioner references it."
            )
        else:
            result.info.append(f"Optional variable '{var_name}' not overridden (default: {var.default_value!r})")  # 3a, unchanged
```

Note 3b only fires when the caller passes a **narrower** `injected_keys` than
`environment_keys` — for an unscoped provisioner the two sets are equal (see
below), so every case falls through to 3a, i.e. today's behaviour exactly.

**`TerraformBuilder._collect_declared_input_keys()`** — becomes the caller that
produces both sets and picks which to trust:

```python
def _collect_declared_input_keys(
    self, deployment_service: DeploymentService, prov: WorkspaceIacModel, matching_stages: Optional[List[Any]] = None
) -> Tuple[Set[str], Set[str]]:
    """Return (injected_keys, environment_keys) for `prov`."""
    environment_keys = self._collect_all_environment_keys(deployment_service, matching_stages)  # today's existing logic, renamed
    scoped = compute_injected(workspace_service.model, prov, environment_keys)
    injected_keys = scoped["variables"] | scoped["secrets"] | scoped["features"] if scoped is not None else environment_keys
    return injected_keys, environment_keys
```

Call site in `_validate_inputs()` changes from one `check_inputs(declared_keys, ...)`
call to `check_inputs(injected_keys, environment_keys, ...)`.

**`ResolvedValues.for_stage()`** — gains scoping for variables/features, mirroring
the existing `secrets` allowlist parameter shape exactly:

```python
def for_stage(
    self,
    allowed_secrets: Optional[List[str]] = None,
    allowed_variables: Optional[List[str]] = None,   # NEW
    allowed_features: Optional[List[str]] = None,    # NEW
) -> "ResolvedValues":
    """... existing docstring, extended:

    allowed_variables / allowed_features: same semantics as allowed_secrets —
    None ⇒ unscoped (all variables/features, today's behaviour), ['*'] ⇒
    explicit all, ['a','b'] ⇒ only those keys. Every existing call site passes
    None (or omits the argument) until it opts in, so behaviour is unchanged
    until step 5's callers are updated to pass the provisioner's injected(P).
    """
```

Apply the same filtering pattern already used for `secrets`/`stage_outputs_sensitive`
to `variables`/`features` in all three branches of the existing `if`/`elif`/`else`.

Both call sites in `base_deploy_command.py` (lines 186, 671) change from
`_resolved_values.for_stage(stage.secrets)` to passing the resolved provisioner's
`injected(P)` variables/features alongside `stage.secrets` — resolved via the same
provisioner-lookup helper `_stages_for_provisioner()`/`_resolve_iac_model()`
already uses, so no new resolution logic is needed there, only new arguments.

### Test plan

New/updated test files, one bullet per behaviour to cover — no new test
infrastructure needed, all of this fits the existing per-module test file
convention (`tests/strata/<area>/test_<module>.py`):

- `tests/strata/models/test_models_workspace.py` — `ProvisionerReferencesModel`
  validates variables/secrets/features independently; `WorkspaceIacModel.references`
  round-trips through YAML; rule 4 raises when one component declares and another
  doesn't (via whatever service method implements `compute_injected()`).
- `tests/strata/utils/test_utils_resolved_values.py` — `for_stage()`'s three new
  parameters mirror `allowed_secrets`' None/['*']/['a','b'] cases exactly; a test
  asserting **omitting** the new parameters preserves current behaviour bit-for-bit
  (regression guard for the "no workspace breaks on upgrade" claim).
- `tests/strata/validators/test_terraform_input_validator.py` — `check_inputs()`'s
  new `environment_keys` parameter: 3a fires when a key is in neither set, 3b fires
  when a key is in `environment_keys` but not `injected_keys`, unchanged when the
  two sets are equal.
- `tests/strata/builders/test_builders_terraform.py` — end-to-end: the worked
  example's exact YAML (network resource + bootstrap script provisioner) builds
  clean; a component-under-declares case produces the rule 4 error; the triggering
  case's dummy `variable {}` is provably no longer required (build a fixture
  without it and assert no error).
- `tests/strata/models/test_models_workspace.py::TestRemovedReferencesField` —
  already exists from Gap 2; no changes needed.

### Task list per rollout step

| Step | Files touched                                                                                     | New tests                                                             |
| ---- | ------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| 2    | `models/workspace_model.py` (add `ProvisionerReferencesModel`, `WorkspaceIacModel.references`)    | model round-trip tests                                                |
| 3    | `services/workspace_service.py` (add `compute_injected()` or equivalent, exposed but unconsumed)  | unit tests on `compute_injected()` directly                           |
| 4    | `builders/terraform_builder.py`, `validators/terraform_input_validator.py`                        | `check_inputs()` 3a/3b tests, rule 4 error, end-to-end worked example |
| 5    | `utils/resolved_values.py`, `commands/deploy/base_deploy_command.py`                              | `for_stage()` new-parameter tests, deploy-time injection test         |
| 6    | `models/resource_model.py`, `models/module_model.py` (optional rule 5 validators)                 | mirrors existing `DnsSpecModel.validate_references_declared()` tests  |
| 7    | remove the `drop_removed_references` validators + `warnings` import from both models (next minor) | delete the now-obsolete deprecation tests                             |

## Analysis — allow- vs deny-by-default

*(Retained as findings. This section deliberately does not select an option — see
[Status](#status) below.)*

Deny-by-default is the right instinct for secrets and the wrong one for
variables, because **`stage.secrets` and variable scoping are not the same
mechanism wearing different defaults. They solve different problems:**

|                      | `stage.secrets`                                             | variable scoping                              |
| -------------------- | ----------------------------------------------------------- | --------------------------------------------- |
| Purpose              | **Access control**                                          | **Dependency declaration**                    |
| The stage is…        | a trust boundary                                            | a consumer                                    |
| Cost of over-sharing | a leaked credential                                         | a validator false-positive                    |
| Correct default      | **deny** — least privilege                                  | **allow** — least ceremony                    |
| Belongs on           | the **stage** (read a stage, see exactly what it can touch) | the **consumer** or the **value**, per option |

An earlier draft flagged the differing defaults as an inconsistency to apologise
for. That was wrong: **the difference is justified by purpose, and forcing
symmetry would be the actual mistake.** Secrets deny-by-default because the blast
radius of over-sharing is a credential leak. Variables allow-by-default because
the blast radius is a build-time warning — and because the ceremony of
deny-by-default (Option B) is what drives teams to `['*']`, which destroys the
mechanism's value for secrets too by normalising the escape hatch.

For a DevOps profile specifically: deny-by-default only produces predictability if
the allowlists are actually maintained. At 40+ variables across multiple stages
they will not be, and a decorative allowlist is worse than none — it looks like a
guarantee while providing none.

### On failure modes

The dominant criterion. Recent incidents in this codebase — `PlanBuildCommand`
silently omitting `repo_map`, and `get_repo_map()` silently resolving against
`os.getcwd()` (both ADR-0077) — were expensive precisely because they failed
*silently* and were diagnosed hours later against a misleading error.

| Option                     | Failure mode when got wrong                                                          |
| -------------------------- | ------------------------------------------------------------------------------------ |
| B (deny, stage enumerates) | Moderately loud — "required variable not set"                                        |
| C (allow, stage narrows)   | **Silent** — a new variable omitted from a narrowed list is simply never injected    |
| D (allow, stage excludes)  | Loud, but exclusion lists compose badly                                              |
| E (allow, variable routes) | Loud — forgetting reverts to today's behaviour + today's error                       |
| F (references-scoped)      | Loud **if** both directions are enforced; **silently vacuous** if adoption stays low |

Any option that introduces a new way for a value to *silently not arrive* should
be treated as disqualified.

## Status

**Accepted — Option F.** See the header for the decision trail. Options A–E are
retained below as rejected alternatives with their reasoning; Option F's design,
review, and executable implementation plan are the sections above and below this
one.

## Open Questions

Closed by the 2026-09-15 revision:

1. ~~Should `spec.references` become load-bearing, or stay documentation?~~
   **Resolved: load-bearing**, opt-in per provisioner (rule 4). See
   [Validation rules](#validation-rules).
2. ~~Where does a `script` provisioner declare its required keys?~~ **Resolved:**
   `references` on `WorkspaceIacModel`, beside `inputs_from` — see
   [Gap 1](#gap-1--references-on-a-provisioner).
4. ~~Resolve the `references` name collision.~~ **Resolved: deleted**, not
   renamed — see [Gap 2](#gap-2--remove-the-inert-references-field--implemented).
5. ~~Does `features` need scoping at all?~~ **Resolved: yes, same mechanism as
   variables** — `ProvisionerReferencesModel`/`ResourceReferencesModel` etc. treat
   `variables`/`secrets`/`features` uniformly; there is no cost to including
   `features` once the machinery exists for `variables`.

Still open, deliberately deferred:

3. **Is type-level granularity ever insufficient?** i.e. would a variable ever
   need to reach one Terraform root but not another *within the same workspace*?
   Real, and larger than this ADR — see
   [the injected(P) algorithm](#computing-injectedp)'s stated limitation: today
   resources/modules/dns/providers are not bound to a specific provisioner at
   all, so this design cannot yet distinguish between two Terraform roots in one
   workspace. Revisit only if a concrete workspace needs it — do not build the
   binding mechanism speculatively.
6. **Should the existing dummy declarations be cleaned up regardless?**
   `enable_platform`, `enable_aks`, `enable_udr_subnet_association`,
   `privatelink_zones`, `layer_name` become removable once their workspace opts a
   provisioner into scoping (rollout step 4+). Not a prerequisite for this ADR;
   revisit per-workspace after adoption.

