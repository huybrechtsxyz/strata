# Terraform `*.auto.tfvars.json` Parity — v1 vs v2 Real Output Gap

- Status: **fixed** — every real, confirmed gap below has been designed
  and implemented (see "Fix Design and Resolution" at the end of this
  doc). The category-by-category catalog above the fold is kept as-is,
  unmodified, as the evidence record the fix was built from.
- Date: 2026-09-30 (catalog), 2026-09-30 (fix)
- Related: [docs/design/gap_fit_v1.md](gap_fit_v1.md) gap #15 (Terraform
  `variables.tf` input validation — a related but distinct gap: that one
  is about *checking* declared inputs against a module's real
  `variables.tf`; this one is about whether v2 even *produces* the
  variable names/shapes a real module declares in the first place, which
  is a prerequisite question), `terraform_projection.py` (the code this
  doc audits), `terraform_builder.py` (v1's real source, external, not
  part of this workspace)

## How this was verified — empirically, not by reading code alone

Prompted by a direct question ("can we compare the tf output for
instance? because those should not be that different correct?"). Rather
than comparing source code shape assumptions, generated **real** v2
output and diffed it against **real**, committed v1 output for the exact
same repository:

1. `.v2-cfg` (this workspace's own hand-migrated slice of
   `config-deploy`) has no local checkout of `iac-int-deployment`
   (the real Terraform source repo, `fetch: external`, a private GHE
   remote). Created a minimal placeholder checkout at the exact path
   `layout.remote_checkout_path()` expects (`spoke/terraform/main.tf`,
   `core/terraform/main.tf`, both empty stubs — `build run` never invokes
   Terraform itself, only copies files and writes tfvars, so a stub is
   sufficient).
2. Ran the real installed CLI: `strata build run deploy-spoke --path
   .v2-cfg --build-path .v2-cfg\build\deploy-spoke`. Succeeded, produced 8
   real `*.auto.tfvars.json` files.
3. Diffed those files' actual JSON content against
   `e:\sources\config-deploy\build\deploy-hub-z00-spoke-s01-customer-c0224-1.0.0\core\terraform\*.auto.tfvars.json`
   — v1's real, committed output for a real deployment in the same repo
   (a different deployment level — customer-scoped vs. `.v2-cfg`'s
   spoke-scoped — so exact values differ, but every *category's shape
   convention* is directly comparable).
4. Cross-checked every discrepancy against v1's real
   `builders/terraform_builder.py` source directly (every `_build_*_vars`
   method read in full, plus `_planned_files()` — the function that
   decides the final on-disk filename + wrapper for each category) —
   never assumed from output alone.

## The core convention v1 actually follows — three shapes, not one

Confirmed by reading every `_build_*_vars()`/`_planned_files()` pair
directly — v1 does **not** use one uniform convention. Three different
shapes, depending on what each category's real Terraform variable is
supposed to be:

1. **Wrapped collections** — the whole category becomes **one** Terraform
   variable holding a map. File content is `{"<wrapper_key>": {...}}`.
   Examples: `providers.auto.tfvars.json` → `{"platform_providers": {...}}`,
   `resx_<type>.auto.tfvars.json` → `{"resources": {...}}`,
   `tenant.auto.tfvars.json` → `{"strata_tenant": {...}}`. The wrapper key
   is **not** the filename's own category name in every case (`providers`
   file → `platform_providers` variable; `tenant` file → `strata_tenant`
   variable) — a real, easy-to-miss detail.
2. **Flat multi-variable bags** — every top-level key becomes its own,
   independent Terraform variable. No wrapper at all, by design.
   Examples: `flags.auto.tfvars.json` (`{key: bool}`, one variable per
   flag), `variables.auto.tfvars.json` (`{key: value}`, one variable per
   variable), `workspace.auto.tfvars.json` (`workspace_name`,
   `deployment_name`, `environment`, `platform_version`, `labels`,
   `metadata` — six independent variables, not one `workspace` map).
3. **Pure passthrough, no wrapper, no v1 opinion** — `properties`/`custom`:
   whatever the real YAML's own `spec.properties`/`spec.custom` declares,
   merged and written as-is. v1's `_resolve_merged_properties()` adds no
   wrapper of its own — the `"environment_info"` key seen in the real
   output is authored content in the real `environment.yaml`, not a
   builder convention. **Confirmed not a gap** — v2's pure-passthrough
   `_build_properties_payload()`/`_build_custom_payload()` (`return
   graph.properties`/`graph.custom` directly) is already correct in
   shape; it will reproduce whatever key structure the real YAML declares
   once that YAML exists in `.v2-cfg`.

