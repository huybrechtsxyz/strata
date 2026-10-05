# Tenant Zone Policy (`policies: tenant_zone`) — v1 Capability Catalog and v2 Gap

- Status: **static tier implemented (2026-09-30)** —
  `_check_deployment_tenant_geography()` is live in `semantic_checks.py`,
  wired into `_check_deployments()`'s existing loop. The full plan-time
  v1-faithful check (Terraform plan-JSON parsing, resource-level location
  extraction) remains deliberately deferred — sized, not built.
- Date: 2026-09-30
- Related: [docs/work/gap_fit_v1.md](gap_fit_v1.md) gap #7 (`policies`/`promotions`
  still fully open), [docs/design/path-conventions.md](path-conventions.md)
  (the sibling policy type, `path_convention`, already resolved — same
  "don't port the generic engine, model the one real check directly"
  precedent this doc follows), `tenant_model.py` (`TenantSpecModel.
  geographies` — the concept v1's `zones` config was superseded by),
  `docs/work/audit-trail.md` (this doc is deliberately split out from
  there — tenant-zone enforcement is unrelated to the audit trail; the two
  were never the same feature, they only shared gap #7's "still fully
  open" bucket)

## Why this is a separate document

Gap #7 groups `policies`/`audit`/`promotions`/`paths` together because
they all lived under v1's same `Configuration.spec` subtree — a grouping
by *where the YAML sits*, not by what the features do. `audit` (now
resolved, Layers 1-2) and `paths` (now resolved) were each their own
design effort; `tenant_zone` — the one real, active-by-declaration
`policies` type — deserves the same treatment rather than being folded
into `audit-trail.md`, which is about deployment evidence/traceability,
a genuinely different concern from data-residency enforcement.

## Problem — real, concrete evidence from `config-deploy`

Read directly (`e:\sources\config-deploy\config\policies.yaml`,
2026-09-30):

```yaml
policies:
  - name: zone-isolation
    type: tenant_zone
    phase: plan
    enforcement: deny
    description: "Planned resources may only be created in regions belonging to a zone the tenant is permitted into"
```

The file's own comment states the mechanism precisely:

> Compares the region of every created/updated resource in the Terraform
> plan against the union of regions of the zones the tenant is permitted
> into. `zone -> regions` from `config/zones.yaml`, `tenant -> zones` from
> the tenant YAML (`spec.zones`). Skips silently when any of these are
> absent: no plan data, no zone configuration, no tenant context, or a
> tenant with no zone constraints. **It therefore has no effect until
> tenants exist.**

**Real-usage check, same discipline as every other gap in this repo**:
grepped every real YAML document in `config-deploy` for a populated
`zones:`/`geographies:` field on any tenant document — **none exists**.
Only `config/zones.yaml` (the zone→region mapping table) and a merged
`.strata/configuration.yaml` cache declare `zones` at all. This confirms
the policy's own graceful-degradation comment: `zone-isolation` is
declared, with `enforcement: deny` (would block a deploy on violation),
but is **currently a structural no-op** — no real tenant today has zone
constraints for it to check. Same "declared but not yet load-bearing"
shape as the audit trail's `journal`/`deployments.ndjson`, though for a
different underlying reason (waiting on tenant onboarding, not waiting on
a consumer).

## v1: real implementation (not just documented)

Confirmed by reading v1's actual source, not the doc catalog alone:

### The generic policy framework `tenant_zone` sits inside

`models/policy_model.py`'s `PolicyModel` (`name`, `type`, `phase`,
`enforcement: deny|warn|audit`, `configuration`, `enabled`,
`on_missing_data`) is one closed field vocabulary for **18 built-in
policy types** (`tenant_zone`, `required_labels`, `naming_pattern`,
`ref_convention`, `resource_type_restrictions`, `script`,
`cve_max_severity`, `cost_threshold`, `path_convention`, `layer_agreement`,
`checkov`, `opa`, `change_reference_required`, four `sbom_*` types,
`ai_review`), dispatched by `validators/policies/policy_engine.py`'s
`PolicyEngine._create()` factory — plus a plugin registration hook
(`.strata/policies/*.py` + `PolicyEngine.register_type()`) for custom
types. Every type shares one `BasePolicy.evaluate(context) -> PolicyResult`
contract and one `PolicyContext` dataclass carrying whatever data each
phase happens to have populated (`plan_data`, `sbom_components`,
`cost_data`, `cve_audit_result`, `change_reference`, etc.) — most fields
`None` for most policy types; each concrete policy checks for the ones it
needs and returns "skipped" gracefully when absent (`on_missing_data`,
ADR-0082).

**Confirms gap #7's own real-usage finding independently**: only 2 of
these 18 documented types are actually enabled in `config-deploy` —
`tenant_zone` (this doc) and `path_convention` (already resolved in v2 via
`PathConventionModel.enforcement`, no generic engine ported). The other 16
are real code, never configured by the one real consumer checked so far.

### `TenantZonePolicy.evaluate()` — the actual check (`validators/policies/tenant_zone_policy.py`)

Runs at phase `plan` only (`PolicyContext.plan_data` — `terraform show
-json <planfile>` output — is the phase this policy needs; the real
config's own comment states this is why `phase: plan`, never `validate`,
matters for this specific type). Sequence, each a graceful early return
("skipped", never a violation) when its input is absent:

1. **No `plan_data`** → skip (nothing to check before a plan exists).
2. **No `zone_regions` lookup** — built from
   `ConfigurationService.model.spec.zones` (a list of `{name, regions}`,
   normalized lowercase/no-spaces) → skip if empty (no zone config
   declared at all).
3. **No tenant context in the plan** — reads
   `plan_data["variables"]["strata_tenant"]["value"]`, a Terraform
   variable v1 itself injects carrying the tenant's own data *into* the
   plan, then reads it back *out* of the plan JSON → skip if absent.
4. **Tenant has no zone constraints** —
   `tenant_value["zones"]`/`tenant_value` itself (dict or list shape both
   handled) is empty → skip (this is the exact branch the real
   `config-deploy` config hits today, per the finding above).
5. **Build the allowed-regions set** — union of `zone_regions[z]` for
   every zone name `z` the tenant is permitted into.
6. **Walk `plan_data["resource_changes"]`** — for each entry whose
   `change.actions` includes `create`/`update` (a no-op resource, or one
   only being destroyed, is never checked), read
   `change.after.location` or `change.after.region` (whichever the
   resource type happens to populate — **this is a heuristic, not a real
   Terraform contract**: it works for the common `azurerm_*` shape and
   nothing guarantees any other provider's resources expose either key
   the same way), normalize, and compare against the allowed-regions set.
7. **Violation message** names the offending `{resource_type}.{resource_name}`,
   its actual region, and the tenant's permitted zone list.

`PolicyResult.passed=False` + `enforcement: deny` (from `PolicyModel`) is
what `PolicyEngine.has_denials()` checks to decide whether the pipeline
should actually block — the engine, not the individual policy, owns
"deny means stop."

### Where this is wired into v1's real command flow

Confirmed via grep, not assumed: `run_build_command.py`,
`base_deploy_command.py`, `run_deploy_command.py`, and a **standalone**
`strata policy check` command (`commands/policies/check_policy_command.py`)
all construct a `PolicyEngine`/`PolicyContext` and call `.evaluate(phase, ...)`.
`check_policy_command.py` is the introspection/dry-run surface — runs
policies for one or more `--phase` values against a deployment *without*
actually deploying, explicitly reporting which phases' context isn't
available yet (e.g. no plan file exists) rather than silently skipping,
and exits 3 if any deny-enforcement policy failed
(`has_validation_errors()` override, same convention `secret status`
uses in the values/secrets doc).

Distinct, smaller `controllers/policy_controller.py` exists too — but its
own docstring labels it "Phase 1 — introspection (`policy list`)" only;
`evaluate(phase, context)` is explicitly documented there as **Phase 2,
future** — i.e. this controller is not the real evaluation path at all,
`PolicyEngine` is. (Caught this distinction directly by reading both
files — an easy one to conflate since both have "policy" in the name.)

## v2: current state

- **No policy model, no policy engine, no `tenant_zone` equivalent at
  all.** `ConfigurationSpecModel` has no `policies` field (gap #7).
- **`zones` is not a gap** — already deliberately superseded (confirmed
  in `tenant_model.py`'s own docstring, point 4, and gap #7): v1's
  `config/zones.yaml` (name → regions mapping) becomes
  `ProviderConfigRegionModel.geography` (tag a provider's own declared
  region with a geography, e.g. `{name: westeurope, geography: europe}`)
  + `TenantSpecModel.geographies` (the tenant's allowed geography list).
  `TenantService.validate_geographies_against_provider_configs()`
  already checks a tenant's declared geographies exist among some
  provider's real regions.
- **That existing check is config-time, not plan-time — a different
  check from what `tenant_zone` needs.** `validate_geographies_against_
  provider_configs()` answers "does this geography exist at all,
  somewhere" (a static consistency check between two documents). It does
  not, and structurally cannot, answer "is *this specific planned
  resource*, in *this specific deploy run*, actually landing in a region
  the tenant is allowed into" — that needs the real Terraform plan's
  contents, which nothing in v2 reads today.
- **The one primitive that already exists: `TerraformIntegration.show()`**
  (`integrations/terraform.py`) — `"terraform show — decode a saved plan
  file. v1's show_plan step."` Real, present, callable. **Never called
  anywhere in `deploy_controller.py` or any other v2 code today** —
  `deploy_run()`'s `plan_result = integration.plan(...)` only checks
  `is_successful`/`stderr`, never decodes the plan's own contents.
- **No resource→region extraction convention exists.** v1's own
  `after.get("location") or after.get("region")` is a heuristic, not a
  verified-general Terraform contract — porting it as-is inherits the
  same fragility, worth deciding explicitly rather than silently
  replicating.
- **No `strata policy check` standalone command, no policy introspection
  command (`policy list`) of any kind.**

## The easy tier — a static, config-time equivalent is buildable today

Found while reviewing this doc further (2026-09-30): `tenant_service.py`'s
own `validate_geographies_against_provider_configs()` docstring already
names this exact next step, and flags its own blocker:

> This is the existence half. The stronger check — "the region this
> tenant's deployment actually targets is inside an allowed geography" —
> needs a `Deployment` binding a tenant to a workspace, **which is not
> built yet**.

**That blocker is gone.** `DeploymentModel` now has both `spec.tenant` and
`spec.workspace` (built after that docstring was written). A static,
config-time equivalent of the real governance question — "is this
deployment even *configured* to use infrastructure in an allowed
geography" — is buildable **now**, entirely inside `semantic_checks.py`,
reusing only already-resolved data:

1. `deployment.spec.tenant` → the `TenantModel` (already a resolved
   reference).
2. `deployment.spec.workspace` → `WorkspaceModel.spec.providers[]` (already
   resolved names) → each real `ProviderModel.spec.region`.
3. `ProviderService.validate_against_provider_config()` (already called
   by `_check_providers()`) already cross-checks that region against its
   `ProviderConfigModel.spec.regions[]` — the same regions carrying the
   `geography` tag `validate_geographies_against_provider_configs()`
   already reads.
4. Compare: is that provider's region's `geography` in
   `tenant.spec.geographies`?

No Terraform plan JSON, no `deploy_run()` changes, no per-resource-type
location heuristic, no new integration wiring — a new
`_check_deployment_tenant_geography()`-shaped function
(`semantic_checks.py`, same tier/pattern as `_check_deployment_layers()`),
reusing data every other Phase 2 check already resolves.

**What this tier does *not* give you** — worth being precise about, so it
isn't mistaken for a full `tenant_zone` port: it checks the *declared
configuration* (this deployment is wired to a provider in region X, which
is geography Y) is consistent with the tenant's allowed geographies. It
cannot catch a Terraform resource that overrides its own location/region
inline (a `provider {}` block's region is the common case, but nothing
stops a resource block from specifying its own `location` that disagrees)
— that's exactly the gap the real plan-JSON check exists to close, and
why v1 built it at `plan` time against actual resource attributes rather
than only at config time. The static check catches the overwhelmingly
common case (and the exact real `config-deploy` example: providers
declare one `region` each) for a fraction of the implementation cost; the
plan-time check catches the rest. **Not mutually exclusive — the static
check is worth having regardless of whether the full plan-time version is
ever built**, and is a natural, much smaller first phase if this is
picked up.

### The static tier's design — fully automatic, zero new config

Raised by the user ("design that system that if the required items are
defined the check just automatically happens") — no new field anywhere.
The check activates purely from data that already exists:
`deployment.spec.tenant`, `deployment.spec.workspace` ->
`workspace.spec.providers[]` -> each `ProviderModel.spec.region` ->
`ProviderConfigRegionModel.geography` -> `tenant.spec.geographies`. If any
link is genuinely absent, that is not a violation — it is "nothing to
check yet," the same graceful precedent
`validate_geographies_against_provider_configs()` already established.
Lives in `semantic_checks.py` as a new `_check_deployment_tenant_geography()`,
called from `_check_deployments()`'s existing per-deployment loop
alongside `_check_deployment_layers()` — same call site, same iteration
pattern (`resolved.get(entry.ref.name, ...)`, extends-aware), zero new
plumbing.

**Scenario matrix** — every input combination and its result:

| #   | Condition                                                               | Result                                                                                                                                                                                            |
| --- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `deployment.spec.tenant` unset                                          | **Skip** — shared/platform deployment, no tenant to constrain it (matches `DeploymentSpecModel.tenant`'s own docstring)                                                                           |
| 2   | Tenant name set but doesn't resolve in the index                        | **Skip** — reference-existence already reports this (`validate_references`); don't duplicate                                                                                                      |
| 3   | Tenant resolves, `spec.geographies` empty/unset                         | **Skip** — tenant declared no constraint (matches v1's real "tenant has no zone constraints" skip)                                                                                                |
| 4   | `deployment.spec.workspace` unset                                       | **Skip** — nothing to check against                                                                                                                                                               |
| 5   | Workspace name set but doesn't resolve                                  | **Skip** — reference-existence already reports this                                                                                                                                               |
| 6   | Workspace resolves, `spec.providers` empty/unset                        | **Skip** — no providers, nothing to check                                                                                                                                                         |
| 7   | A listed provider name doesn't resolve                                  | **Skip *that provider*** — reference-existence already reports this; keep checking the rest                                                                                                       |
| 8   | Provider resolves, but its `type` has no matching `ProviderConfig`      | **Skip *that provider*** — matches `_check_providers()`'s own existing "unregistered type… not existence" skip                                                                                    |
| 9   | Provider's `region` isn't in its `ProviderConfig.spec.regions[]` at all | **Skip *that provider*** — `_check_providers()` already reports this as its own error; don't duplicate                                                                                            |
| 10  | Region is valid, but that region entry has no `geography` tag           | **Skip *that provider*** — structurally unknowable, same "no provider declares any geography" grace `validate_geographies_against_provider_configs()` already uses. Deliberate, not an oversight. |
| 11  | Region's `geography` **is** in `tenant.spec.geographies`                | **Pass** — no finding at all                                                                                                                                                                      |
| 12  | Region's `geography` is **not** in `tenant.spec.geographies`            | **Violation** — `Severity.ERROR`, one per offending provider                                                                                                                                      |

**Multiple providers**: every provider on the workspace is checked
independently — never short-circuits on the first violation, matching
`_check_deployment_layers()`'s own "report every mismatch" behavior.

**Severity — fixed `Severity.ERROR`, no config toggle.** Follows directly
from "no config needed, fully automatic": there is no field anywhere to
hold a `warn`/`deny` choice, and data residency is the kind of governance
boundary that should fail closed rather than warn-and-continue (matches
the one real production config's own choice, `enforcement: deny`). No
escape hatch in this first cut — revisit only if a real need for one
surfaces.

**Violation message** names the deployment, the offending provider, its
region, its geography, and the tenant's allowed list — same substance as
v1's real message, adapted to config-time terms (no resource type/name,
since nothing was actually planned).

## What real building the *full* v1-faithful check would require (sizing, not a plan yet)

Substantially larger than `paths`/`security` (config-model-only
additions) — closer in shape to the audit-trail Layer 2 effort. This is
the plan-time, actual-resource-level check described above — the static
tier above is the cheaper alternative/complement, not a prerequisite:

1. A **scoped-down policy model** — not the generic 18-type
   `PolicyModel`/`PolicyEngine` (same "don't port the framework, model the
   one real check directly" call already made for `path_convention`
   becoming `PathConventionModel.enforcement`, and for audit's
   `event_overrides` avoiding the word "policy" entirely). Likely a
   `TenantZonePolicyModel` (or similar, name TBD) directly on
   `ConfigurationSpecModel`, `enforcement: warn|deny` only (matching
   `PathConventionModel`'s own precedent), no `phase`/`configuration`/
   `on_missing_data` generality unless a second real policy type ever
   shows up.
2. **Plan-JSON access wired into `deploy_run()`** — calling
   `integration.show(path, plan_file=..., json_format=True)` after a
   successful `plan()`, parsing the result, extending `deploy_run()`'s
   flow (or a wrapping module, matching Phase 5's `audit_run.py`
   precedent of not touching `deploy_run()`'s own internals) to run this
   check before `deploy()` is called for a `deny`-enforced policy.
3. **A resource→region extraction decision** — port v1's
   `location`/`region` heuristic as-is (simple, matches real behavior,
   inherits its fragility), or design something more principled. Worth
   deciding deliberately, not by default.
4. **Tenant/zone correlation using v2's real model** —
   `deployment.spec.tenant` → `TenantModel.spec.geographies` → resolve
   each geography to its member regions via every `ProviderConfigModel`'s
   `regions[].geography` tag (the reverse direction from how
   `validate_geographies_against_provider_configs()` already walks it,
   but the same underlying data — the static tier above already needs
   this same correlation, so it is not extra work the full tier adds).
5. **Decide the "no tenant zone constraints" default** — v1's
   graceful-skip-when-absent behavior means this check is currently
   inert for the one real consumer; matching that default (skip, not
   deny, when a deployment's tenant has no `geographies`) avoids
   surprising every existing deployment the moment this ships.
6. **Decide standalone `policy check`/`policy list` commands** — v1 has
   both; real usage evidence for either is not yet gathered (unlike
   `deploy run`'s Tier 1 critical-path confirmation) — check before
   assuming both are needed.

## Open Questions (deliberately unresolved — catalog only, no decisions made)

0. **Build the static tier, the full plan-time tier, both, or neither
   yet?** The "ahead of a concrete need" concern below applies much less
   to the static tier — it's cheap (one new `semantic_checks.py`
   function, no new machinery), catches the real common case
   (`config-deploy`'s providers each declare one `region`), and
   costs little even if no tenant has `geographies` populated yet (same
   "no constraints declared -> nothing to check" graceful skip the
   existing `validate_geographies_against_provider_configs()` already
   has). The full plan-time tier is the one where the "is this the right
   time" question in #1 below actually bites.
1. Given `tenant_zone` is currently a structural no-op in the one real
   consumer (no tenant has zone constraints yet), is this the right time
   to build the **full plan-time** check — or does building it now count
   as the same "ahead of a concrete need" pattern this repo's own
   discipline has deferred elsewhere (`journal`, `deployments.ndjson`,
   the `integration` sink arm)? Difference worth weighing: those were
   deferred because *nothing forwards to them*; this is deferred-by-the-
   real-repo's-own-current-tenant-configuration, which could change the
   moment a real tenant with zone constraints is onboarded — a genuinely
   different kind of "not yet needed."
2. Where does the plan-JSON-reading + policy-check step actually live —
   inside `deploy_controller.py::deploy_run()` itself (needs the plan
   file path, which the existing loop already has, right after
   `integration.plan()` succeeds and before `integration.deploy()` runs),
   or a new wrapping module (matching `audit_run.py`'s "don't touch
   `deploy_run()`'s internals" precedent from the audit-trail work)? The
   audit precedent wrapped *after* the whole run because it only needed
   the final outcome; this needs to intervene *mid-run*, per-step, before
   `deploy()` — a materially different integration shape that may not
   tolerate the same "wrap, don't touch" approach. (Only relevant to the
   full plan-time tier — the static tier needs none of this.)
3. Does a `deny` violation here abort only that one step, the whole
   `deploy run` invocation, or something in between (matching the
   existing per-step fail-fast `deploy_run()` already does for a failed
   `plan()`/`deploy()` call)? (Full plan-time tier only — the static
   tier's `deny` is a `require_valid()`-time failure, same as every other
   Phase 2 check, no new question to answer.)
4. Same `required`/graceful-degradation question the audit design already
   worked through for push failures: should a mid-plan policy-check
   failure (as opposed to a policy *violation*) — e.g. malformed plan
   JSON, an unexpected schema shape — warn or hard-fail, and is that a
   per-policy-type choice or fixed? (Full plan-time tier only.)
5. Is `strata policy check` (the standalone dry-run command) real-usage
   justified, or Tier-2-priority the same way `deploy status/history` are
   in the rebuild-order memory? No evidence gathered yet either way.

## Related Decisions

None yet — `docs/decisions/` has no v2 ADR for policies. Source material
is entirely v1's own real code (external, not part of this workspace):
`models/policy_model.py`, `controllers/policy_controller.py`,
`validators/policies/policy_engine.py`, `validators/policies/base_policy.py`,
`validators/policies/tenant_zone_policy.py`,
`commands/policies/check_policy_command.py`. No v1 ADR reference found for
this subsystem in the files read so far.

## Changelog

- 2026-09-30: Created — split out from `docs/work/gap_fit_v1.md` gap #7's
  `policies` bullet and `docs/work/audit-trail.md` (which this is
  explicitly *not* part of), per direct request ("create a new document
  for this, this is not really part of the audit"). Catalogs v1's real
  `tenant_zone` implementation (`policy_model.py`/`policy_engine.py`/
  `base_policy.py`/`tenant_zone_policy.py`/`check_policy_command.py`, all
  read directly) against v2's current zero-equivalent state, and sizes
  the gap as substantially larger than `paths`/`security` (needs real
  Terraform plan-JSON access, which exists as an unused primitive
  — `TerraformIntegration.show()` — but is wired nowhere in v2 today).
  Found and recorded a real, load-bearing discrepancy the design must
  weigh: `config-deploy`'s real `zone-isolation` policy is declared
  with `enforcement: deny` but is currently a structural no-op (no real
  tenant has zone constraints populated yet), confirmed by grepping every
  real YAML document in that repo. Five open questions recorded,
  deliberately left unresolved — no v2 design decisions made yet.
- 2026-09-30: Found a much cheaper, buildable-today static tier, per
  direct challenge ("the easy strata check: does deployment matches
  geography can match easier i assume"). `tenant_service.py`'s own
  `validate_geographies_against_provider_configs()` docstring already
  named this exact next step and its blocker — "needs a `Deployment`
  binding a tenant to a workspace, which is not built yet" — and that
  blocker is gone (`DeploymentModel` now has both `spec.tenant` and
  `spec.workspace`). Added "The easy tier" section: a new
  `semantic_checks.py` function correlating `deployment.spec.tenant` ->
  `deployment.spec.workspace` -> each real `ProviderModel.spec.region`'s
  geography against the tenant's `spec.geographies`, reusing only
  already-resolved data and the existing `_check_providers()`/
  `validate_geographies_against_provider_configs()` machinery — no
  Terraform plan JSON, no `deploy_run()` changes. Explicitly scoped what
  it does *not* catch (an inline resource-level region override) to
  avoid it being mistaken for a full `tenant_zone` port. Added Open
  Question #0 distinguishing which open questions apply to which tier —
  #2-4 (deploy_run() wiring, mid-run abort semantics, failure-vs-
  violation handling) apply only to the full plan-time tier; the static
  tier has none of them, since a violation is just an ordinary
  `require_valid()`-time error like every other Phase 2 check. Updated
  the status line to reflect the two-tier framing.
- 2026-09-30: Fully designed the static tier's mechanics, per direct
  request ("design that system that if the required items are defined
  the check just automatically happens" / "lets look at the different
  scenarios and what the result would be"), before any implementation.
  Added "The static tier's design" section: confirmed the exact wiring
  point (`_check_deployments()`'s existing per-deployment loop, same call
  site as `_check_deployment_layers()`, extends-aware for free); a full
  12-row scenario matrix covering every unset/unresolved/untagged/
  mismatched combination and its result (skip vs. per-provider skip vs.
  pass vs. violation); confirmed multiple providers are checked
  independently, never short-circuiting on the first violation (matches
  `_check_deployment_layers()`'s own behavior); and settled severity as a
  fixed `Severity.ERROR` with no config toggle at all — follows directly
  from the "fully automatic, zero new config" framing, since there is no
  field anywhere to hold a warn/deny choice, and matches the one real
  production config's own `enforcement: deny` choice. Status line updated
  — static tier is now fully designed and ready to implement.
- 2026-09-30: **Implemented the static tier**, per direct request
  ("design, plan, and implement the check. defer the provisoiner check.")
  — the full plan-time tier's Terraform-plan/resource-level check
  (referred to there as the "provisioner check") is explicitly deferred,
  not built. New `_check_deployment_tenant_geography()` in
  `semantic_checks.py`, wired into `_check_deployments()`'s existing
  per-deployment loop exactly as designed — no new plumbing, extends-aware
  for free. Implements the full 12-row scenario matrix as designed, fixed
  `Severity.ERROR`, one finding per offending provider.

  **Found and fixed a real model error while wiring up tests for the
  "tenant has no constraint" skip branch**: `TenantSpecModel.geographies`
  was `list[str]` with `min_length=1` — *required*, non-empty — which
  structurally contradicts the design's own scenario #3 ("tenant resolves,
  `geographies` empty/unset → skip") and the doc's own real-usage finding
  (no tenant in the one production consumer checked has `geographies`
  populated at all). A tenant could never actually be in the "no
  constraint" state the new check's skip branch exists to handle. Fixed:
  `geographies: list[str] | None = Field(default=None, ...)`; guarded
  `TenantSpecModel.validate_unique_geographies()` and
  `TenantService.validate_geographies_against_provider_configs()` for the
  new `None` case (both now `if self.geographies:`/`... or []`). Corrected
  two tests that had encoded the old, wrong requirement:
  `test_models_tenant.py::test_tenant_requires_at_least_one_geography`
  (replaced with `test_tenant_geographies_is_optional`/
  `test_tenant_accepts_empty_geography_list`) and
  `test_semantic_checks.py::test_tenant_with_no_geographies_skips_geography_check`
  (previously asserted schema *rejection* instead of testing the skip
  branch it was named for — rewritten to actually prove the skip, using
  the same real-mismatch-that-should-be-caught-but-isn't pattern the
  sibling `test_deployment_with_no_tenant_skips_geography_check` already
  uses). Full check suite green: mypy (121 files), ruff, import-linter,
  pytest (1620 passed — same pre-existing unrelated `config/` drift as the
  sole failure). Sphinx rebuilt clean.
