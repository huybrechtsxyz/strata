# Scoping Variables and Features to Provisioners — Allow- vs Deny-by-Default

- Status: proposed — under evaluation, **no option selected**; Option F is the current focus
- Date: 2026-09-11
- Revised: 2026-09-14 — Option E's addressing scheme found unworkable against shared/layered environment files; Option F (`spec.references`) added; decision withdrawn pending evaluation
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

Scope the Terraform cross-check to **keys actually referenced by that
provisioner's components**, instead of every key declared in the environment. A
bookkeeping variable that no resource, module, provider or dns document
references is then never cross-checked, and needs no dummy declaration.

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

#### The combination that resolves it — opt-in precision, per provisioner

- Components **do** declare `references` ⇒ the check is scoped to those keys, and
  enforced **in both directions**: usage must be referenced (the DNS precedent,
  extended to resource/module), and references must exist in `variables.tf`.
- Components **do not** declare references ⇒ current behaviour, unchanged.

This is backwards-compatible, and opting in makes the check *more* precise rather
than weaker — because the usage-side enforcement closes the loop. A key that is
referenced but missing from `variables.tf` is still an error; a key used but not
referenced is a new error; a key that is neither is correctly ignored.

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

### Gap 2 — remove the inert `references` field

Delete, rather than rename:

| Location                                                                                                       | Field                        | Action                                      |
| -------------------------------------------------------------------------------------------------------------- | ---------------------------- | ------------------------------------------- |
| `WorkspaceResourceModel` ([workspace_model.py](../../src/strata/models/workspace_model.py#L342))               | `references: Dict[str, str]` | remove                                      |
| `EnvironmentResourceOverrideModel` ([environment_model.py](../../src/strata/models/environment_model.py#L118)) | `references: Dict[str, str]` | remove                                      |
| `deployment_service.py` ([L815](../../src/strata/services/deployment_service.py#L815))                         | override merge block         | remove                                      |
| `.strata/templates/workspace.yaml`, `templates/solution/…`                                                     | `references: {}`             | remove the line and its explanatory comment |

**Migration risk: low, but not zero.** Models use `extra="forbid"`, so a
workspace in the wild that *does* carry `references:` on a resource entry — copied
from the shipped template, which emits `references: {}` — would start failing
validation on upgrade. Mitigation: keep a `model_validator(mode="before")` for one
minor release that drops the key with a deprecation warning rather than erroring,
then remove the shim.

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

Enforcement is **opt-in per component** and runs **in both directions**. A
component that declares no `references` keeps today's behaviour exactly.

| #   | Rule                                                                                                                      | When it applies                 | Severity |
| --- | ------------------------------------------------------------------------------------------------------------------------- | ------------------------------- | -------- |
| 1   | Every key in `references.variables` must be declared in the provisioner's `variables.tf` (Terraform)                      | component declares `references` | error    |
| 2   | Every key **used** by the component (`var:`/`${var:…}`) must appear in its `references`                                   | component declares `references` | error    |
| 3   | Environment keys referenced by **no** component of a provisioner are **not** cross-checked against that provisioner       | always                          | —        |
| 4   | A key supplied by `inputs_from` is **not** subject to rules 1–2 — it arrives from an upstream output, not the environment | always                          | —        |

Rule 2 is the loop-closer that stops Option F from degrading into "check
nothing" — it already exists for DNS
(`DnsSpecModel.validate_references_declared()`) and is extended to
resource/module/provisioner.

Rule 4 records an existing behaviour rather than adding one:
`_collect_declared_input_keys()` already folds
`collect_inputs_from_keys(prov.inputs_from)` into the supplied set
([terraform_builder.py](../../src/strata/builders/terraform_builder.py#L1452)).
It is stated explicitly because the contract/wiring split makes the reason for it
non-obvious: `references` is about the environment, `inputs_from` is not.

`inputs_from` validation is unchanged — `WorkspaceSpecModel.validate_inputs_from()`
already checks producer existence, self-reference and cycles.


### Scoping the Terraform cross-check

`TerraformBuilder._collect_declared_input_keys()` changes from *"every key
declared in the environment"* to:

```
for each provisioner P:
    components(P) = resources ∪ modules ∪ dns ∪ providers bound to P
                    ∪ P itself (via its own references)

    if no component of P declares references:
        declared_keys(P) = all environment keys      # unchanged behaviour
    else:
        declared_keys(P) = ⋃ references of components that declare them
                           ∪ all environment keys of components that do not
```

The mixed case matters: a workspace migrating incrementally has some components
with `references` and some without. Components that have opted in are checked
precisely; those that have not fall back to the current wide check. Precision
improves monotonically as adoption grows, and no workspace breaks on upgrade.

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
ERROR  Provisioner 'infra': references key 'vnet_cidr' has no matching
       'variable "vnet_cidr"' block in terraform/variables.tf

ERROR  Resource 'app_tier': uses ${var:region} but 'region' is not declared
       in spec.references.variables
       → add 'region' to spec.references.variables in config/app.yaml

WARN   Workspace resource 'app_tier': 'references' is no longer supported and
       has been ignored. The field was never read; remove it.
```

### Files affected

| File                                                       | Change                                                                                                                     |
| ---------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `models/workspace_model.py`                                | remove `WorkspaceResourceModel.references`; add `WorkspaceIacModel.references`; add the one-release drop-with-warning shim |
| `models/environment_model.py`                              | remove `EnvironmentResourceOverrideModel.references`                                                                       |
| `services/deployment_service.py`                           | remove the `references` override-merge block                                                                               |
| `models/resource_model.py`, `module_model.py`              | usage-side validator (rule 2), mirroring the DNS one                                                                       |
| `builders/terraform_builder.py`                            | `_collect_declared_input_keys()` scoping; `_validate_inputs()` messages                                                    |
| `services/workspace_service.py`                            | resolve provisioner ↔ component binding for the scoping step                                                               |
| `.strata/templates/workspace.yaml`, `templates/solution/…` | remove the `references: {}` line and its comment                                                                           |
| `docs/config/workspace.md`, `resource.md`                  | document `references` on provisioners; document the contract/wiring split                                                  |

Unchanged: `inputs_from`, `ProvisionerInputMappingModel`,
`apply_input_mapping()`, `validate_inputs_from()`, and every deployer.

### Rollout

1. Remove the inert `references` field (both models + the merge block + templates),
   with the drop-with-warning shim (no behaviour change — nothing read it).
2. Add `references` to `WorkspaceIacModel` (additive, no behaviour change).
3. Add rule 2 (usage-side) for resource/module, opt-in via declaring `references`.
4. Switch `_collect_declared_input_keys()` to the scoped computation.
5. Next minor: remove the drop-with-warning shim.

Steps 1–3 are independently shippable and individually reversible. Only step 4
changes what the build accepts, and only for workspaces that opted in at step 3.

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

**No decision yet.** Options A–E are recorded above; Option F is the current focus
of evaluation. The open fork is stated in [Open Questions](#open-questions) item 1.

Remaining work is deliberately not enumerated until an option is selected — the
task list differs substantially between E (new schema field, routing resolution,
emission filtering) and F (making an existing mechanism load-bearing, plus a
migration path that does not weaken checks during adoption).

## Open Questions

1. **Should `spec.references` become load-bearing, or stay documentation?** This
   is the real fork. Load-bearing gives a complete, enforced dependency graph and
   solves this cleanly — but the current 2-occurrence adoption rate has to rise,
   and every existing workspace needs a migration path that does not silently
   weaken its checks in the meantime.
2. **Where does a `script` provisioner declare its required keys?** Probably
   `references` on the provisioner in the workspace file, beside `inputs_from` —
   but that needs confirming against how script provisioners are actually
   configured.
3. **Is type-level granularity ever insufficient?** Concretely: would a variable
   ever need to reach *one* Terraform root but not another within the same
   deployment? If not, several options collapse to the same thing and the
   finer-grained ones are over-engineering.
4. **Resolve the `references` name collision** (required-keys vs cross-resource
   wiring) before building further on the term.
5. **Does `features` need scoping at all**, or only `variables`? The mechanism is
   free once variables have it, but if no real case exists it may be surface area
   with no demand.
6. **Should the existing dummy declarations be cleaned up** regardless?
   `enable_platform`, `enable_aks`, `enable_udr_subnet_association`,
   `privatelink_zones` and `layer_name` are genuinely unused — a different
   problem, and possibly they should simply be deleted rather than scoped.