v2's actual behavior mixes these up: some categories correctly use shape
2 (`flags`/`variables` — already right), one already correctly uses shape
1 (`resx_<type>` — already fixed once, per that function's own docstring
recalling exactly this bug class), but **most of the rest use shape 2
(flat, no wrapper) where v1's real contract needs shape 1 (wrapped)**.

## Category-by-category findings

| Category              | File                                                                                                           | v1's real shape (confirmed)                                                                                                                                                                                                                                                                                             | v2's actual shape (confirmed)                                                                                          | Verdict                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| --------------------- | -------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **workspace**         | `workspace.auto.tfvars.json`                                                                                   | Flat, 6 vars: `workspace_name`, `workspace_version`, `deployment_name`, `environment`, `platform_version`, `labels`, `metadata: {deployment_version, workspace_description, deployment_description, workspace_tags, deployment_tags}`                                                                                   | Flat, 4 keys: `name`, `labels`, `tags`, `annotations`                                                                  | **Real gap.** Missing `deployment_name`/`environment`/`platform_version`/`workspace_version`/`metadata` entirely — a real Terraform root using any of these (very plausible for resource naming/tagging) gets nothing. Also needs deployment-level context (`deployment_name`, `environment`) that `_build_workspace_payload(graph)` doesn't currently receive at all — a signature change, not just a field rename.                                                                                                                                                                                                   |
| **providers**         | `providers.auto.tfvars.json`                                                                                   | `{"platform_providers": {name: {type, region, version, description, labels, tags}}}`                                                                                                                                                                                                                                    | `{name: {type, region, display_name, configuration, custom}}` — **no wrapper**                                         | **Real gap.** Missing `platform_providers` wrapper (every top-level provider name becomes its own stray variable instead). Field drift too: v1 has `version`/`description`/`labels`/`tags`; v2 has `display_name`/`configuration`/`custom` instead — `configuration`/`custom` are a deliberate, documented v2 addition (gap #17 fix) worth keeping; `version`/`description`/`labels`/`tags` being dropped is not documented anywhere and needs a decision.                                                                                                                                                             |
| **topologies**        | `topologies.auto.tfvars.json`                                                                                  | `{"topologies": {name: {type, provider, provisioner, components, volumes}}}`                                                                                                                                                                                                                                            | `{name: {type, components, volumes}}` — **no wrapper**                                                                 | **Partial gap.** Missing wrapper (real bug). Missing `provider`/`provisioner` is **deliberate, already documented** — ADR-0011 moved that binding to `spec.execution[]`; the module's own docstring already explains this, not an oversight.                                                                                                                                                                                                                                                                                                                                                                           |
| **modules**           | `modules.auto.tfvars.json`                                                                                     | `{"modules": {name: {repository, source_path, target_path, description, labels, tags, properties}}}`                                                                                                                                                                                                                    | **No equivalent function exists at all**                                                                               | **Deliberate, already-justified — confirmed, not a gap.** `terraform_projection.py`'s own module docstring: checked all six real workspaces available for ADR-0023; zero use of `TopologyComponentModel.modules` in any of them (Compose/Helm modules go through the entirely separate `prepare_namespace()` pipeline, ADR-0022 D5-D7, never this projection). Same evidence-based-omission treatment as topology's `provider`/`provisioner` and dns's secret/output split below — this doc's earlier "undecided" verdict was wrong; the decision was already made and documented, just not yet cross-referenced here. |
| **namespaces**        | `namespaces.auto.tfvars.json`                                                                                  | `{"namespaces": {name: {description, labels, tags, modules}}}`                                                                                                                                                                                                                                                          | `{name: {description, labels, tags, modules}}` — **no wrapper**                                                        | **Real gap.** Fields match exactly; only the wrapper is missing.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| **firewalls**         | `firewalls.auto.tfvars.json`                                                                                   | `{"firewalls": {name: {description, labels, tags, rules: {reset, defaults, deny, allow}}}}`                                                                                                                                                                                                                             | `{name: {description, labels, tags, rules: {...}}}` — **no wrapper**                                                   | **Real gap.** Fields match closely; only the wrapper is missing.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| **dns**               | `dns.auto.tfvars.json` (+ v1-only `dns_secret_records.auto.tfvars.json`/`dns_output_records.auto.tfvars.json`) | `{"dns_zones": {name: {description, labels, tags, provider, zones}}}`, plus two more files for secret-sourced/output-sourced records                                                                                                                                                                                    | `{name: {description, labels, tags, provider, zones}}` — **no wrapper**, and no secret/output-record file split at all | **Two-part finding.** Missing `dns_zones` wrapper is a real gap. The missing secret/output-record split is **deliberate, already documented** (`_build_dns_payload()`'s own docstring: v2's `DnsRecordModel` has one `value` field, `output_key` was never ported — ADR-0006, no shared runtime Context store yet) — not an oversight.                                                                                                                                                                                                                                                                                 |
| **networks**          | `networks.auto.tfvars.json`                                                                                    | Very likely `{"networks": {name: {description, labels, tags, networks: {...}}}}` (same established wrap-by-category-name pattern as providers/topologies/modules/namespaces/firewalls; return statement not directly re-read this pass, inferred with high confidence from the identical pattern in 5 other categories) | `{name: {description, labels, tags, networks: {...}}}` — **no wrapper**                                                | **Real gap, same shape as the others** — verify the exact v1 return statement before fixing, but the wrapper-missing pattern is already proven common enough here not to be in doubt.                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| **resx_&lt;type&gt;** | `resx_<type>.auto.tfvars.json`                                                                                 | `{"resources": {name: entry}}`                                                                                                                                                                                                                                                                                          | `{"resources": {name: entry}}`                                                                                         | **Already correct** — this exact bug class was found and fixed once already; the fix's own docstring documents the incident.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| **tenant**            | `tenant.auto.tfvars.json`                                                                                      | `{"strata_tenant": {code, name, zones, onboarded, configuration}}` (confirmed twice — once in `terraform_builder.py`, once independently via `tenant_zone_policy.py`'s real `plan_data["variables"]["strata_tenant"]` read)                                                                                             | `{code, name, zones, onboarded, configuration}` — **no wrapper**                                                       | **Real gap, narrow fix.** Field names already match v1 almost exactly (`zones` even keeps its v1 name despite the model itself being renamed to `geographies` internally) — only the `strata_tenant` wrapper is missing. The function's own docstring already flagged this uncertainty ("no fixture data exists yet to confirm v1's exact real shape for this one") — this doc's evidence resolves it.                                                                                                                                                                                                                 |
| **flags**             | `flags.auto.tfvars.json`                                                                                       | Flat `{key: bool}`, one variable per flag                                                                                                                                                                                                                                                                               | Flat `{key: bool}`                                                                                                     | **Already correct.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| **variables**         | `variables.auto.tfvars.json`                                                                                   | Flat `{key: value}`, one variable per variable                                                                                                                                                                                                                                                                          | Flat `{key: value}`                                                                                                    | **Already correct.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| **properties**        | `properties.auto.tfvars.json`                                                                                  | Pure passthrough of the real YAML's own merged `spec.properties` — no builder-added wrapper                                                                                                                                                                                                                             | Pure passthrough (`return graph.properties`)                                                                           | **Already correct** — confirmed the real output's `"environment_info"` key is authored content in the real repo's YAML, not a v1 builder convention, so v2's shape-neutral passthrough is the right design already.                                                                                                                                                                                                                                                                                                                                                                                                    |
| **custom**            | `custom.auto.tfvars.json`                                                                                      | Same passthrough convention as `properties`, `custom` source                                                                                                                                                                                                                                                            | Pure passthrough (`return graph.custom`)                                                                               | **Already correct**, same reasoning as `properties`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |

## Severity, at a glance

- **Confirmed real gaps needing a fix** — **all fixed**: workspace (field
  set), providers (wrapper + field drift), topologies (wrapper only),
  namespaces (wrapper only), firewalls (wrapper only), dns (wrapper
  only), networks (wrapper only), tenant (wrapper only).
- **Confirmed already correct, no action needed**: resx_&lt;type&gt;,
  flags, variables, properties, custom.
- **Deliberate, already-reasoned v2 differences — not gaps**: `modules`
  (no v2 equivalent at all — zero real usage across all six real
  workspaces this ADR checked), topology's dropped `provider`/
  `provisioner` fields (ADR-0011), dns's dropped secret/output-record
  split (ADR-0006 — no runtime Context store yet).

Six of the eight real gaps were the exact same one-line fix (wrap the
existing, otherwise-correct payload under its real v1 variable name).
`workspace` needed deployment context threaded into
`ResolvedWorkspaceGraph`; `providers` needed `description`/`labels`/
`tags` restored from the provider's own `meta`. See "Fix Design and
Resolution" below for exactly what shipped.

## Open Questions — resolved

1. ~~For `providers`: keep v2's `configuration`/`custom` additions...~~
   **Resolved**: kept `configuration`/`custom`, restored `description`/
   `labels`/`tags` (real, evidence-backed — the real committed output has
   non-trivial values for all three). `version` deliberately **not**
   restored — no v2 model field exists for it at all, and the one real
   data point available shows it as `null` — no confirmed real usage to
   justify adding a new model field for it. Tracked as its own, still-open,
   much narrower question below.
