# Variables, secrets and features — requirement, interface, injection and grant

- Status: proposed
- Date: 2026-09-20

This ADR records what was established in a design conversation on 2026-09-20,
working from two real platforms rather than from the existing code. It is
deliberately a **findings document first**: the conceptual model and the verified
facts are the durable part, and the implementation choice follows from them.

## Context and Problem Statement

[ADR-0078](0078-scoping-variables-and-features-to-provisioners.md) introduced
scoping of variables and features to provisioners. It works for the platform it was
designed against and cannot work for the other one:

- **haven** — one Terraform root that consumes the strata platform model itself
  (`resources`, `topologies`, `modules`, …) plus a handful of named environment
  keys. The workspace *is* the description of the infrastructure.
- **int-deployment** — five-plus independent Terraform roots (`control`, `ring`,
  `customer`, `spoke`, and `dispatcher_api` from an entirely different repository),
  each pre-existing, each with its own input interface, all sharing one
  environment. The workspace is a *composition* of things that already exist.

In the second case, every root receives every environment key. `dispatcher_api`
declares two of the environment's variables and receives all 22, plus all 7 feature
flags, and the build fails with 21 "not declared in variables.tf" errors.

ADR-0078's scoping cannot fix this, and the reason is structural rather than a bug
— see [Requirements union, grants restrict](#requirements-union-grants-restrict).

## What we learned

### Four concepts, of which strata models two and a half

| #   | Concept         | Question it answers                 | Modelled today                           |
| --- | --------------- | ----------------------------------- | ---------------------------------------- |
| 1   | **Requirement** | "What does this component need?"    | ✅ `references`                           |
| 2   | **Interface**   | "What can this root accept?"        | ⚠️ parsed, but only used to complain      |
| 3   | **Injection**   | "What does this root actually get?" | ❌ **undefined — defaults to everything** |
| 4   | **Grant**       | "What is this run allowed to see?"  | ⚠️ `stages[].secrets`, secrets only       |

(3) is the gap. It was never decided; it fell out as a default. So when an operator
wants to narrow, there is no lever to pull, and they reach for (1) — because it is
the only list-shaped thing available, and it was never on that path.

This also explains `stages[].secrets`: it exists because someone needed (3) for
secrets, found nowhere to put it, and attached it to the stage. **The secret
allowlist is compensating for the absence of a definition of injection.**

### Requirements union, grants restrict

`references` and `stages[].secrets` look alike — both are lists of key names — but
they compose in opposite directions:

- **Requirements compose by union.** If A needs `x` and B needs `y`, both must be
  satisfied. Adding a component can only add needs.
- **Grants compose by restriction.** The narrowest applicable permission wins.
  Adding a constraint can only remove.

ADR-0078 derives its injected set from a union of requirements. A union cannot
narrow — subtraction is not an operation it has. The measured "every provisioner
sees all 22 keys" is therefore not an implementation defect to be patched; it is
what that formula must produce.

### Requirement is not the same as authority

A resource does not read a variable. The Terraform root that *materialises* the
resource does. So a requirement declared on a resource is a statement about code
one step removed from the declaring object.

The useful test for where something belongs is: **who is the authority, and who
would be wrong to override them?**

| Role              | Authority on                    | Artefact                |
| ----------------- | ------------------------------- | ----------------------- |
| Component author  | what this code consumes         | the IaC source          |
| Platform engineer | what exists and how it composes | workspace, resources    |
| Operator / SRE    | values, and who may see them    | environment, deployment |

Only the component author can say what `deploy_dsp/terraform` consumes. Only the
operator can say what `vnet_name` is in prd, or whether a stage may see
`db_password`. `dispatcher_api` lives in a different repository from the workspace
that composes it, so this three-way split already exists in practice — the config
merely does not reflect it.

### Two input channels, not one

A Terraform root receives values through two independent channels, and the code
already distinguishes them (`EmitCategory` in `workspace_model.py`):

| Channel       | Contents                                                                                                          | Source                |
| ------------- | ----------------------------------------------------------------------------------------------------------------- | --------------------- |
| Structural    | `workspace`, `resources`, `topologies`, `modules`, `namespaces`, `providers`, `firewalls`, `properties`, `custom` | the platform artifact |
| Environmental | `variables`, `features` (and secrets at deploy time)                                                              | the environment       |

Only the environmental channel is in question here. Narrowing it does not disturb
the structural one.

### Verified facts about the current implementation

Each of these was checked against the source, and several contradict assumptions
recorded in ADR-0078:

| Fact                                                | Evidence                                                                                                                                                                                                                                                                                               |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `resource → provisioner` **is** derivable           | `WorkspaceTopologyModel.provisioner` is a **required** single-valued field; `components[].resource` is required (`min_length=1`) and validated against `spec.resources`; `spec.topology` itself is required. `resolve_stage_provisioner_name()` already walks `stage.topology → topology.provisioner`. |
| ADR-0078 states the opposite                        | It records "there is no `WorkspaceResourceModel.provisioner` field **or equivalent**". The field is absent; the equivalent is one hop away through the topology. The "v1 simplification" rests on this.                                                                                                |
| A root's interface is already parsed                | `parse_variables_tf()` globs **every root-level `*.tf`** (not just `variables.tf`) with a real HCL parser, capturing `has_default`, `sensitive`, `nullable`, `type_expr`.                                                                                                                              |
| …per provisioner, at build time, from the real code | `_validate_inputs()` loops provisioners, resolves each one's build directory via `get_provisioner_path()`, and parses the **copied** source.                                                                                                                                                           |
| …and then discards it                               | `_save_terraform_vars()` is a *separate loop over the same provisioners* that never sees `module_vars`. One computes the interface and throws it away after checking; the other writes the files without it.                                                                                           |
| Per-provisioner output control already exists       | `OutputProfileModel.emits` selects which **categories** a provisioner receives. There is no key-level equivalent — `variables` is all-or-nothing.                                                                                                                                                      |
| Only Terraform is introspected                      | `_validate_inputs()` skips every non-Terraform type. For 7 of the 8 `ProvisionerType` values, nothing is validated and everything is injected.                                                                                                                                                         |
| `argocd` / `flux` have no source to introspect      | `_SYNC_PROVISIONER_TYPES` — they render from the platform artifact; the artifact *is* their input.                                                                                                                                                                                                     |

### How the secret grant binds — and where it disagrees with itself

`stages[].secrets` is the only grant that exists, but it is declared on a **stage**,
while the thing that receives values is a **provisioner**. The binding between them
is `deploy.spec.stages[].provisioner | topology`, resolved by
`resolve_stage_provisioner_name()` with strict priority:

1. `stage.provisioner` — explicit name
2. `stage.topology` → that topology's required `provisioner` field
3. the sole workspace provisioner, only when exactly one is declared

An explicit-but-unresolvable reference returns `None` — a hard failure, never a
silent fallback to a different provisioner.

So a grant written on a stage may reach its provisioner **indirectly**, through a
topology declared in a different file by a different person. The author of
`secrets: [db_password]` is not necessarily looking at the thing that will receive
it.

**The two sides then use that binding differently:**

|        | Set used                                                                  | Source                                                           |
| ------ | ------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| Build  | **Union** of `secrets` across *every* stage resolving to this provisioner | `_stages_for_provisioner()` → `allowed_secret_keys_for_stages()` |
| Deploy | **Only** the running stage's `secrets`                                    | `ResolvedValues.for_stage(stage.secrets)`                        |

Build therefore validates against a **broader** set than any single deploy
receives. Two stages targeting one provisioner with different allowlists both
validate clean against the union, and each then runs with strictly less than was
checked. Nothing catches the difference.

**And the fallback is non-monotonic**, which is the sharper edge:

| Situation                                      | Secrets in scope at build |
| ---------------------------------------------- | ------------------------- |
| No stage resolves to this provisioner          | **all**                   |
| A matching stage declares `secrets: ['*']`     | **all**                   |
| Matching stages exist, none declares `secrets` | **none**                  |

Adding a stage that declares nothing therefore *narrows* the validated set from
everything to nothing, while adding no stage at all leaves it at everything. That
is surprising in the direction that matters: the configuration that looks most
innocuous is the one that silently empties the scope.

Note also that in the same function, **variables and features are added
unconditionally** — only secrets consult the stage binding at all. The asymmetry
recorded elsewhere in this ADR is visible in a single block of code.

### `references` already has two unrelated jobs — audit of every consumer

Added after an explicit audit of every `.references` read in `src/strata`. The
earlier claim in this ADR that `references` is "optional documentation that gates
nothing" is **false for three kinds**, and that matters for any option considered
here.

**Job 1 — provisioner input scoping (ADR-0078).** `resource` / `module` /
`provider` / provisioner `references` are unioned by
`TerraformBuilder._compute_injected_keys()` into `injected(P)`, which becomes the
validation scope for `check_inputs()`. Optional; absent means unscoped. This is the
job the rest of this ADR is about.

**Job 2 — document-local value binding.** `dns`, `module` and `network` documents
let sibling fields name a key *by string*, and `spec.references` is the namespace
those names resolve against. It is **mandatory**, and it is consumed at build time
to produce real values:

| Kind      | Field that indexes into `references`                    | Enforcement                                                                                                               | Resolved by                                                                      |
| --------- | ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `dns`     | `zones[].records[].var` / `.secret`                     | `DnsSpecModel.validate_references_declared()` — **raises** if `references` is absent while any record uses `var`/`secret` | `terraform_builder.py:630`, `ansible_builder.py:593`                             |
| `module`  | `services[].environment[].var` / `.secret` / `.feature` | `ModuleService._validate_self_consistency()` — error per undeclared key                                                   | `helm_builder.py:531`, `compose_builder.py:511`                                  |
| `network` | `networks[].address_space[].var`, `subnets[].cidr.var`  | `network_model.py:191`                                                                                                    | `terraform_builder.py:711,730`; also read by `diagram_source_controller.py:1177` |

`module` does **both** jobs with the same field: it contributes to `injected(P)`
*and* is the namespace its own `services[].environment` entries resolve against.

`network_service.py` additionally **merges** `references` across layered network
documents, so the namespace is assembled from several files.
`platform_artifact_model.py` carries `references` into the built artifact, so
downstream consumers see it too.

**Consequences for this ADR:**

- Any option that makes `references` optional, derived, or ignorable must exempt
  Job 2, or `dns`, `module` and `network` break outright. Option C in particular
  cannot simply "return `references` to documentation".
- This is the same *one field, two meanings* problem flagged elsewhere in this ADR
  as a risk to avoid — it already exists, and it predates ADR-0078. Adding a third
  meaning on the provisioner entry made an existing overload worse rather than
  introducing a new one.
- Job 2 is a genuinely good pattern and is not in question: a document declares the
  environment keys it may use, then references them by name. It is arguably what
  `references` should have meant everywhere.

### The mode A / mode B split does not exist

It was assumed that "dynamic" workspaces consume generic structured inputs while
"fixed" roots name their inputs individually, and that the two need different
handling. Checking all three real roots disproves it:

| Root                                    | Declares | Shape                                                          |
| --------------------------------------- | -------- | -------------------------------------------------------------- |
| haven `deploy/terraform`                | 21       | 13 structural **+ 8 named environment keys**                   |
| `iac-int` `control/terraform`           | 25       | structured config objects + `enable_*` flags + named variables |
| `iac-int` `ring/terraform` (`core_iac`) | 27       | some structured + **7 `enable_*` flags** + named variables     |
| `dispatcher_api` `deploy_dsp/terraform` | 29       | entirely application-specific, **zero structural**             |

Every root names its environment keys explicitly. The only difference is whether a
root *also* consumes the structural artifact — and that difference is itself
expressed by declaring (or not declaring) `variable "resources"` and friends.

### Scoping is already being done — by splitting environment files

int-deployment does not use one environment. It keeps one per stack:
`stacks/{core,ring,customer,spoke,instance}/environment.yaml`, layered and
overridden per hub × spoke × customer × ring.

So the environment is scoped **per deployment**, and the problem only appears in
the one place that granularity cannot reach: a single deployment whose workspace
declares **two provisioners with different appetites** — `core_iac` (27 declared
inputs) and `dispatcher_api` (29, of which 2 overlap the environment).

This matters for sizing the problem. It is not "strata cannot scope values". It is
"the existing unit of scoping is the deployment, and provisioners are finer than
that".

### The requirement relationship is already being documented — in prose

From `stacks/ring/environment.yaml`, per key, unprompted:

> `spoke` — "used only to template workspace.yaml's Terraform state key
> (`int-{spoke}-{customer_code}-{ring}.tfstate`), **not a Terraform root input**"
>
> `ring` — "templates workspace.yaml's Terraform state key, and (since
> ring-estate) **is also a real ring/terraform root input**"

