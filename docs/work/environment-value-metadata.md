# Environment Value Metadata — Work

- Status: draft — problem and evidence recorded; the attributeconfig-over-merged-`properties`/`custom` sub-design
  (topic 1) is fully built and documented in [dynamic-configuration-validation.md](../design/dynamic-configuration-validation.md)
  (`target.view: document | effective`); topics 2-4 and the genuinely open questions under topic 1 remain undecided
- Last updated: 2026-10-22
- Related: [dynamic-configuration-validation.md](../design/dynamic-configuration-validation.md)
  (`attributeconfig`: the closest existing mechanism, scoped to `properties`/`custom`/`configuration` only),
  [ADR-0040](../decisions/0040-attributeconfig-standalone-kind.md),
  [cross-document-value-references.md](../design/cross-document-value-references.md) (`${value:}`),
  [composite-variable-merge.md](../design/composite-variable-merge.md),
  [policy-engine-architecture.md](policy-engine-architecture.md) (generic engine ruled out, 2026-10-07),
  [promotion.md](promotion.md) (same consumer),
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
  forked root cannot be renamed to match.
- **Provenance.** Values such as `api_management_id`, `vnet_id` and `agw_subnet_id` are copied from applied upstream
  layers on purpose (the audit trail requires the value to be visible in the reviewed diff; App Configuration is
  private-endpoint-only). Each carries "confirmed via `az apim show`, 2026-09-23" or similar, plus "re-read if the VNet
  is recreated". `dispatcher_product_name` is "PLACEHOLDER pending a naming decision ... changing it later forces a
  destroy/recreate".
- **Naming budget.** "dev + intdsp + c0224dev2 + kv = 20 characters. Four to spare. Re-check that sum before changing
  any of the three." Key Vault caps at 24; node pool names at 12; tenant codes are held to 5 (AKS pool budget).

## What exists in v2 today

| Need         | Closest existing mechanism                                                                                                                   | Gap                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Requirements | `attributeconfig`: JSON Schema 2020-12 (`pattern`, `required`, `if`/`then`, `dependentRequired`, `enum`) pushed onto every matching document | Targets only the `properties`/`custom`/`configuration` blob. `variables` and `features` are explicitly out of scope (typed lists), and most of cfg-int's values are in `variables`. A schema sees one block, so "feature X on, therefore variable Y non-empty" cannot be expressed. **Tested 2026-10-21:** `block: variables` and `block: features` are rejected at load (`Input should be 'properties', 'custom' or 'configuration'`). |
| Requirements | `VariableStoreModel` validators                                                                                                              | Only `type`/`value` consistency and `default`/`field` placement. No pattern, no cross-key rule.                                                                                                                                                                                                                                                                                                                                         |
| Alias        | `${value:kind.name.path}` (resolved at validate and deploy)                                                                                  | Addresses another *document's* field. A list segment is matched by a `"name"` key, but variables are keyed by `key`. **Tested 2026-10-21:** by `key` fails (`value_reference_invalid_path`); by list position (`variables.1.value`) validates and resolves, but position is fragile and the token addresses one document, not the merged environment.                                                                                   |
| Alias        | `${var:KEY}` inside another variable's `value`                                                                                               | **Tested 2026-10-21: does not work.** Before the fix `strata validate` accepted it (0 errors), `build run` emitted the literal token into tfvars, and `deploy run` failed with `value_resolution_failed`; a `store: constant` value is only resolved against `${value:}` references. `validate` now rejects it (`store_value_token_unsupported`).                                                                                       |
| Provenance   | `meta.annotations` (free-form, per document); `kind: version` pins carry a rationale ([ADR-0019](../decisions/0019-version-pinning.md))      | Per document, not per key. Nothing structured, nothing queryable, nothing that can go stale.                                                                                                                                                                                                                                                                                                                                            |
| Naming       | `attributeconfig` `maxLength` / `pattern` on a single `properties` value                                                                     | A budget applies to a name *composed* from several keys plus a literal suffix, so it is a computed length across keys, which a per-value schema cannot state.                                                                                                                                                                                                                                                                           |

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

Open shapes (not chosen):

- Extend `attributeconfig` to also target `variables`/`features`, evaluated against the **merged** environment of a
  deployment (a leaf alone is meaningless: its blanks are filled or overridden by the layers above it).
- A small dedicated rule type, `required_when` / `forbidden_when` over `(feature | variable) -> (variable)`, kept
  deliberately narrow so it does not become a policy engine.