2. ~~For `workspace`: needs deployment identity threaded in...~~
   **Resolved**: added `deployment: DeploymentModel | None` to
   `ResolvedWorkspaceGraph` (same optional-with-None-default pattern as
   its existing `tenant` field), threaded through
   `build_resolved_workspace_graph()`'s two real call sites
   (`build_controller.py`, `deploy_controller.py` — both already had a
   resolved `DeploymentModel` in scope, just not passed through).
3. ~~Is `modules` still a real category in v2's world...~~ **Resolved,
   turned out to already be answered**: `terraform_projection.py`'s own
   module docstring already states it was checked and deliberately
   dropped (zero real usage across all six real workspaces available for
   ADR-0023) — see the corrected table row above. No new work needed.
4. Should this doc's findings feed into gap #15 (Terraform `variables.tf`
   validation) as one combined effort, or ship independently? **Decided
   in practice**: shipped independently, first — gap #15 remains open,
   unstarted, tracked separately.
5. Is there a real Terraform root (`iac-int-deployment`) available to
   verify against directly? **Still open** — not needed for this fix
   (v1's builder source plus the real committed output were sufficient,
   confirmed consistent with each other everywhere checked), but would
   remove all remaining doubt if it ever becomes available.

### Newly opened by the fix itself

1. **`ProviderPropertiesModel.version`** — v1 has this field
   (`provider.properties.version`), v2 has no equivalent at all. Not
   restored (see above) — would need a new, currently-unjustified model
   field if real usage ever surfaces.
2. ~~Deploy-time `TF_VAR_<name>` env var naming does not use the same
   real-variable-name mapping the on-disk file now does.~~ **Fully
   resolved** (see "Fix Design and Resolution — deploy-time env var
   naming" below, both passes): `providers`/`dns`/`tenant` deliver as
   `TF_VAR_platform_providers`/`TF_VAR_dns_zones`/`TF_VAR_strata_tenant`
   (first pass); `workspace`/`flags`/`variables`/`properties`/`custom`
   each deliver one `TF_VAR_<key>` per top-level key instead of one blob
   per category, with a hard collision-detection error if two flat
   categories ever declare the same key; every `resx_<type>` merges into
   one `TF_VAR_resources` instead of colliding per type (second pass).

## Related Decisions

None yet — no v2 ADR exists for Terraform tfvars projection shape.
Source material: v1's real `builders/terraform_builder.py` (external, not
part of this workspace, read in full this pass) and
`e:\sources\config-deploy`'s real, committed build output (also
external).

## Fix Design and Resolution — build-time file wrapping

Scope: build-time **file** parity only (`planned_files()`'s output) —
matches this doc's own empirical method (comparing real committed
`*.auto.tfvars.json` files). Deploy-time `TF_VAR_<name>` env var delivery
(`build_configuration_payloads()`/`deploy_controller.py`) was
deliberately left untouched in this first pass — see the follow-on fix
below.

**Design, in order of how much surface it touches:**