Two observations:

1. The platform team is hand-maintaining, in descriptions, exactly the
   requirement mapping this ADR is about. Revealed preference: the need is real
   and currently unmet by anything structural.
2. `spoke` is the ADR-0078 triggering case in the wild — a legitimate key that
   exists for strata's own templating and is deliberately *not* a Terraform root
   input. Any rule that validates environment keys against `variables.tf` must
   accommodate it.

### The name-collision hazard is latent, not live

`dispatcher_api` declares `location`, `project_name`, `resource_group_name` and
`environment_prefix` — generic names, each with a default, chosen by a team with no
knowledge of this environment. If a key of the same name later appears in the
shared environment, it silently overrides that default and the root deploys
somewhere or as something other than intended.

Checked: none of those four currently exist in the int environments. So this is a
**foreseeable** hazard, not a present defect — and worth recording precisely
because a "give everything to everyone" default makes it strictly more likely as
the environment grows.

### Nothing actually breaks today — the failure is self-inflicted

This is the finding that most affects how much machinery is justified.

Terraform **ignores** `TF_VAR_x` for any `x` it does not declare, and merely
*warns* on an undeclared entry in a `.auto.tfvars.json`. Handing `dispatcher_api`
all 22 variables and all 7 feature flags breaks nothing in Terraform.

