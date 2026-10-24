# Environment Value Metadata — Work

- Status: **Archived (2026-10-24) — all four topics decided.** Requirements (topic 1) are built and documented in
  [dynamic-configuration-validation.md](../design/dynamic-configuration-validation.md). Alias, provenance and naming
  budgets (topics 2-4) were decided against a strata feature in
  [ADR-0044](../decisions/0044-environment-values-carry-no-metadata-fields.md); facts between layers (item 6) in
  [ADR-0045](../decisions/0045-facts-between-layers-are-terraform-records.md). Finding attribution is recorded as out
  of scope in the design doc. Kept as the investigation record (evidence, counts, conversion, rejected directions).
- Last updated: 2026-10-24
- Related: [dynamic-configuration-validation.md](../design/dynamic-configuration-validation.md)
  (`attributeconfig`: the mechanism topic 1 extended),
  [ADR-0040](../decisions/0040-attributeconfig-standalone-kind.md),
  [cross-document-value-references.md](../design/cross-document-value-references.md) (`${value:}`),
  [composite-variable-merge.md](../design/composite-variable-merge.md),
  [policy-engine-architecture.md](../work/policy-engine-architecture.md) (generic engine ruled out, 2026-10-07),
  [promotion.md](../work/promotion.md) (same consumer),
  [output-declarations.md](../design/output-declarations.md) and [ADR-0036](../decisions/0036-cross-run-key-value-output-store.md)
  (`${output:}`), [cross-run-step-output-delivery.md](../archive/cross-run-step-output-delivery.md) (archived)

## Overview

A `kind: environment` document says *what* a value is (key, store, value, type) but nothing *about* it.
Everything else a maintainer needs to know to edit it safely lives in YAML comments, where no tool can read it.
Found reading `cfg-int-deployment` (branch `v3`, still v1/v2-shaped config, not yet converted): its
`deploy/hubs/z00/s01/c0224/dev/environment.yaml` is 718 lines, and most of those lines are prose carrying four
kinds of metadata strata has no field for:

1. **Requirements** — a value is only valid in combination with another value, or must match a pattern.
2. **Alias / copy-of** — a value must equal another value, under a different key.
3. **Provenance** — where a value came from, when it was last checked, and whether it is final.
4. **Naming budgets** — a composed name must fit a length limit.

This doc exists to scope those four before any schema work. It is not in `docs/design/` because nothing is decided.
These are four separate problems that share one cause (a value has no metadata), and they may not share one solution.

## Evidence (cfg-int, 12 environment files, 2026-10-21)

Approximate counts of regex line matches over the 12 `*environment*.yaml` files, not hand-verified individually:

| Pattern                                                   | Lines |
| --------------------------------------------------------- | ----: |
| variable keys (`- key:`)                                  |   165 |
| blank constants (`value: ""`)                             |    19 |
| "duplicate of" / "keep identical"                         |    12 |
| value cited as confirmed/verified/read with a date        |    17 |
| `PLACEHOLDER` / "pending" / "Blank —"                     |    10 |
| naming-length arithmetic (`characters`, `24-char`, `cap`) |    11 |
| an `az ...` command cited as the source of a value        |    11 |

Specific cases worth keeping in view:

- **Requirement that already caused a defect.** `vnet_private_endpoints_subnet_name` was left blank. The Dispatcher
  estate gates its private endpoints on `vnet_name != "" && vnet_private_endpoints_subnet_name != ""`, so none were
  created, silently, while the matching DNS zone values were already real. Found by reading Terraform (2026-09-29),
  not by any check. Other instances: `aks_cluster_id` may be blank only while `enable_aks` is false; the four `sci_*`
  and `aci_subnet_name` blanks are valid only while SCI is descoped.