1. **Wrapper keys** (topologies/namespaces/firewalls/networks/providers/
   dns/tenant): the wrap happens in `planned_files()` only, not inside
   each `_build_*_payload()` function. Kept `_build_providers_payload()`
   etc. returning the flat `{name: {...}}` shape (the actual Terraform
   *variable's value*), and added `planned_files()`'s own
   `_REAL_VARIABLE_NAME` lookup (`providers` → `platform_providers`,
   `dns` → `dns_zones`, `tenant` → `strata_tenant`; every other
   non-flat category wraps under its own name unchanged) plus a
   `_FLAT_CATEGORIES` set (`workspace`/`flags`/`variables`/`properties`/
   `custom` — never wrapped). This keeps `build_configuration_payloads()`'s
   deploy-time payloads (which reuse the exact same `_build_*_payload()`
   functions) unaffected by the file-shape wrapper — confirmed
   necessary: wrapping inside the builder functions would have
   double-wrapped the deploy-time `TF_VAR_<name>` env var's value too,
   since both code paths call the same functions.
2. **`workspace`**: `ResolvedWorkspaceGraph` gained a `deployment:
   DeploymentModel | None = None` field (same pattern as its existing
   `tenant` field), threaded through `build_resolved_workspace_graph()`'s
   two real call sites. `_build_workspace_payload()` rewritten to
   v1's exact real formula (confirmed directly against
   `_build_workspace_vars()`): `workspace_version` from
   `workspace.meta.labels["version"]` (default `"1.0.0"`),
   `deployment_name`/`platform_version` from the deployment document's
   own `meta.name`/`apiVersion`, `environment`/`metadata.deployment_version`
   from `deployment.meta.labels["environment"/"version"]` (defaults
   `"production"`/`workspace_version`), `metadata.*_description`/`*_tags`
   from each document's own `meta.annotations`/`meta.tags`. Falls back to
   sensible defaults when `graph.deployment` is `None`.
3. **`providers`**: added `description`/`labels`/`tags` from
   `ProviderModel.meta` (the same convention every other category here
   already uses for its own `description` field). `version` deliberately
   not added (see Open Questions).

**Implementation** (2026-09-30, same day as the catalog):
- `src/strata/integrations/resolved_context.py` — `ResolvedWorkspaceGraph.deployment` field added.
- `src/strata/controllers/build_controller.py` — `build_resolved_workspace_graph()` gained a `deployment` parameter; its one real call site passes the already-resolved `DeploymentModel` through.
- `src/strata/controllers/deploy_controller.py` — same call site update.
- `src/strata/integrations/terraform_projection.py` — `_build_workspace_payload()` rewritten; `_build_providers_payload()` extended; `planned_files()` gained `_REAL_VARIABLE_NAME`/`_FLAT_CATEGORIES` and now wraps non-flat categories at file-write time; `_build_tenant_payload()`'s docstring corrected (it previously claimed "no fixture data exists" for v1's tenant shape — this doc's own evidence resolves that).
- Tests updated in `tests/strata/integrations/test_integrations_terraform_projection.py` (new/updated assertions for the workspace/providers field sets and the wrapper-key behavior) and the three on-disk-file assertions in `tests/strata/controllers/test_build_controller.py`/`test_deploy_controller.py` that read a `.auto.tfvars.json` file's content directly.
- Full check suite green: mypy (121 files), ruff, import-linter, pytest (1642 passed, only the pre-existing, unrelated `test_shipped_example_solution_loads_cleanly` failure remains — confirmed present before this change too).

## Fix Design and Resolution — deploy-time env var naming (2026-09-30, follow-on, two passes)

**Pass 1 — rename the categories with one real variable name.** Reused
the exact same `_REAL_VARIABLE_NAME` knowledge the build-time file fix
above already established, applied a second time to a second,
independent call site.

**Design:** added a public `real_variable_name(category: str) -> str` to
`terraform_projection.py` — `_REAL_VARIABLE_NAME.get(category, category)`,
exposed so `deploy_controller.py` can reuse it (identity for every
category with no single rename target: `FLAT_CATEGORIES` and
`resx_<type>`, handled separately in pass 2 below). Wired into both of
`deploy_controller.py`'s delivery loops:
`build_dns_networks_firewalls_payloads()`'s per-step-claimed categories
(`dns` → `TF_VAR_dns_zones`; `networks`/`firewalls` unchanged, already
identity) and `build_configuration_payloads()`'s ten broadcast categories
(`providers` → `TF_VAR_platform_providers`, `tenant` →
`TF_VAR_strata_tenant`; everything else unchanged).