The 21 `Input 'x' is not declared in variables.tf` errors that block the build are
produced by **strata's own validator** (`check_inputs()`), not by Terraform.

So for **variables and features**, scoping is not a correctness requirement. It is
noise reduction, plus protection against the latent collision above. The hard
requirement is secrets — and secrets already have a mechanism.

### The requirements, restated from zero

Starting from "one environment, everything goes to every provisioner", and asking
what genuinely justifies deviating from it:

| #   | Requirement                                                      | Driver                                                                           | Strength                           |
| --- | ---------------------------------------------------------------- | -------------------------------------------------------------------------------- | ---------------------------------- |
| R1  | Do not place every secret in every process environment           | A `script`/`ansible` provisioner receives all of them                            | **Hard** — security, irreducible   |
| R2  | Do not let an environment key silently override a root's default | Generic names in externally-authored roots                                       | **Latent** — foreseeable, not live |
| R3  | Report what is missing before touching a cloud                   | Terraform reports at plan, costing credentials, network, and one stage at a time | **Soft** — speed and CI cost       |
| R4  | Show which root consumes which key                               | Being paid for manually in prose today                                           | **Soft, but demonstrated**         |

R1 is met by `stages[].secrets`. R2 and R3 are partially served by the current
validator. R4 is met by nothing.