- Move the data into `properties` and use what exists. Cheapest, but does not help `features`, and a feature flag is
  almost always the "when" side.

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

- Should `effective` become the default for `properties`/`custom` on the layered kinds? Per-document `required` on them
  is almost never what an author means, but flipping the default is breaking. Nothing in the repo relies on it today.
- Attributing a finding to the layer that set the value.
- The cross-block extension (`variables`/`features` composed with `properties`, topic 1 item 2 above) is the same
  `effective` view with a different block name. This design stops at `properties`/`custom`, but uses a
  deployment-kind target so that extension is additive rather than a redesign.

### 2. Alias or copy-of

Two different intents that should not be conflated:

- **Alias**: one source of truth; the second key is generated from the first. Removes the duplication.
- **Copy-of**: both literals stay; strata asserts they are equal. Cheaper, but the duplicate remains.

Neither is expressible today (tested, see the two Alias rows above): `${var:KEY}` inside a variable does not resolve,
and `${value:}` cannot address a variable by key. The smallest candidate is a field on the variable naming its source
key, resolved after the layers merge and failing `validate` if the source is missing. The structural root cause (one flat, broadcast key namespace per provisioner)
is a separate, larger question; cfg-int's own config calls per-provisioner input scoping "a separate upstream ask".
An alias is a workaround for it, not a fix.

### 3. Structured provenance

Candidate facts that exist today only as prose: origin (an upstream output, a `az` query, a decision), the date it was
last checked, and a status (`pinned`, `placeholder`, `derived`). The payoff is queries and staleness, not validation:
list every placeholder in a deployment, list everything unchecked for N days, point at the upstream output a pinned
value should still equal.

Open questions:

- Per-key field on the variable, or a separate sidecar so the 700-line file does not grow further?
- Is it emitted anywhere? It should not reach Terraform or the tfvars projection.
- Relationship to the audit trail ([audit-trail.md](audit-trail.md)): that records what a *deploy* did. This records
  where a *config value* came from. Related, but the two should not be merged.
- If a pinned value has a recorded origin, a drift check against the upstream output becomes possible. That is a
  separate feature layered on this one, and cfg-int rules out resolving such values at deploy time, so it would have
  to be an explicit check or a refresh command that produces a diff.

### 4. Naming budgets

Narrow by design: *length* (and the charset that goes with it), not naming. Composing names is Terraform's job:
cfg-int's `docs/design/apim-name-primitive.md` (draft, 2026-10-19) puts the rules in a `primitives/` lookup that fails at
plan time, and deliberately avoids a second rule set. The real question here is only whether strata should catch an
over-length composed name earlier, at `strata validate` on the pull request, instead of at plan.

- If yes, strata would need the same inputs the primitive uses (the parts, the literal suffix, the limit), which
  duplicates the rule and has to be kept consistent with it.
- If no, strata's contribution shrinks to the single-value shape checks in topic 1 (the closed `environment_name` set,
  the tenant-code pattern and its 5-character limit), which are already in reach.

Related, already tracked: [subnet-cidr-derivation.md](subnet-cidr-derivation.md) reached the same "strata carries the
request, Terraform computes" split for CIDRs.

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
  and firewall documents had none until a step could claim them ([gap_fit_v1.md](gap_fit_v1.md) gap #12).
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

Candidate directions (mine, not decided, each with its cost):

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
3. Convert one real cfg-int leaf (c0224/dev) to v2 and recount: how many of the blanks, duplicates and dated notes
   survive conversion, and which are removed by existing v2 features (composite-variable-merge, unified projection).
   Do this before designing, since conversion may shrink the problem.
4. ~~Decide the scope question for topic 1's `properties`/`custom` half~~ — resolved by building it: `target.view:
   effective` (2026-10-22, see topic 1) covers the layered-blob half without waiting on the `variables`/`features`
   question. What's still open is only the `variables`/`features` half — extend `attributeconfig` further (item 2's
   three bullets above), add a narrow dedicated rule, or move the data to `properties` instead.
5. Decide whether naming budgets belong in strata at all (topic 4).
6. Decide whether, and how, an upstream step's output reaches a downstream step's inputs (the adjacent section above).
   cfg-int works around it with the Key Vault for now, so this is not blocking; the trigger to revisit is when that
   workaround is dropped.
7. Only then pick a first slice. The recorded defect (blank value that silently disabled private endpoints) makes
   cross-key requirements the strongest candidate; provenance has the largest volume but no defect behind it.

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