**Implementation:**
- `src/strata/integrations/terraform_projection.py` — new `real_variable_name()` function.
- `src/strata/controllers/deploy_controller.py` — both `env[f"{integration.ENV_VAR_PREFIX}{category}"]`/`{name}` sites now key through `real_variable_name(category)`/`real_variable_name(name)` instead of the bare category/file name.
- Tests updated in `tests/strata/controllers/test_deploy_controller.py` (`TF_VAR_dns` → `TF_VAR_dns_zones`, `TF_VAR_providers` → `TF_VAR_platform_providers`, across all three affected tests).

**Pass 2 — the two categories pass 1 deliberately left alone**, per
direct request ("lets look into... the tfvars parity leftover item...
before the variables.tf validation"):

**Design:**
1. **`FLAT_CATEGORIES`** (`workspace`/`flags`/`variables`/`properties`/
   `custom`): each top-level key is its own independent real Terraform
   variable (not one variable per category) — delivered as one
   `TF_VAR_<key>` per key instead of one `TF_VAR_<category>` blob.
   `FLAT_CATEGORIES` was promoted from a private `_FLAT_CATEGORIES` to a
   public constant in `terraform_projection.py` so `deploy_controller.py`
   can reuse the exact same set the build-time file fix already
   established, rather than a second, possibly-drifting definition.
2. **Collision detection**: an env var, unlike a separate on-disk
   `*.auto.tfvars.json` file, can only hold one value per name — if two
   flat categories (or `resx_<type>`'s merge, see below) ever declare the
   same top-level key, the second one would silently overwrite the first
   in the `env` dict before Terraform ever runs, with no ordering
   guarantee at all (unlike the file case, at least loaded in a
   deterministic order). Computed once, step-invariant, right alongside
   the existing pre-flight `${output:...}` checks — a real hard error
   (`diagnostics.error`), not a silent overwrite.
3. **`resx_<type>`**: every real file is actually named `resources`
   regardless of type (confirmed against v1's real
   `_build_resources_by_category()`) — delivering each type as its own
   `TF_VAR_resx_<type>` would be a made-up variable name no real root
   declares, and delivering all types as separate `TF_VAR_resources`
   assignments would have the last type silently clobber every earlier
   one. Fixed by merging every active type's `resources` sub-dict into
   one combined dict, delivered once as a single `TF_VAR_resources`.

**Implementation:**
- `src/strata/integrations/terraform_projection.py` — `_FLAT_CATEGORIES` renamed to public `FLAT_CATEGORIES`; `planned_files()`'s local variable renamed from `real_variable_name` to `real_name` (was shadowing the module-level function of the same name).
- `src/strata/controllers/deploy_controller.py` — new step-invariant pre-loop block (`flat_key_owner`/`merged_resources`) computing collisions and the merged resources dict once; the per-step delivery loop now skips `FLAT_CATEGORIES`/`resx_<type>` in its generic `real_variable_name()` pass and delivers them via two dedicated blocks instead (per-key `TF_VAR_<key>` for flat categories, one merged `TF_VAR_resources`).
- Tests: two existing assertions updated (`TF_VAR_resx_server` → `TF_VAR_resources`, unwrapped shape) in `tests/strata/controllers/test_deploy_controller.py`; three new tests added — `test_deploy_run_merges_multiple_resx_types_into_one_tf_var_resources`, `test_deploy_run_delivers_flat_categories_as_one_env_var_per_key`, `test_deploy_run_rejects_flat_category_key_collision`.
- Full check suite green: mypy (121 files), ruff, import-linter, pytest (1645 passed, same one pre-existing unrelated failure as every prior run this doc records).

This closes out every category identified in this doc's own "Newly
opened by the fix itself" #2 — deploy-time `TF_VAR_<name>` delivery now
matches the real Terraform variable name (or shape) for all thirteen
categories, not just the eight the build-time file fix covered.

## Changelog

- 2026-09-30: Created, per direct request ("lets look at the full
  terraform output and make a design gap doc we need to fix. then we can
  plan"). Empirically generated real v2 build output against `.v2-cfg`
  (a minimal placeholder external-repo checkout, since `build run` never
  invokes Terraform itself) and diffed it against `config-deploy`'s
  real, committed v1 build output for the same repository, cross-checked
  against v1's real `terraform_builder.py` source in full. Found v1
  actually follows three distinct shape conventions (wrapped collections,
  flat multi-variable bags, and pure unwrapped passthrough), not one, and
  catalogued all ~13 categories against them. Six real gaps found are the
  same one-line wrapper-key fix; `workspace` needs a signature change to
  thread deployment context in; `providers` needs a field-set
  reconciliation decision; `modules` has no v2 equivalent at all and
  needs a real design decision, not a mechanical port. Confirmed several
  suspected differences are actually deliberate, already-documented v2
  architecture choices (topology's `provider`/`provisioner` removal,
  ADR-0011; dns's secret/output-record split removal, ADR-0006) — not
  gaps. No fixes made yet, no design decisions taken — catalog only, per
  direct request to plan afterward.
- 2026-09-30: **Fixed**, per direct request ("some differences are
  allowed but it is v2... the v1 core layout was correct... design the
  fit so the terraform output is back on track"). Corrected the
  `modules` verdict from "undecided" to "deliberate, already-justified"
  (found the real evidence already documented in
  `terraform_projection.py`'s own module docstring — no new
  investigation needed). Designed and implemented all 8 confirmed real
  gaps: wrapper keys for topologies/namespaces/firewalls/networks/
  providers/dns/tenant (via a new `planned_files()`-local
  `_REAL_VARIABLE_NAME`/`_FLAT_CATEGORIES` lookup, deliberately not
  inside the builder functions themselves, to avoid double-wrapping
  `build_configuration_payloads()`'s deploy-time env-var delivery, which
  reuses the same functions); `workspace`'s full v1 field set (via a new
  `ResolvedWorkspaceGraph.deployment` field, threaded through both real
  `build_resolved_workspace_graph()` call sites); `providers`'
  `description`/`labels`/`tags` (from `ProviderMetaModel`, previously
  unread). Deliberately scoped to build-time *file* parity only — found
  and flagged, but did not fix, a related, deeper, v2-only gap: deploy-time
  `TF_VAR_<name>` env var delivery still names variables after the file
  category, not the real Terraform variable name the file-side fix now
  uses, and needs its own separate design pass (workspace/flags/variables
  have no single "real variable name" to map to at all). Full check
  suite green (mypy 121 files, ruff, import-linter, pytest 1642 passed,
  same one pre-existing unrelated failure as before this change).
- 2026-09-30: **Fixed the deploy-time env var naming leftover**, per
  direct request ("lets look at the tfvars parity leftover item"),
  scoped to exactly the categories that have one real Terraform variable
  name (`providers`/`dns`/`tenant`) — reused the build-time fix's own
  `_REAL_VARIABLE_NAME` lookup via a new public `real_variable_name()`,
  wired into both of `deploy_controller.py`'s `TF_VAR_<name>` delivery
  loops. `workspace`/`flags`/`variables`/`properties`/`custom` (no single
  real variable name each) and `resx_<type>` (every real file is
  actually named `resources` regardless of type — a single env var per
  type would collide if more than one type is active in one step) remain
  deliberately out of scope, now precisely identified as needing a
  materially different delivery shape, not a rename. Full check suite
  green (mypy 121 files, ruff, import-linter, pytest 1642 passed, same
  one pre-existing unrelated failure).
- 2026-09-30: **Closed the remaining deploy-time env var naming gap**,
  per direct request ("lets look into... the tfvars parity leftover
  item... before the variables.tf validation"). `FLAT_CATEGORIES`
  (`workspace`/`flags`/`variables`/`properties`/`custom`) promoted from
  private to public in `terraform_projection.py`, now delivers one
  `TF_VAR_<key>` per top-level key instead of one blob per category —
  with a new hard collision-detection error (computed once,
  step-invariant) if two flat categories ever declare the same key,
  since an env var, unlike a separate on-disk file, can only hold one
  value per name with no ordering guarantee. `resx_<type>` now merges
  every active resource type's `resources` sub-dict into one combined
  dict, delivered once as `TF_VAR_resources` (the one real Terraform
  variable name every real file actually uses, confirmed against v1's
  `_build_resources_by_category()`) instead of a made-up
  `TF_VAR_resx_<type>` per type that would silently clobber across
  types. Two existing tests updated, three new tests added covering the
  merge, the per-key delivery, and the collision error. This closes out
  every category this doc's "Newly opened by the fix itself" #2
  flagged — deploy-time `TF_VAR_<name>` delivery now matches v1's real
  Terraform variable contract for all thirteen categories, not just the
  eight the first deploy-time pass covered. Full check suite green
  (mypy 121 files, ruff, import-linter, pytest 1645 passed, same one
  pre-existing unrelated failure).