Notably absent: *"scope variables so Terraform works"*. Terraform already works.

## Considered Options

### Option A — a provisioner's `references` overrides the workspace-wide union

Smallest change; unblocks int-deployment. Rejected as the primary model: it obtains
narrowing by letting a *requirement* act as a *grant*, which is the same category
error in a smaller box. Retained as a possible stop-gap.

### Option B — add a grant for variables, mirroring `stages[].secrets`

Consistent with the existing secret mechanism. Rejected: the operator is not the
authority on a root's input surface, and this would add a second hand-maintained
list per stage that must track the code. It also does not reduce the duplication it
would be introduced to manage.

### Option C — derive injection from the root's declared interface

```
injected(P) = interface(P) ∩ available(environment)
```

Where `interface(P)` is introspected for provisioner types that permit it
(Terraform today; Bicep, Helm-with-schema and Compose are feasible) and declared
for those that do not (`script`, and `ansible`/`compose` without a schema).

Both platforms become correct with **no configuration at all**:

- haven declares the structural variables *and* its 8 environment keys, so it
  receives both.
- `dispatcher_api` declares 2 of the environment's keys and no `enable_*`, so it
  receives 2 variables and an empty flags file.
- `core_iac` declares 27 and receives 27.

The structural channel collapses into the same rule: declaring
`variable "topologies"` *is* the opt-in, so `output.emits` stops being load-bearing
for correctness.

### Option D — stop validating environment keys the root does not declare

The narrowest option, and the one the zero-base requirements analysis points at.

Nothing about the current behaviour is *broken* for Terraform: it ignores
`TF_VAR_x` for undeclared `x` and warns on undeclared tfvars entries. The build
failure is produced by strata's own `check_inputs()`. Drop that direction of the
check — keep only "a required variable has no value" — and int-deployment is
unblocked with **no new concept, no new field, and no change to what is
injected**.

- Good: smallest possible change; removes a self-inflicted failure rather than
  adding machinery to route around it.
- Good: accommodates the `spoke` case (a key that exists for strata's own
  templating and is deliberately not a root input) without a special rule.
- Bad: gives up typo detection. `vnet_nmae` in an environment becomes silent
  rather than caught — though the *root's* missing `vnet_name` would still be
  reported by the surviving check.
- Bad: leaves R2 (silent default override) and R4 (comprehension) entirely unmet,
  and leaves every provisioner receiving every value.

Option D is not exclusive with C. D is the fast unblock; C is the model. They can
ship in that order, and D does not have to be reverted for C to land.

## Decision Outcome

**No decision yet.** The analysis points at **Option C** as the model and possibly
**Option D** as the immediate unblock, with `references` **in its Job 1 role**
(provisioner input scoping) reduced to documentation and validation, gating
nothing — and `stages[].secrets` retained as a genuine second axis for withholding
a secret a root legitimately asked for.