- **Alias.** `vnet_default_subnet_name`, `vnet_private_endpoints_subnet_name` and `vnet_event_grid_subnet_name` repeat
  `web_apps_subnet_name`/`private_endpoints_subnet_name` under the names the forked Terraform roots declare;
  `log_workspace_daily_quota_gb` repeats `daily_quota_gb`. Each comment says "keep the two values identical".
  Nothing enforces it. The cause is structural: every key is delivered to every provisioner under its own name, and a
  forked root cannot be renamed to match. (That is v1's flat delivery; v2 does not have it. See topic 2.)
- **Provenance.** Values such as `api_management_id`, `vnet_id` and `agw_subnet_id` are copied from applied upstream
  layers on purpose (the audit trail requires the value to be visible in the reviewed diff; App Configuration is
  private-endpoint-only). Each carries "confirmed via `az apim show`, 2026-09-23" or similar, plus "re-read if the VNet
  is recreated". `dispatcher_product_name` is "PLACEHOLDER pending a naming decision ... changing it later forces a
  destroy/recreate".
- **Naming budget.** "dev + intdsp + c0224dev2 + kv = 20 characters. Four to spare. Re-check that sum before changing
  any of the three." Key Vault caps at 24; node pool names at 12; tenant codes are held to 5 (AKS pool budget).

## What exists in v2 today

| Need         | Closest existing mechanism                                                                                                                   | Gap                                                                                                                                                                                                                                                                                                                                                   |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Requirements | `attributeconfig`: JSON Schema 2020-12 (`pattern`, `required`, `if`/`then`, `dependentRequired`, `enum`) pushed onto every matching document | *Was (2026-10-21):* only `properties`/`custom`/`configuration`, one document at a time. **Since 2026-10-22/23:** also `variables`/`features`, an `effective` view over the merged deployment, and cross-block rules (`block: composite`) — see topic 1. Still out of reach: value equality between fields, and resolved (non-constant) store values.  |
| Requirements | `VariableStoreModel` validators                                                                                                              | Only `type`/`value` consistency and `default`/`field` placement. No pattern, no cross-key rule.                                                                                                                                                                                                                                                       |
| Alias        | `${value:kind.name.path}` (resolved at validate and deploy)                                                                                  | Addresses another *document's* field. A list segment is matched by a `"name"` key, but variables are keyed by `key`. **Tested 2026-10-21:** by `key` fails (`value_reference_invalid_path`); by list position (`variables.1.value`) validates and resolves, but position is fragile and the token addresses one document, not the merged environment. |
| Alias        | `${var:KEY}` inside another variable's `value`                                                                                               | **Tested 2026-10-21: does not work.** Before the fix `strata validate` accepted it (0 errors), `build run` emitted the literal token into tfvars, and `deploy run` failed with `value_resolution_failed`; a `store: constant` value is only resolved against `${value:}` references. `validate` now rejects it (`store_value_token_unsupported`).     |
| Provenance   | `meta.annotations` (free-form, per document); `kind: version` pins carry a rationale ([ADR-0019](../decisions/0019-version-pinning.md))      | Per document, not per key. Nothing structured, nothing queryable, nothing that can go stale.                                                                                                                                                                                                                                                          |
| Naming       | `attributeconfig` `maxLength` / `pattern` on a single `properties` value                                                                     | A budget applies to a name *composed* from several keys plus a literal suffix, so it is a computed length across keys, which a per-value schema cannot state.                                                                                                                                                                                         |

Constraints from earlier decisions that any design here has to respect:

- **No generic policy engine** (policy-engine-architecture.md, 2026-10-07: "bespoke, indefinitely"). A general
  constraint language over environment values drifts toward exactly that. `attributeconfig` was accepted as a
  deliberate, narrow exception; an extension needs the same justification, not an assumption.
- **Push, not pull** (ADR-0040): a rule that has to be opted into by each leaf protects nothing already written.
  Requirements about a *key* belong where the key is declared, not on each of the many leaves that set it.
- **Evidence over assumption**: nothing here is built until a real defect or a counted recurring cost justifies it.

## Per-topic notes

### 1. Requirements (regex, value combinations)

Two distinct sub-problems:

- *Single-value shape* (pattern, enum, non-empty). Reachable today if the value lives in `properties`, which is what
  composite-variable-merge already recommends for composed values, but only per document, not on the merged view (see the
  design below). Not reachable for `variables`/`features`.
- *Cross-key combinations* ("when `enable_aks` is true, `aks_cluster_id` is non-empty", "these two keys are equal").
  Not expressible by any existing mechanism, and the real defect above is of this kind.

There are two evaluation points, and they differ. `_check_deployment_value_tokens()` in `semantic_checks.py` walks each
deployment's **merged** environment, so the merged data is available there. `_check_attributeconfigs()` instead
evaluates each **document on its own** (tested 2026-10-21: a schema requiring a property fails a layer that lacks it,
even when another layer in the same deployment sets it). Any `required` rule on `properties` therefore already
misfires on layered environments.

#### `attributeconfig` over the merged `properties`/`custom` — DONE, now in docs/design/

Built as a 5-phase plan (pair validation at load, `target.view` field, a layering-cycle-avoiding refactor, the
`effective`-view evaluation loop, docs) — all five shipped 2026-10-22, full check suite clean at every phase
(mypy/ruff/lint-imports/pytest, 3025 tests at completion). The mechanism, final shape, and evaluation semantics are
now documented in [dynamic-configuration-validation.md](../design/dynamic-configuration-validation.md)'s "Scope" and
"Evaluation semantics" sections (`target.view: document | effective`) and its History — not repeated here, since a
design doc reflects finished behavior and this doc shouldn't maintain two descriptions of the same thing. The
real defect this closes: a value blank at one leaf but filled in by a tenant/workspace default above it reads as
genuinely missing to a `required` schema checking one document in isolation; `view: effective` checks the deployment's
actual merged, deployable result instead.

**Tests (cross-cutting, beyond each phase's own list)**

- Regression: `azure-attributes.yaml` unchanged; `config/`, `.v2-haven` and `.v2-cfg` still validate with the same
  counts after every phase.

**Open questions**

- ~~Should `effective` become the default for `properties`/`custom` on the layered kinds?~~ **Decided 2026-10-24:
  neither view is the default; `target.view` becomes a required field.** `document` and `effective` check different
  data, and the wrong one fails silently or misfires, so the author should always say which. Breaking, but strata-v2
  is pre-1.0 and ADR-0035 set the precedent of a clean break over a compatibility mode. Impact, counted 2026-10-24:
  3 schemas in `config/registries/azure-attributes.yaml`, 2 examples in `docs/config/attributeconfig.md`, 1 in the
  design doc, and test fixtures (14 YAML schemas in `test_semantic_checks.py` plus `_minimal_attributeconfig()` in
  the model tests); `.v2-cfg` and `.v2-haven` have none.
  **Design:** drop the default from `AttributeConfigTargetModel.view`; Pydantic then rejects a missing `view` at
  load with its standard "Field required" error at `spec.target.view`. No evaluator change: `_check_attributeconfigs()`
  already branches on the value. The `composite`/`effective` validators are unchanged (they run after field
  validation, so they always see a declared view).
  **Plan (one phase):** model field + description; `view: document` on the 3 `config/` schemas; every test fixture
  declares `view`; a model test that a missing `view` is rejected; `docs/config/attributeconfig.md`,
  `docs/config/resource.md` and the design doc's document shape, evaluation semantics and History; full check suite
  and `config/` validate. **Built 2026-10-24** as planned; 3061 tests passing, `config/` validates (32 documents).
- Attributing a finding to the layer that set the value.
- ~~The cross-block extension (`variables`/`features` composed with `properties`)~~ — built: same-block
  `variables`/`features` (2026-10-22) and cross-block conditional rules as `block: composite` (2026-10-23), both below.

#### `attributeconfig` over `variables`/`features` — DONE, now in docs/design/

Built as a 4-phase plan (widen `block` + fix the view/table coupling, reshape + document view, effective view,
docs) — all four shipped 2026-10-22, full check suite clean at every phase (mypy/ruff/lint-imports/pytest, 3045
tests at completion). The mechanism, final shape, representation, and the secrets-exclusion rationale are now
documented in [dynamic-configuration-validation.md](../design/dynamic-configuration-validation.md)'s "Scope",
"Evaluation semantics" and "Known limits" sections, and its History — not repeated here. Covers the same-block half
of topic 1's "Requirements" problem for `variables`/`features`, including the real recorded defect
(`vnet_private_endpoints_subnet_name` blank while `vnet_name` was set — reproduced and caught end-to-end). The
cross-block case (a `features` key gating a `variables` key) was built afterwards as `block: composite`, below.

#### Cross-block conditional rules (`block: composite`) — DONE, now in docs/design/

Built as a 3-phase plan (model + evaluator, conflict check, docs) — all three shipped 2026-10-23, full check suite
clean at every phase (mypy/ruff/lint-imports/pytest, 3060 tests at completion). The shape, evaluation rules,
conflict-check change and the declared-vs-resolved limitation are documented in
[dynamic-configuration-validation.md](../design/dynamic-configuration-validation.md)'s "Evaluation semantics" and
"Known limits" sections and its History — not repeated here. Covers the two recorded cross-block cases
(`enable_aks` gating `aks_cluster_id`, `enable_sci` gating four `sci_*`/`aci_subnet_name` variables), both verified
against `E:\Sources\cfg-int-deployment` and reproduced end-to-end against a base + leaf environment chain. Value
equality between fields remains out of scope (topic 2; design doc "Known limits" #4).

### 2. Alias or copy-of — analysed 2026-10-24: no strata feature needed

**What the duplicates actually are** (re-checked in `E:\Sources\cfg-int-deployment` and the product repo). The
Evidence table's "12" counted matching *lines*, including generated `build/` output and wrapped descriptions. In the
source there are **4 duplicate keys, all in the c0224/dev leaf**:

| Duplicate key                        | Copy of                         | Variable declared by                                                                                             |
| ------------------------------------ | ------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `vnet_default_subnet_name`           | `web_apps_subnet_name`          | Dispatcher root (`deploy_dsp/terraform`) and DataHub root (`deploy/terraform`), both in `IntegrationApi.DataHub` |
| `vnet_private_endpoints_subnet_name` | `private_endpoints_subnet_name` | Dispatcher root                                                                                                  |
| `vnet_event_grid_subnet_name`        | `private_endpoints_subnet_name` | DataHub root                                                                                                     |
| `log_workspace_daily_quota_gb`       | `daily_quota_gb`                | DataHub and Dispatcher roots                                                                                     |

The originals (`web_apps_subnet_name`, `private_endpoints_subnet_name`, `daily_quota_gb`) are the names cfg-int's own
ring root declares.

**Why they exist.** One environment feeds three Terraform roots (ring, DataHub, Dispatcher) that name the same thing
differently, and two of them live in another repository. v1 delivered every environment key as a flat Terraform
variable of the same name, so the only way to give a root an input under its own name was a second key with the same
value. The duplication is a naming difference at the consumer boundary, not a configuration need.

**Merge strategies do not help.** All four pairs sit in one document, in one layer. A merge strategy decides which
layer's value wins *for the same key*; here one value is needed *under two names* by two consumers. No merge renames.

**v2 already removes the cause.** ADR-0035 replaced flat per-key variables with one `strata` object: a root reads
`local.strata.variables.<key>`, and there are no flat variables left to match by name. ADR-0035 also already requires
every consumer root to be rewritten to read `local.strata.*`. During that rewrite a product root maps the value to
its own name in its own `locals` (e.g. `vnet_default_subnet_name = local.strata.variables.web_apps_subnet_name`), so
the rename lives in HCL, next to the code that has the different name, and the four duplicate keys are deleted. If a
product root cannot be changed, a thin wrapper root in cfg-int calls it as a module with renamed arguments; still HCL,
still no strata feature.

**Decision: no alias field, no copy-of assertion, no merge strategy.** Reopen only if a duplicate turns up that is not
a consumer-naming difference (two keys that must be equal by policy). None is recorded. Not yet verified end to end:
`.v2-cfg` does not include the product estates, so the HCL mapping has not been tried. Open item 3's conversion
(2026-10-24) confirmed the config side (4 duplicates to 0); the consumer side still needs the fork roots.

### 3. Structured provenance — decided 2026-10-24: no strata feature

Candidate facts that exist today only as prose: origin (an upstream output, a `az` query, a decision), the date it was
last checked, and a status (`pinned`, `placeholder`, `derived`). The payoff is queries and staleness, not validation:
list every placeholder in a deployment, list everything unchecked for N days, point at the upstream output a pinned
value should still equal.

Open questions:

- Per-key field on the variable, or a separate sidecar so the 700-line file does not grow further?
- Is it emitted anywhere? It should not reach Terraform or the tfvars projection.
- Relationship to the audit trail ([audit-trail.md](../work/audit-trail.md)): that records what a *deploy* did. This records
  where a *config value* came from. Related, but the two should not be merged.
- If a pinned value has a recorded origin, a drift check against the upstream output becomes possible. That is a
  separate feature layered on this one, and cfg-int rules out resolving such values at deploy time, so it would have
  to be an explicit check or a refresh command that produces a diff.

**Re-counted 2026-10-24 against the v3-shaped config** (`.v2-cfg`, 83 YAML files, 5286 lines, excluding the converted
v1 leaf). 239 lines match the provenance patterns (a date, "copied", "confirmed", "verified", `az`, `terraform
output`, placeholder/pending). A rough first-match classification, enough to see the shape:

| Kind                                                                                                                 | Lines | Where it goes                                                                                                                                                                                                                                        |
| -------------------------------------------------------------------------------------------------------------------- | ----: | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Change history ("Added 2026-10-07 to unblock...", "apply failed")                                                    |  ~41+ | Not value metadata: git history and the consumer's ADRs. Most of the 107 "other" lines are continuation lines of these narratives.                                                                                                                   |
| Subscription onboarding facts (tenant, subscription, SPN, state storage, service connection, hub VNet, project code) |   ~44 | cfg-int's `scripts/New-BootstrapEnv.ps1` renders them into a generated environment file, reading Azure with `-Discover` and printing each value's source (given, azure, convention); the file is never hand-edited, and re-rendering is the refresh. |
| A layer's outputs copied by hand (`terraform output`, "read from Azure", the ring's own first apply)                 |   ~21 | Leaves the config through layer records (item 6). Still hand-copied today only where that layer has not switched over yet (ring, products).                                                                                                          |
| Pins and placeholders                                                                                                |   ~21 | Almost all are explanations of *why* a blank base default is fine. Genuinely pending: 2 image tags ("smoke-test value, NOT a stable pin") and one `application_id` "not recorded".                                                                   |
| `az` query cited as source                                                                                           |     5 | The onboarding generator or a layer record, as above.                                                                                                                                                                                                |

**Decision: no strata feature.** Every kind of provenance that matters already has an owner that is better placed
than a strata field: generated onboarding facts carry their source in the generator and are refreshed by
re-rendering; layer outputs carry `schema`/`layer` and the vault's own version timestamp, and staleness shows up as a
plan diff; history belongs in git. The image tags are exactly what `kind: version` already structures (ADR-0019:
`status: unverified` requires a `reason`, plus `reviewed`), so they need a pin, not a new field. That leaves one
"not recorded" placeholder, which a comment handles. No defect has been traced to a stale or unsourced value. Reopen
if one is, or if placeholders start reaching production deployments.

### 4. Naming budgets — decided 2026-10-24: no strata feature

Narrow by design: *length* (and the charset that goes with it), not naming. Composing names is Terraform's job:
cfg-int's `docs/design/apim-name-primitive.md` (draft, 2026-10-19) puts the rules in a `primitives/` lookup that fails at
plan time, and deliberately avoids a second rule set. The real question here is only whether strata should catch an
over-length composed name earlier, at `strata validate` on the pull request, instead of at plan.

- If yes, strata would need the same inputs the primitive uses (the parts, the literal suffix, the limit), which
  duplicates the rule and has to be kept consistent with it.
- If no, strata's contribution shrinks to the single-value shape checks in topic 1 (the closed `environment_name` set,
  the tenant-code pattern and its 5-character limit), which are already in reach.

Related, already tracked: [subnet-cidr-derivation.md](../work/subnet-cidr-derivation.md) reached the same "strata carries the
request, Terraform computes" split for CIDRs.

**Decision: no.** Terraform composes the names, so Terraform is where the length rule belongs; a strata copy of the
composition would be a second rule set that can drift from the first. Item 3's conversion left only 2 budget notes
(the Key Vault 24-character sum, the 12-character node pool key), and Azure/Terraform already reject both at plan.
Single-value shape checks (`environment_name` set, tenant-code pattern and length) stay available through
`attributeconfig`. Reopen if an over-length name gets past plan, or a failed apply is recorded that a PR-time check
would have caught.

### Adjacent: an upstream step's output as a downstream step's input

Same hand-copy symptom as the alias topic, different cause, so it is kept out of the four above. cfg-int's product
estates need the ring's resource group, key vault and subnet facts. `.v2-cfg` copies them in by hand
(`stacks/ring/environment-datahubapi.yaml`, the `ring_*` properties) even though the estate's execution step already
`depends_on` the ring's.

**Where `${output:step.key}` resolves today** (checked in `deploy_controller.py` and its tests, 2026-10-21):

- `provisioner.backend.configuration`.
- A dns, network or firewall document that an execution step claims through `targets` (gap #12).
- A module reached through a targeted namespace (Helm and Compose values; gap #16).
- Everywhere else it is a `deploy run` error ("not supported outside dns/networks/firewalls"), including `properties`,
  `custom`, `variables`, `tenant` and resource `configuration`. An unclaimed dns/network/firewall document is rejected too.

**Why it was never opened up** (recorded decisions, not new analysis):

- Output visibility is per step and dependency-scoped, so a token needs an owning step to scope against. dns, network
  and firewall documents had none until a step could claim them ([gap_fit_v1.md](../work/gap_fit_v1.md) gap #12).
- `properties`, `custom` and `tenant` are workspace-wide singletons with no name to put in `targets`, so no owner is
  possible. Per-name ownership for topologies and resources was deferred for "zero evidenced need", "not forgotten,
  should real evidence ever surface" ([value-token-resolution.md](../design/value-token-resolution.md), gap #8/#9).
  ADR-0035 kept the three-category limit when the projection was unified.
- v1's `inputs_from` data mapping (which output becomes which input) was deferred in
  [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md) until a Context existed.

**What the archived cross-run doc did and did not do.** It made outputs *available* across separate `deploy run`
invocations (`DeploymentOutputStore`, ADR-0036). It did not change *where* the token may be written, so the limit
above still applies after it.

**cfg-int status (maintainer, 2026-10-21):** worked around for now by keeping the values in the existing Key Vault;
expected to be fixed properly later. Not verified against the cfg-int repo from here. Open question: how this squares
with the "value visible in the reviewed diff" stance recorded under Provenance, since a Key Vault read is a runtime
lookup.

**Re-checked 2026-10-24 against the real repos: solved on the consumer side, no strata capability needed.**

- *No other strata doc covers this.* The archived cross-run doc and ADR-0036 cover `${output:}` availability across
  runs of **one** deployment; `output-declarations.md` checks `${output:}` keys; `key-value-store-integration.md` is
  the store backends. This section is the only place the question lives.
- *The real problem is across deployments, not across steps.* In cfg-int v3, bootstrap, spoke, ingress, customer and
  ring are separate deployments. `${output:}` resolves only within one deployment's graph, so none of the candidate
  directions above (step-level input mapping, widening claiming) would have reached it. cfg-int's own alternatives
  table rejects `${output:}` for exactly this reason.
- *What cfg-int built instead* (`docs/design/bootstrap-config-layering.md`, decided 2026-10-21, untracked files in
  `E:\Sources\cfg-int-deployment`; iac-int `v3`, `components/layer-record`). Each layer's own Terraform writes one
  JSON secret, `<layer>-outputs` (`schema`, `layer`, then IDs/names/addresses only), into the per-subscription
  Terraform-state Key Vault that was bootstrapped at onboarding, in the same apply. The next layer reads it at plan
  time through `layer-record`, which fails the plan if the secret is not JSON, has an unknown `schema`, names another
  `layer`, or lacks a required key. Strata only carries `properties.state_vault`. Built for bootstrap, spoke, ingress
  and customer (mock-tested); the ring layer and the first real read against a vault are still open.
- *It answers the audit question above.* The record is read at plan time, so a re-applied upstream layer shows up as a
  plan diff in the reader, which is the reviewed artifact; nothing goes stale in the config.
- *Why strata should not duplicate it.* The record is published if and only if the apply succeeded, versioned
  atomically, schema-checked, and works for a root run without strata. A strata mechanism would sit outside the apply
  and could not offer any of those. Same split as subnet-cidr-derivation.md: strata carries the request (where the
  vault is), Terraform moves the facts. (It also does not need ADR-0036's CAS: one writer per record.)
- **Decision: no strata capability.** Reopen if a non-Terraform consumer (Helm, Compose, a script) needs another
  deployment's facts, or if the layer-record approach fails its first real run.

Candidate directions considered before the 2026-10-24 re-check (superseded by the decision above; kept for the
record):

- *Step-level input mapping*: the consuming step declares which upstream output feeds which key. Avoids the
  singleton-owner problem and reuses `depends_on` ordering; new schema on the step.
- *Snapshot or refresh command*: write the upstream output into the leaf as a constant. Keeps the audit-diff property
  and gives provenance (topic 3) a natural origin; still a manual step.
- *Widen claiming to more categories*: smallest change for topologies and resources, but cannot reach the singleton
  categories, which is where cfg-int's values live.

## Remaining Work / Open Questions

Evidence first, per repo convention; nothing below is a commitment.

1. ~~Verify the two alias rows against the real code~~ — done 2026-10-21, against a scratch copy of `config/` with
   venv strata 2.0.0a26. Results are in the Alias rows. It also surfaced two defects independent of this doc's
   direction, both fixed the same day: `validate` accepted a `${var:KEY}` in a variable value that `deploy run` then
   rejected (now `store_value_token_unsupported` at validate), and `values get` reported "resolved 4 of 3 keys"
   because the count included the internal `${value:}` entries.
2. ~~Confirm that `attributeconfig` really cannot reach `variables`/`features`~~ — done 2026-10-21, it cannot, and the
   limit is in the code, not only the design doc: `AttributeConfigTargetModel.block` is
   `Literal["properties", "custom", "configuration"]`. What an extension would take, from reading
   `_check_attributeconfigs()`:
   - Widen the `block` literal. The evaluator does `getattr(spec, block)`, which for `variables`/`features` is a list of
     Pydantic objects, not JSON, so it would need a `model_dump` and, to be usable by `required`/`properties`, a
     reshape into a map keyed by `key`. The `or {}` default is wrong for a list.
   - Evaluate the merged view per deployment, not per document (see topic 1), which is a second loop alongside the
     per-document one.
   - A rule across blocks (feature on, so variable non-empty) needs a composed object (`variables`, `features`,
     `properties` together) as the thing a schema is evaluated against; one block per schema cannot do it. With that,
     JSON Schema `if`/`then` can express it.
   - Still out of reach: "these two keys are equal". JSON Schema has no cross-property equality, so copy-of (topic 2)
     stays a separate mechanism whichever way topic 1 goes.
3. ~~Convert one real cfg-int leaf (c0224/dev) to v2 and recount~~ — done 2026-10-24, in
   `.v2-cfg/stacks/ring/dev/` (local fixture, gitignored): `environment.yaml` (the converted leaf), `deployment.yaml`
   (`ring-dev-c0224`, extends `ring_deployment_base`, `tenant: c0224`) and `attributes.yaml` (three
   `block: composite` rules). Note this is the v1 tree's leaf (sub-zone0-c0224-004, Dispatcher + DataHub forks), not
   the different c0224 ring already in `.v2-cfg/deploy/hubs/z00/s01/c0224/dev` (sub-zone0-c0224-003). `.v2-cfg`
   validates with 87 documents (82 + 5) and the same 24 warnings.

   **Conversion rules.** Values unchanged. Blocks unchanged except where the v2 merge chain forced it: `ring` moved
   to `properties` (the ring base declares it there); `customer_code` dropped (the tenant's `c0224-env` supplies
   it); `spoke` dropped (it only fed v1's templated backend key, now an explicit `tf_state_key`); the four duplicates
   dropped (topic 2), with a note on the original key saying which root renames it in HCL. `daily_quota_gb` added,
   since v1 carried it in `stacks/core` and `.v2-cfg`'s `core-env` does not. Comments kept only if they still carry
   something strata cannot hold.

   | Pattern (same regexes as the Evidence table)                                | v1 leaf | v2 leaf |
   | --------------------------------------------------------------------------- | ------: | ------: |
   | Lines                                                                       |     779 |     338 |
   | Comment lines                                                               |     465 |      51 |
   | Variable + feature keys                                                     |      57 |      53 |
   | Blank constants                                                             |       5 |       5 |
   | "Duplicate of" / "keep identical"                                           |       4 |       0 |
   | Lines with a date                                                           |      30 |      13 |
   | PLACEHOLDER / pending / Blank                                               |      17 |       9 |
   | Naming-length arithmetic                                                    |       9 |       3 |
   | `az ...` cited as source                                                    |      11 |       5 |
   | v1 mechanics (broadcast, `inputs_from`, undeclared-input check, strata 1.x) |      11 |       0 |

   **What the numbers mean, per topic:**

   - *Most of the prose was not metadata.* About 90% of the comment lines were v1 mechanics (per-key broadcast,
     `inputs_from`, the undeclared-input check), change history, or design rationale that belongs in the consumer
     repo's ADRs. v2 removes the mechanics outright.
   - *Topic 1 (requirements):* all three recorded requirements are now checks, not prose — `enable_aks` needs
     `aks_cluster_id` and a non-empty `worker_pools`; `enable_sci` needs its four inputs; `enable_dispatcher_api` needs
     `vnet_name` and `private_endpoints_subnet_name`. Proven against a scratch copy with the values broken: all 7
     expected findings fire, none on the other four ring deployments. The 5 blanks remain, but each is now guarded
     by a rule instead of a comment. The recorded defect itself was on the duplicate key
     (`vnet_private_endpoints_subnet_name` blank while `private_endpoints_subnet_name` was set), so dropping the
     duplicate removes that exact failure; the rule still guards the remaining single key.
   - *Topic 2 (duplicates):* 4 to 0. Not verified on the consumer side: the Dispatcher and DataHub fork roots are not
     in `.v2-cfg` (its `datahubapi_infra` is a later, rewritten root), so the HCL renames are still untried.
   - *Topic 3 (provenance):* the largest survivor. 8 provenance notes remain, covering 17 of the 47 variables, and
     almost all are the same shape: a value copied by hand from an applied upstream layer's output (spoke VNet,
     subnets, AGW, APIM) or an `az` query, with a date and "re-read if X is recreated". That is the adjacent
     upstream-output problem (item 6) seen from the other side; a snapshot/refresh command would answer both.
     Two placeholders remain (`application_id`, `dispatcher_product_name`). **Corrected 2026-10-24:** this overstates
     topic 3. The converted leaf is the v1 tree; in cfg-int v3 those upstream values come from layer records read by
     Terraform (item 6), so they leave the config entirely. What topic 3 still has is the placeholders and the few
     values sourced from an `az` query rather than from a layer.
   - *Topic 4 (naming budgets):* 2 notes remain: the Key Vault 24-character sum across four keys (two estates) and
     the 12-character node pool key. Small, and both are already enforced by Azure/Terraform at plan.
4. ~~Decide the scope question for topic 1's `properties`/`custom` half~~ — resolved by building it: `target.view:
   effective` (2026-10-22, see topic 1) covers the layered-blob half without waiting on the `variables`/`features`
   question. ~~What's still open is only the `variables`/`features` half~~ — the same-block half of that (single-value
   shape + same-block cross-key, including the recorded real defect) is now designed (2026-10-22, see topic 1's
   second design) via the same `attributeconfig`/`view: effective` mechanism, widened rather than a new rule type.
   Only the cross-block half (a feature gating a variable) remains undecided — still a dedicated rule, or a later
   composed-object widening of the same mechanism. **Decided 2026-10-23:** the composed-object widening
   (`block: composite`, see topic 1).
5. ~~Decide whether naming budgets belong in strata at all (topic 4)~~ — decided 2026-10-24: no, Terraform composes
   the names and enforces the limits; see topic 4.
6. ~~Decide whether, and how, an upstream step's output reaches a downstream step's inputs~~ — decided 2026-10-24:
   no strata capability. cfg-int v3 passes facts between layers (separate deployments) as Terraform-written records
   in the bootstrapped state Key Vault; see the adjacent section.
7. ~~Only then pick a first slice~~ — picked: cross-key requirements (topic 1), on the strength of the recorded defect
   (blank value that silently disabled private endpoints) — now built for `properties`/`custom`, `variables`/
   `features`, and cross-block conditional rules (`composite`). Topics 2, 3 and 4 and item 6 are closed with no strata
   feature. Only topic 1's finding attribution remains open, with no evidence of need yet.

## Changelog

- 2026-10-21: created from a read of `cfg-int-deployment`'s `v3` branch (unchanged from `main`; only the uncommitted
  APIM naming-primitive draft differs). Counts in the Evidence table taken the same day.
- 2026-10-21: open item 1 tested. `${var:KEY}` in a variable value is accepted by `validate` but fails at resolution;
  `${value:}` addresses a variable by position only, not by `key`.
- 2026-10-21: fixed the two defects that test found. `validate` now rejects a `var`/`secret`/`feature`/`output` token
  in a store's own `value` (`EnvironmentService.validate_store_value_tokens()`), so the alias gap fails early instead of
  at deploy; `values get` counts only the requested keys. Aliasing itself is still unsupported and still open (topic 2).
- 2026-10-21: added the adjacent section on upstream outputs. Corrects an earlier reading that `${output:}` resolves in
  two places: it also resolves in modules reached through a targeted namespace. Records why the three-place limit
  exists, that the archived cross-run doc covers availability and not placement, and the current Key Vault workaround.
- 2026-10-21: open item 2 tested. `attributeconfig` rejects `block: variables`/`features` at load, and evaluates each
  document on its own rather than the merged environment. Recorded what an extension would take; copy-of is out of
  reach of JSON Schema either way.
- 2026-10-22: tested `attributeconfig` on `environment.properties`/`custom` (both work, per document) and on a
  kind/block mismatch (`resource.properties` silently evaluates `{}`). Drafted a design for validating the pair at load
  and an opt-in `view: effective` over the merged `properties`/`custom`.
- 2026-10-22: fleshed the design sketch out into a 5-phase Implementation Plan (pair validation at load, `target.view`
  field, moving `merge_workspace_environment_deployment_properties()` to `environment_service.py` to avoid a layering
  cycle, the `effective`-view evaluation loop, docs). Verified the `(kind, block)` table directly against every
  top-level `*SpecModel` class's real fields (not just the earlier `model_fields` skim) — confirms nested sub-models
  that happen to share a field name (`DnsZoneModel.custom`, `FirewallRuleModel.custom`, `WorkspaceResourceModel.
  configuration`) don't leak into the top-level pair, and that `dns`/`network` genuinely have none at the top level.
- 2026-10-22: Phase 1 implemented — `BLOCKS_BY_KIND` + `AttributeConfigTargetModel.validate_block_exists_on_kind()`
  (`attributeconfig_model.py`), 10 new tests including a pinning test against every real `*SpecModel` class. Full
  check suite clean; `config/`'s own `resource.configuration` schemas unaffected.
- 2026-10-22: Phase 2 implemented — `AttributeConfigTargetModel.view` (default `document`) +
  `validate_effective_view_is_deployment_properties_or_custom()`. 4 new tests. Full check suite clean (3018 passed);
  `config/` unchanged. Phases 3-5 remain undecided/unbuilt — Phase 4 (the actual `effective` evaluation) has nothing
  to run against yet, this phase only lets a schema author declare the intent.
- 2026-10-22: Phase 3 implemented — `merge_workspace_environment_deployment_properties()` moved from
  `value_controller.py` to `environment_service.py`; `build_controller.py`/`deploy_controller.py` updated to import
  it from there; its tests moved from `test_value_controller.py` to `test_services_environment.py`. Pure refactor,
  same 3018 tests. Unblocks Phase 4 (the evaluation loop can now import the merge from `services` without recreating
  `value_references.py`'s documented controller-layer cycle). Phases 4-5 remain undecided/unbuilt.
- 2026-10-22: Phase 4 implemented — `_check_attributeconfig_effective_view()` in `semantic_checks.py`, evaluating a
  `view: effective` schema against each deployable deployment's merged `properties`/`custom`. Confirmed against a real
  layered scenario (a tenant-default `owner` invisible under `document`, visible under `effective` — the exact defect
  pattern this whole doc started from). 9 new tests, full check suite clean (3025 passed). Only Phase 5 (docs) left;
  the three "Open questions" under topic 1 (default view, provenance attribution, the `variables`/`features`
  extension) are unaffected by this plan and remain genuinely open.
- 2026-10-22: Phase 5 implemented — graduated `target.view`/`BLOCKS_BY_KIND` into
  [dynamic-configuration-validation.md](../design/dynamic-configuration-validation.md)'s "Document shape", "Scope" and
  "Evaluation semantics" sections plus a new `History` entry, then collapsed this doc's topic-1 design-sketch and
  5-phase plan down to a short pointer at the design doc (per `docs/work/README.md`'s "superseded content doesn't stay
  duplicated" convention) — kept the "Open questions" list, since those are still genuinely unresolved. The 5-phase
  plan itself is closed; topic 1's remaining open items (3, 4 partially, 7) and topics 2-4 are untouched by it.
- 2026-10-22: designed the `variables`/`features` extension (same-block half only). Key decisions: secrets excluded
  deliberately (a failing schema's message can echo the offending value, and a `store: constant` secret's `value` IS
  the literal secret — a real leak path, not just unaddressed scope); representation is the full
  `store.model_dump(mode="json", by_alias=True)` per key, not just `.value` (both are typed `Any`, confirmed against
  the real models, so a rule may need `store`/`type` alongside `value`); the effective-view merge for these two blocks
  reuses the already-existing `merge_environment_models()` (no workspace/deployment layer — confirmed `variables`/
  `secrets`/`features` exist ONLY on `EnvironmentSpecModel`, nowhere else). Surfaced a real contradiction in the
  shipped Phase 1/2 validators that only this extension exposes: `validate_block_exists_on_kind()`'s "does `target.
  kind`'s own spec have this field" question is simply false for `kind: deployment, block: variables` (deployment has
  no such field at all — the effective merge source is a different kind than the target), so that validator now needs
  to apply only to `view: document`. 4-phase implementation plan written (widen block + fix the validator coupling;
  reshape + document view; effective view; docs). Nothing built yet — design only, per explicit instruction this
  time ("create the design", not "implement").
- 2026-10-22: Phase 1 of the `variables`/`features` extension implemented — model widened, validator coupling fixed
  (the design gap). Verifying it end-to-end (not just at the model layer) found a real, now-reachable crash in the
  already-shipped Phase 4 evaluator (`deep_merge()` on a `list[VariableStoreModel]`) — guarded with a clean
  "not yet implemented" diagnostic plus a regression test reproducing the exact crash. 7 new tests total (6 model, 1
  semantic_checks). Full check suite clean (3032 passed); `config/` unaffected. Phases 2-4 of this extension remain
  unbuilt.
- 2026-10-22: Phase 2 of the `variables`/`features` extension implemented — `reshape_block_value()` (new, in
  `attributeconfig_evaluator.py`) + `_check_attributeconfig_document_view()` wired to call it. `document`-view
  schemas on `variables`/`features` are now actually usable (previously accepted by the model but never evaluated
  meaningfully). 10 new tests (6 unit, 4 integration). Full check suite clean (3042 passed); `config/` unaffected.
  Phase 3 (the `effective`-view merge — what currently just reports "not yet implemented") is the remaining piece.
- 2026-10-22: Phase 3 of the `variables`/`features` extension implemented — `_merged_effective_value()` (new) branches
  the merge by block; the Phase 1 crash-guard is gone, superseded by real evaluation. Reproduced the real recorded
  defect's exact shape end-to-end (fails when it should, passes when the condition doesn't apply, survives a
  multi-environment merge). Verified the real diagnostic code via a standalone repro before writing the test
  assertion (`attributeconfig_validation_error`, not `attributeconfig_field_missing` — the key is declared, just
  blank, so `minLength` fails, not `required`). 4 new tests replacing the 1 now-moot crash-guard test. Full check
  suite clean (3045 passed); `config/` unaffected. Only Phase 4 (docs) remains for this extension.
- 2026-10-22: Phase 4 (docs) of the `variables`/`features` extension implemented — graduated into
  [dynamic-configuration-validation.md](../design/dynamic-configuration-validation.md)'s "Document shape" (block list
  grows to five), "Scope" (the secrets-exclusion security rationale, moved here verbatim from this doc), "Evaluation
  semantics" (effective-view merge source differs by block), "Known limits" (cross-block and alias/copy-of now listed
  explicitly), and a new `History` entry. Collapsed this doc's variables/features design + 4-phase plan down to a
  short pointer, same treatment the `properties`/`custom` design got in Phase 5 of its own plan. Both attributeconfig
  sub-designs under topic 1 are now fully built and documented — nothing left to implement under topic 1 except the
  cross-block case and the two genuinely open questions (default view, provenance attribution).
- 2026-10-23: designed cross-block conditional rules as `block: composite` (all four merged blocks in one object,
  `view: effective` only, `secrets` excluded). Re-verified the evidence directly in `E:\Sources\cfg-int-deployment`
  rather than relying on the earlier summary: `enable_aks`/`enable_sci` are features, their dependents are variables,
  the base stack defaults `enable_aks` to a boolean `true` that a leaf overrides, and Terraform already enforces the
  same rule at plan. Value equality stays out (topic 2). Found that the conflict check would misfire on `composite`
  (every schema shares the four top-level keys) and planned the one-level-down fix. 4-phase plan written; nothing
  built.
- 2026-10-23: reviewed the `composite` design against the code. Confirmed all four blocks are included. Corrected
  three points: the dedicated `composite requires view: effective` check must be defined before
  `validate_block_exists_on_kind()` (Pydantic stops at the first failing after-validator, verified); the location
  prefix is built from `target.block` today, so `composite` needs an explicit `spec` prefix, not "the same form" for
  free; the conflict-check misfire only happens when two schemas declare the same sub-block at the top level, not
  always. Added a limitation that applies to the shipped blocks too: rules see declared `value`s, which are real
  values only for `store: constant` (missed findings, never false ones). Merged the model and evaluator phases so no
  temporary guard is needed; 3 phases now.
- 2026-10-23: composite Phase 1 implemented (model + evaluator together, no temporary guard). Both real cfg-int cases
  reproduce end-to-end against a base + leaf chain, including the leaf override and the boolean feature value. 12 new
  tests, full check suite clean (3057 passed). Remaining: Phase 2 (conflict check), Phase 3 (docs).
- 2026-10-23: composite Phase 2 implemented. The conflict check compares `(sub_block, key)` pairs for `composite`
  instead of the four shared top-level keys. 3 new tests, full check suite clean (3060 passed). Remaining: Phase 3
  (docs).
- 2026-10-23: composite Phase 3 (docs) implemented. Graduated into
  [dynamic-configuration-validation.md](../design/dynamic-configuration-validation.md): document shape, "Scope",
  "Evaluation semantics" (composite shape and rules, conflict check one level down), "Known limits" (#3 rewritten to
  cross-block equality only; new #5 for declared-vs-resolved values, which also covers the shipped `variables`/
  `features` blocks), and a History entry. Collapsed this doc's composite design and plan into a pointer. Topic 1's
  remaining items are the two open questions (default view, finding attribution); topics 2-4 are untouched.
- 2026-10-24: decided `target.view` becomes required (no default); impact counted, not built. Analysed topic 2 against
  the real repos: 4 duplicate keys (not 12), all one environment value under the names different Terraform roots
  declare. A naming difference at the consumer, not a merge problem; ADR-0035's single `strata` object already removes
  the cause, so the rename moves into each root's HCL during the migration ADR-0035 already requires. No alias or
  copy-of feature. Also corrected two stale spots (the Requirements row in "What exists in v2 today" and the
  `Related:` line).
- 2026-10-24: built the required `target.view` in one phase: field default removed, `view: document` added to the
  3 `config/` schemas and every test fixture, a missing-`view` model test, docs (`attributeconfig.md`, `resource.md`,
  design doc shape, evaluation semantics and History). Full check suite clean (3061 passed); `config/` validates.
- 2026-10-24: open item 3 done. Converted cfg-int's c0224/dev leaf into `.v2-cfg/stacks/ring/dev/` with a deployment
  and three `composite` rules, and recounted: 779 to 338 lines, 465 to 51 comment lines, duplicates 4 to 0, v1-mechanics
  notes 11 to 0. All three requirements are now checks (7 findings proven on a broken scratch copy). Provenance is
  what survives (8 notes over 17 values, nearly all hand-copied upstream outputs, tying topic 3 to item 6); naming
  budgets shrink to 2 notes. Consumer-side HCL renames remain untried (the fork roots are not in `.v2-cfg`).
- 2026-10-24: item 6 decided: no strata capability. No other strata doc covered it. Checked cfg-int and iac-int v3:
  facts travel between layers, which are separate deployments (out of `${output:}`'s reach), as Terraform-written
  JSON records in the bootstrapped per-subscription state Key Vault, read at plan time by `layer-record` with schema
  and key checks. That also answers the reviewed-diff question (changes show as plan diffs) and removes most of
  topic 3's surviving provenance, since those values leave the config.
- 2026-10-24: topic 4 (item 5) decided: no strata feature. Terraform composes the names and enforces the limits at
  plan; a strata copy of the composition would drift. Also marked the adjacent section's candidate directions as
  superseded and refreshed the status line and item 7.
- 2026-10-24: topic 3 decided: no strata feature. Re-counted against the v3-shaped config rather than the v1 leaf:
  the provenance lines are change history (git), subscription onboarding facts (rendered by cfg-int's
  `New-BootstrapEnv.ps1`, which records each value's source), and layer outputs (layer records, item 6). The only
  genuinely pending values are 2 smoke-test image tags, which `kind: version` already covers, and one "not recorded"
  `application_id`.
- 2026-10-24: archived. Wrote ADR-0044 (no value metadata fields beyond requirements) and ADR-0045 (facts between
  layers are Terraform-written records); recorded finding attribution as out of scope in the design doc; repointed
  code and doc references from `docs/work/` to `docs/archive/`.