**Job 2 is out of scope and must not be disturbed.** `dns`, `module` and `network`
depend on `spec.references` as a mandatory, build-time-resolved namespace; see
[the audit](#references-already-has-two-unrelated-jobs--audit-of-every-consumer).
Any option here applies to Job 1 only.

This ADR is raised at `proposed` deliberately: the findings above are worth
recording before any of them is acted on, because several of them correct the
factual basis of ADR-0078 — and one corrects an earlier claim in this ADR — and
would otherwise be rediscovered.

**A caution against over-building.** The zero-base requirements analysis produced
exactly one hard requirement (R1, secrets), which is already met. Everything else
is latent, soft, or self-inflicted. Any option adopted here should be justified
against that table rather than against the apparent size of the problem — the
21-error build failure looks like a large problem and is, in fact, a validator
objecting to a configuration that would have worked.

### Consequences if Option C is adopted

- Good: both platforms are correct by default; the configuration that would
  otherwise be required disappears. That is the test of a right primitive.
- Good: no new schema surface. The interface is already parsed, per provisioner,
  at build time.
- Good: validation sharpens — "root declares `X`, no default, environment has no
  `X`" becomes a precise pre-deploy error rather than a warning under noise.
- Bad: one of the two existing checks becomes vacuous. "Input not declared in
  variables.tf" cannot fire when the injected set *is* the declared set. The
  failure reappears as "required variable not supplied", but teams who treat the
  strata-side declaration as a review surface lose it.
- Bad: interface matching is **by name**, and assumes the environment's namespace
  agrees with the root's. `dispatcher_api` declares `location`, `project_name`,
  `resource_group_name` — generic names chosen by a team unaware of this
  environment. A collision is silent. This is true today as well, so it is not a
  regression, but Option C does not fix it either.
- Bad: it changes what is written to disk per provisioner. Invisible to a
  well-behaved root (Terraform ignores values for variables it does not declare),
  but `format: script` provisioners receive raw files and may parse them.

## Remaining Work

Nothing in this ADR has been implemented. Before it can move past `proposed`:

- **Decide whether the problem justifies the model.** The requirements table gives
  one hard requirement, already met. Option D unblocks int-deployment by deleting a
  check; Option C builds a model. Choosing C means accepting that R2/R3/R4 are
  worth it — that case has not been made, only sketched.
- **Decide Option D's typo trade.** Dropping the undeclared-key check gives up
  catching `vnet_nmae` in an environment file. Whether that check has ever caught a
  real typo, or only ever produced the `spoke`-shaped false positive, is answerable
  from history and has not been checked.
- **Settle the name collision question.** Whether `injected(P)` should eventually
  support explicit mapping (`this root's "location" ← environment key
  "azure_region"`) affects whether Option C is designed in a way that permits it.
  Neither platform needs it today.
- **Establish the interface source for the other seven provisioner types.** Only
  `terraform` is introspected. `bicep` (`param` declarations) and `helm`
  (`values.schema.json`) look feasible; `script` cannot be, and needs the
  declaration branch.
- **Decide what `references` on a *provisioner* means afterwards.** Under Option C
  it is the declaration branch for non-introspectable types. Whether it keeps the
  same field name — given `references` already means two different things
  elsewhere (see the audit) — is unresolved, and one field with several meanings
  depending on where it sits is how the current confusion started.
- **Check Job 2 against any Job 1 change.** `dns`, `module` and `network` resolve
  `var:`/`secret:`/`feature:` fields through `spec.references` at build time, and
  `module` serves both jobs from one field. Every option here must be tested
  against those three kinds, not only against Terraform input validation.
- **Correct ADR-0078's factual basis.** Its "no binding exists" and "new schema
  surface required" statements are both wrong, and its deferred per-provisioner
  binding is cheaper than it records. That ADR should point here.
- **Confirm `STRATA_INJECTED_KEYS`.** There is an existing notion of keys strata
  supplies regardless; it is likely how structural keys avoid being flagged as
  undeclared, and it must be reconciled with any interface-driven rule.
- **Decide whether build should validate the union or the per-stage set.** Build
  checks secrets against the union of every stage bound to a provisioner; deploy
  injects only the running stage's list. Either build should validate per stage, or
  the discrepancy should be stated as intentional — today it is neither.
- **Decide the empty-allowlist fallback.** "No matching stages" yields all secrets
  while "matching stages declaring none" yields none. Whichever is right, the two
  should not disagree by accident.
- **Decide the zero-variable case.** `_validate_inputs()` currently does
  `if not module_vars: continue` — a root declaring no variables is skipped
  entirely. Under interface-driven injection that needs an explicit answer.
