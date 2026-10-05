# v1 -> v2 Coverage Gaps

- Status: living — update in place as gaps are closed or new ones are found
- Last updated: 2026-10-02 (gap #19 added — composite/mergeable variable
  values, Azure Application Gateway + WAF multi-customer composition)

## Overview

Tracks concrete v1 -> v2 schema/feature gaps found while hand-migrating real
production strata documents to v2. Unlike `docs/decisions/` (immutable ADRs)
and `docs/design/` (per-component design docs), this is a flat working list
— one entry per gap, closed out or superseded in place as v2 grows to cover
it. Each entry should say *what* is missing, *where it was found* (a real
document, not a hypothetical), and whether it's a deliberate ADR-backed
deferral or an open gap nobody has decided about yet.

## How gaps get added here

When porting or validating a real v1-derived document against v2 and
something doesn't fit, add an entry below instead of only noting it in
session/repo memory — memory is conversation-scoped, this file is the
durable, reviewable record.

## Gap list

### 1. ~~Module `spec.references.secrets` has no v2 equivalent~~ — RESOLVED

- **Found in:** every haven module that declared a `references.secrets`
  list (e.g. `authentik.yaml`, `vaultwarden.yaml`, `homarr-secrets.yaml`).
- **Status:** closed (2026-09-28). The *field choice* was always deliberate
  (ADR-0002) and confirmed by
  [docs/design/value-token-resolution.md](value-token-resolution.md)'s
  Per-Kind Status table: v1's declared-keys list is replaced by inline
  `${secret:KEY}` value tokens at the point of use
  (`ModuleServiceEnvironmentModel.value` — the only field the four
  dedicated per-kind validators cover for Module, though `configuration`/
  `custom` is now also token-aware for every kind, gap #8). **Rewriting to
  the correct field now fully works, too** — gap #9's deploy-time resolver
  (all 7 phases) reaches `ModuleServiceEnvironmentModel.value` via Helm
  `--set-string`/Compose bare-`${KEY}` env-var injection, both
  implemented and tested. So this is correct at the schema level and now
  also functional at the runtime level.
- **Closed by:** gap #9 (fully implemented 2026-09-28) — the resolver
  reaching `ModuleServiceEnvironmentModel.value` via Helm `--set-string`/
  Compose env-var injection was both necessary and sufficient here, and is
  done. Gap #8 (`configuration:`/`custom:` passthrough) doesn't apply,
  since this entry's own migration action already avoids that field; gap
  #10 (`strata validate` can't detect an unresolvable token) is also now
  closed, a discoverability improvement, not a functional prerequisite.
- **Migration action:** rewrite every `environment[].secret: KEY` entry to
  `environment[].value: "${secret:KEY}"` under `spec.services[]` — this is
  the schema-correct target, and gap #9's resolver now actually delivers
  it to a deployed container/pod. If the target chart's values shape
  doesn't fit `spec.services` at all (a third-party chart with fixed
  top-level keys — see gap #8's `immich.yaml` example), use raw
  `configuration`/`custom` instead — also token-aware now (gap #8).

### 2. ~~Module `source.chart_repository` (inline Helm registry URL) has no v2 equivalent~~ — RESOLVED

- **Found in:** every helm-chart-registry module in haven (`cert-manager`,
  `gatus`, `homarr`, `immich`, `jellyfin`, `nextcloud` — 6 real modules).
- **Status:** deliberate design (ADR-0015 — remotes declared once,
  referenced by name), confirmed **not** Terraform/git-specific:
  `RemoteType.HELM` is a first-class value, and `SolutionRemoteModel`'s own
  docstring justifies the whole pattern by citing **Helm's own
  `repositories.yaml` + `@alias` dependency syntax** as one of its four
  precedents (with Flux, Bazel, Nix). A `HELM`-type remote is also already
  lighter than a `git`/`oci` one — it doesn't require `reference` (only
  `GIT`/`OCI` do), since the real version pin lives on the module's own
  `chart_version`.
- **But the payoff genuinely doesn't materialize for haven** — checked real
  usage: all 6 chart remotes are each referenced by **exactly one** module,
  zero sharing. The design's benefit ("one remote, one ref, so two modules
  can't silently pull different versions of the same source") only pays off
  when a registry is actually shared across consumers; with a strict 1:1
  mapping every one of haven's real modules has, it's indirection with no
  corresponding payoff — not a modeling mistake, just a real-world case this
  pattern wasn't optimizing for (an internal chart museum feeding many
  modules is the case it helps).
- **Decision (2026-09-28): Direction 1 — inline `SourceModel.chart_repository`,
  as an *alternative* to `remote:`, not a replacement.** Direction 2 (route
  chart registry location through the `artifact` kind) was investigated and
  rejected: `ArtifactModel.registry`'s own docstring is explicit that the
  field is "free text for documentation only... NOT a `SolutionRemoteModel`
  reference, since strata never fetches an image" — i.e. it is inert.
  Helm's registry location is the opposite: `HelmIntegration._resolve_chart()`
  actively reads it to run `helm repo add` and build the chart-ref argument
  for `helm upgrade`. Reusing `artifact.registry` for charts would make the
  same field mean two different things (inert for images, actionable for
  charts) — a discriminated-union smell the schema was already deliberately
  avoiding once (its docstring already rejected a "chart mode" for the
  narrower reason of version-pinning, ADR-0026; this is a second, independent
  reason pointing the same way).
- **Implemented:**
  - `SourceModel.chart_repository: str | None` (`common_models.py`) — raw
    Helm repo URL or `oci://` reference. Chart-based mode (`chart_name` set)
    now requires **exactly one** of `remote` or `chart_repository`; both
    together, or `chart_repository` on a git-based source, are rejected by
    `validate_source_mode()`. Unlike `remote`, this is never validated
    against the solution manifest — a typo surfaces only when `helm repo
    add` itself fails, not at schema time (documented in the field itself).
  - `HelmIntegration._render_meta()` (`helm.py`) — writes `chartRepository`
    to the build-time `meta.yaml` artifact when `source.chart_repository` is
    set (mutually exclusive with `chartRemote`, mirroring the schema).
  - `HelmIntegration._resolve_chart()` (`helm.py`) — now resolves a chart ref
    from either `chartRemote` (existing path, needs `remotes=`) or the new
    `chartRepository` (no solution-level declaration needed at all). Both
    branches share a new `_chart_ref_from_url()` helper for the
    OCI-vs-`helm repo add` branching, so the logic isn't duplicated between
    the two sources.
  - Tests: `tests/strata/models/test_models_common.py` (new `SourceModel`
    section — valid git/chart modes, both mutual-exclusivity rules, the
    "requires exactly one of" rule), `tests/strata/integrations/
    test_integrations_helm.py` (`_render_meta()` writing `chartRepository`,
    `_resolve_chart()`'s OCI and HTTP inline-repository paths, an end-to-end
    `deploy_namespace()` test with no `remotes=` passed at all), plus one
    pre-existing `test_models_module.py` test updated for the new error
    message. Full check suite green: mypy (107 files), ruff, import-linter
    (1 kept, 0 broken), pytest (1236 passed).
- **Migration action:** for haven's 6 single-consumer chart modules, prefer
  `source.chart_repository` inline over a `spec.remotes` entry + `source.remote`
  — no `strata.yaml`-level declaration needed at all now. Keep using
  `remote:` only where a registry is genuinely shared across modules or
  needs credentials (`SolutionRemoteModel.integration`).


### 3. ~~OCI-type remotes require a `reference`, but an OCI chart index has no natural one~~ — RESOLVED

- **Found in:** `strata.yaml`'s `jetstack`/`homarr-charts`/`immich-charts`
  remotes (all `type: oci`).
- **Status: closed (2026-09-29).** `SolutionRemoteModel` used to require
  `reference` for every git/oci remote uniformly, but for a chart registry
  the actual version pin lives on `chart_version` at the module's `source`
  (module-level, not remote-level) — there is no single meaningful "ref" for
  the whole registry the way a git branch/tag/commit is.
- **Relationship to gap #2:** related, but not closed by it. Gap #2 chose
  Direction 1 (inline `chart_repository` as an *alternative* to `remote:`,
  not a replacement — `remote:` stays required for shared/credentialed
  registries), which only shrinks how often this is hit (fewer `type: oci`
  remotes need declaring at all now), it doesn't fix the underlying schema
  rule. This gap is also broader than charts — `RemoteType.OCI`'s own
  docstring says OCI "serves container images, Helm charts and arbitrary
  artifacts alike," so a non-chart OCI source with no natural single ref
  could hit the same friction regardless of gap #2.
- **Design/Implementation (2026-09-29):** split the rule the same way gap
  #2 split `SourceModel`'s own mode selection — what the field *allows* is
  a Phase 1 (schema-only) check; what it *requires* depends on how the
  remote is actually used elsewhere in the solution, which needs the loaded
  manifest, so that half is a new Phase 2 check. Considered and rejected a
  simpler always-optional relaxation (drop the requirement for every OCI
  remote, chart-serving or not) — rejected because it silently loses real
  pinning for the non-chart case (a genuine container image/blob remote,
  which `RemoteType.OCI`'s own docstring says is just as valid a use as
  charts): that remote losing its immutable-digest guarantee would be a
  real regression, not a convenience.
  1. **Phase 1 (`SolutionRemoteModel.reference`, `solution_model.py`):**
     `reference` is now required unconditionally only for `git`; merely
     *optional* (not forbidden) for `oci`; still forbidden for
     `helm`/`local` (unchanged).
  2. **Phase 2 (new — `semantic_checks._check_remotes()`):** wired into
     `run_semantic_checks()` as its 8th check (which now also takes
     `solution: SolutionModel | None`, since the manifest's own kind is
     never indexed — same reason `check_version_pins()` already takes it
     directly). Collects every `SourceModel` in the solution (`ModuleModel.
     spec.source` + every `WorkspaceModel.spec.provisioners[].source`); for
     each `type: oci` remote with `reference is None`, any consumer naming
     it via `remote:` with `chart_name` unset (git-based/non-chart mode)
     raises an `oci_remote_missing_reference` error naming the remote and
     that consumer's `source_path`; a chart-based consumer, or zero
     consumers at all, is valid as-is (an unused remote is a separate,
     still-unbuilt gap — not this check's concern).
  - Ended up simpler than the original design sketch's naming: implemented
    as a standalone function in `semantic_checks.py` (matching
    `check_version_pins()`'s own "controller-level function scanning
    multiple kinds against the manifest" shape) rather than a
    `SolutionRemoteService` method — there's no single natural "referencing
    kind" here the way `WorkspaceService.validate_topology_references()`
    has one (this check's two consumer kinds, Module and Workspace, are
    peers, not a service validating itself against another document).
  - **Verified:** full check suite green (mypy 107 files, ruff clean,
    import-linter 1 kept/0 broken, pytest 1243 passed — 7 new tests: 2 in
    `test_models_solution.py` for the relaxed Phase 1 rule, 5 in
    `test_semantic_checks.py` for the new Phase 2 check, covering
    chart-based/git-based Module consumers, a Workspace provisioner
    consumer, a remote with `reference` already set, and the no-remotes
    no-op case). `strata validate` against `.v2-haven/` confirmed to add
    zero new findings (its pre-existing 44 `malformed_value_token` errors,
    unrelated to remotes, were confirmed via `git stash` to already exist
    on the unmodified branch).
- **Migration action:** none needed — an OCI remote already omitting
  `reference` (or a fresh `chart_repository`-based module needing no remote
  at all, gap #2) now validates as long as every real consumer is
  chart-based; a genuine non-chart OCI consumer still needs a real pin,
  exactly as before.

### 4. ~~`default_tags` is REQUIRED on Resource/Firewall; `default_labels` is REQUIRED on Module/Namespace~~ — RESOLVED

- **Found in:** every haven resource (`haven_vm_hetzner_hearth`/`_forge`),
  firewall (`haven_fw_hetzner_hearth`/`_forge`), module (all 30), and
  namespace (all 8) document — none of them carry tags/labels in the real
  v1 repo.
- **Status: closed (2026-09-28).** Made `default_tags`/`default_labels`
  optional (`| None = None`) on every kind that had them required:
  `Resource`, `Firewall`, `Module`, `Namespace` — plus, per the broadened
  request ("not all systems can handle tags, check each schema"),
  `Network` and `DNS`, which had the identical pattern but weren't hit by
  haven specifically (haven has no `network`/`dns` documents). Checked
  every kind with a `default_tags`/`custom_tags`/`default_labels`/
  `custom_labels` field (`Provider`, `Workspace` included): `custom_tags`/
  `custom_labels` were already optional everywhere; `Provider`/`Workspace`'s
  `default_tags` were already optional too — only `Resource`, `Firewall`,
  `Module`, `Namespace`, `Network`, `DNS` needed the change.
- **Verified:** full check suite green (mypy 107 files, ruff clean,
  import-linter 1 kept/0 broken, pytest 1137 passed — one test,
  `test_resource_missing_default_tags_is_invalid`, asserted the old
  required behavior and was rewritten to
  `test_resource_missing_default_tags_is_valid`). `strata validate` against
  `.v2-haven/` still passes clean (52 documents). `docs/config/{resource,
  firewall,module,namespace,network,dns}.md` updated to drop the
  "(required)" wording; Sphinx build re-verified clean.
- **Migration action:** none needed anymore — a v1-derived document with no
  tags/labels at all now validates as-is. Synthesizing a baseline (e.g.
  `{managed-by: strata}`/`{app: <name>}`) is optional polish, not required
  to pass validation.

### 5. Workspace/Topology/Provisioning decoupling (ADR-0011, revised ADR-0028)

- **Found in:** haven's single monolithic `config/stack/workspace.yaml` —
  topology + resources + namespaces + firewalls + provisioners all nested
  together, with `provisioner`/`topology` bound per-stage in
  `deployment.yaml`.
- **Status:** deliberate (ADR-0011) — not an open gap, converts cleanly.
  **Revised 2026-10-01 (ADR-0028):** originally documented as "one v1
  document becomes 4 v2 documents" (`Workspace` + 2 standalone `Topology`
  documents + an explicit `execution:` step recipe) — ADR-0028 reverted the
  standalone-`Topology`-document half of ADR-0011 (zero real cross-workspace
  reuse found; the inverted reference direction `TopologyComponentModel.resource`
  always had was a real structural cost with no offsetting payoff). Topology
  grouping is back to being inline on `Workspace.spec.topology[]`, same
  place v1 had it — so this is now genuinely **less** work than first
  documented: one v1 document becomes 1 v2 document (`Workspace`, topology
  inline) plus the still-real `execution:` step recipe replacing v1's
  per-stage `topology:`/`provisioner:` binding. The provisioning-decoupling
  half of ADR-0011 (grouping independent of tooling) is unaffected and still
  the real, non-mechanical part of this migration.
- **Migration action:** no shortcut on the provisioner/execution-recipe
  half — a migration guide/tool still needs to spend real explanation
  budget there. The topology-splitting half that used to need its own
  explanation no longer exists as a step at all.
- **Migration guide written 2026-09-30, updated in place 2026-10-01**:
  [docs/how-to/migrate-v1-workspace-topology-provisioning.md](../how-to/migrate-v1-workspace-topology-provisioning.md)
  — step-by-step, built from the real `.v2-cfg/workspaces/spoke.yaml`
  example (that fixture itself still uses the pre-ADR-0028 standalone
  `topologies/spoke-cluster.yaml` split on disk — not migrated inline,
  since `.v2-cfg` is a local, gitignored, not-test-covered fixture and
  Phase 5 of the ADR-0028 reversal deliberately skipped it; the guide's own
  YAML shown inline is the authoritative, up-to-date shape), plus a
  checklist for migrating any other real v1 workspace through the same
  split.

### 6. ~~Deployment stage `scope: infra|apps` has no v2 field~~ — WAS ALREADY WRONG, RESOLVED

- **Found in:** haven's real `deployment.yaml` stages (used by
  `.github/workflows/deploy-infra.yml`'s `--scope infra`).
- **Status: closed (2026-09-28) — this entry was stale, not open.** Checked
  directly, per request ("this is moved to workspace right"): `scope`
  **is** a real v2 field — `ProvisioningStepModel.scope`
  (`provisioning_model.py`), deliberately placed on the *workspace's*
  execution step, not `DeploymentStageModel`. Its own docstring already
  explains why: "whether a step is 'infra' or 'apps' is a fact about what
  the step does, not about which environment/deployment runs it." `strata
  deploy run --scope <label>` already filters on it
  (`deploy_controller.py`: `steps = [s for s in steps if s.scope ==
  scope]`), with a real, passing unit test
  (`test_deploy_run_filters_by_scope`). This entry and
  `deploy-command.md`'s own "Missing: `scope`" note (in its `DeploymentStageModel`
  field list) were both stale — true only in the narrow sense that
  `DeploymentStageModel` itself lacks it, which was never the intended home.
- **The actual gap was in the haven fixture, not v2**: `.v2-haven/workspaces/
  haven-platform.yaml`'s `execution[]` steps had no `scope:` set at all, so
  haven's real `--scope infra` CI usage wasn't exercised. Fixed: added
  `scope: infra` to `provision-hearth`/`provision-forge`, `scope: apps` to
  `deploy-apps` — matches v1's real `infrastructure_hearth`/
  `infrastructure_forge`/`applications_forge` stage scoping. `strata
  validate` still passes clean (52 documents).
- **Migration action:** none — set `scope` directly on the relevant
  `WorkspaceSpecModel.execution[]` entries (not on the deployment's
  `stages[]`), same label vocabulary as v1 used (free-form, e.g.
  `infra`/`apps`).

### 7. Configuration's `integrations`/`security`/`zones`/`remotes`/`audit`/`policies`/`paths` subtree remains unported

- **Found in:** haven's real `config/configuration.yaml` (`spec.integrations`
  — git/terraform/infisical tool declarations with capabilities/validation/
  authentication — plus `spec.deployment.properties` schema).
- **Status:** split apart 2026-09-29 after reading `config-deploy`'s
  real, current `config/*.yaml` directly (7 files: `base`/`stores`/`zones`/
  `policies`/`audit`/`paths`/`promotions`/`remotes`) — this entry's original
  blanket "all deferred" framing was too coarse. Per-field status now:
  - `security` (`allowed_secret_stores`/`allowed_variable_stores`/
    `allowed_feature_stores`) — **RESOLVED 2026-09-29.**
    `ConfigurationSecurityModel`/`ConfigurationSpecModel.security` +
    `EnvironmentService.validate_allowed_stores()` (wired into
    `semantic_checks.py`'s `_check_environments()`) now enforce this — see
    the Changelog entry below. The one field here with no v2 equivalent to
    derive it from elsewhere, unlike the three below.
  - `zones` — **NOT A GAP**, confirmed while investigating this entry: `zones` was
    already deliberately superseded, differently, by `TenantSpecModel.
    geographies` + `ProviderConfigRegionModel.geography` (see that model's own
    docstring, point 4) — `TenantService.validate_geographies_against_provider_configs()`
    already implements the real cross-check v1's `config/zones.yaml` exists
    for. Migration action: tag each real region with the matching
    `geography` value in its `ProviderConfig` document (e.g. `westeurope`/
    `northeurope` -> `geography: europe`, matching `zones.yaml`'s real
    groupings) instead of authoring a separate `zones:` section anywhere.
  - `remotes` — **NOT A GAP**, confirmed while investigating this entry:
    `SolutionRemoteModel`'s own docstring already made this decision
    deliberately (bootstrap ordering — Configuration itself can live in a
    remote) and its `RemoteFetch.EXTERNAL` value already covers the exact
    real case `remotes.yaml`'s `type: bundled` git-repos-checked-out-by-CI
    entries need. Migration action: `config/remotes.yaml`'s content maps to
    `strata.yaml`'s `spec.remotes`, not to any `kind: configuration`
    document — a placement difference from v1, not a missing feature.
  - `integrations` (the embedded-list style, e.g. `config/stores.yaml`'s
    `spec.integrations: [{name: terraform, type: terraform, ...}]`) — still
    open, but now a **shape gap, not a modeling gap**: ADR-0021 already
    built the equivalent capability as a standalone `kind: integration`
    document (deliberately, not an embedded list — see that ADR's D3).
    Migration action: convert each embedded list entry into its own
    `kind: integration` document; no new v2 code needed for this specific
    shape translation.
  - `paths` — **RESOLVED 2026-09-30** (structural half only —
    see [docs/design/path-conventions.md](path-conventions.md)).
    New `PathConventionModel`/`PathSegmentModel`/
    `ConfigurationSpecModel.paths` (`pattern` for directory shape,
    independently optional `filename_pattern` for filename shape — a
    redesign from the first 2026-09-29 pass, which combined both into one
    full-path field; real usage needs both split, e.g. `provider-path`'s
    one real capture lives entirely in the filename with no directory
    capture at all), `strata/utils/path_conventions.py` (pure regex
    matching, no Pydantic dependency), `semantic_checks.py`'s
    `_check_paths()`. Also finally activates `DeploymentLayersModel`
    (previously "inert in v2"): `_check_deployment_layers()` cross-checks
    a declared `layers.segments` against the values captured from the
    deployment document's own real directory. v1's generic `validate:`
    sub-block (arbitrary YAML-expression/file-existence cross-checks) is
    deliberately not ported — both its real instances are already covered
    by more precise v2 checks elsewhere (`ProviderConfig` region
    cross-check, `Tenant` reference existence).
  - `policies`/`promotions` — still fully open, unstarted. `audit` —
    **RESOLVED 2026-09-30** (Layers 1+2 only — see
    [docs/work/audit-trail.md](audit-trail.md)). `strata sln init`/
    `update` scaffold the real PR/issue templates (Layer 1), and `deploy
    run` now writes a durable `_manifest.json`/`_metrics.json` per
    execution with optional `git`-sink distribution (Layer 2's full
    5-phase Implementation Plan, all shipped). Layer 3 (CLI reporting:
    `audit changes`/`diff`/`resend`/`status`) and the `integration` sink
    arm's actual dispatch (SIEM/webhook forwarding) remain unbuilt — no
    evidenced consumer for either yet, deferred deliberately, not an
    oversight. Real active usage is narrower than the documented feature
    catalog for the two still-open fields (checked directly, 2026-09-29):
    only 2 of `policies.yaml`'s many documented policy `type`s are
    actually enabled (`tenant_zone`@phase `plan`, `path_convention`@phase
    `validate` — the latter is now what `_check_paths()` implements,
    without the generic phase/enforcement-dispatching policy framework
    around it); `promotions.yaml` declares a `progressions.standard` ring
    sequence but no active `strategies` entry. `policies` now has its own
    catalog doc — [docs/work/tenant-zone-policy.md](tenant-zone-policy.md)
    (2026-09-30) — scoped to `tenant_zone` (the one real, active-by-
    declaration remaining type), not the full 18-type documented catalog;
    catalog only so far, no v2 design decisions made yet. That doc also
    found a real wrinkle worth restating here: `zone-isolation` is
    declared with `enforcement: deny` in the real repo, but is currently
    a structural no-op — no real tenant has zone constraints populated
    yet, confirmed by grepping every real YAML document there.
- **Migration action:** see per-field notes above; `policies`/
  `promotions` still drop with a comment pointing here until built.


### 8. ~~Value tokens inside `spec.configuration`/`spec.custom` passthrough dicts are never resolved~~ — RESOLVED

- **Found in:** `modules/immich.yaml` (`configuration.controllers.main.
  containers.main.env.DB_PASSWORD: "${IMMICH_DB_PASSWORD}"` — deliberately
  using raw `configuration` instead of `spec.services` per that module's own
  comment, since this chart's top-level keys are fixed by the chart and
  don't fit strata's per-service shape); `modules/nextcloud-secrets.yaml`,
  `modules/homarr-secrets.yaml`, `modules/grimoire-secrets.yaml`, and
  `modules/pgadmin.yaml` (all using a bare `${VARNAME}` placeholder inside
  `configuration.env`, not even `${secret:KEY}` syntax).
- **Status:** closed. Malformed-token *detection* (Phase 1, below) is
  done; a module's own `configuration`/`custom` resolves for real, as a
  side effect of gap #9's Helm/Compose delivery (Phases 4/5); the
  Terraform-side resolution gap (`resources_by_category`/`topologies`/
  `properties`/`custom`/`tenant`, Phase 6) is implemented too (see the
  amendment further down). Originally: open gap, confirmed in source, more specific than gap #1's
  passing mention that `configuration` "is not validated either way" —
  it is not just unvalidated, it is **never walked for tokens at all**.
  `resolve_value_tokens_in_mapping()` is only ever called on
  `provisioner.backend.configuration` (`deploy_controller.py`);
  `HelmIntegration.prepare_namespace()` does a raw
  `values.update(module.spec.configuration)` with no resolution step. A
  `${secret:KEY}`/`${var:KEY}` token placed in a module's (or namespace's/
  firewall's/resource's) `configuration`/`custom` dict is written verbatim
  into the rendered artifact — it does not become the real value.
- **Telling detail:** `modules/gatus.yaml` has an explicit comment noting
  its own bare `${TOKEN}` placeholders are Gatus's own chart-native
  substitution mechanism, unrelated to v2's tokens — a deliberate, documented
  choice. The four modules above have no such comment, so it's unclear
  whether their bare `${VARNAME}` is the same intentional pattern
  (undocumented) or a plain oversight where `${secret:KEY}` was meant.
  Either way, today neither form actually resolves through strata.
- **Design completed 2026-09-28** (see
  [docs/design/value-token-resolution.md](value-token-resolution.md)'s
  "unify gaps #8/#9/#10" section): extend `resolve_value_tokens_in_mapping()`
  (already field-agnostic) to `configuration`/`custom` call sites too — not
  a new algorithm, just new call sites, decided in favor of option (a) from
  the original two below.
- **Migration action:** none available yet, implementation not started —
  needs a design decision: either
  (a) extend token resolution to `configuration`/`custom` dicts generally
  (a real feature, not just a migration fix), or (b) explicitly document
  `configuration`/`custom` as raw-only passthrough and give charts that need
  secrets in fixed, non-`services`-shaped chart values a different
  resolution path.
- **Amendment 2026-09-28 — Phase 1 (`validate_value_tokens()`) is also not
  generic, and generalizing it needs an escape syntax first:** Phase 2
  (`unresolved_value_tokens()`) already walks the *whole* document
  generically (gap #10 confirms this), but Phase 1's malformed-token check
  is wired per-field only (`dns_model.py`, `module_model.py`,
  `network_model.py`/`firewall_model.py` via `validate_cidr_or_token()`) —
  a `configuration`/`custom` dict never runs it. Making Phase 1 generic too
  (a `model_validator(mode="after")` on `PlatformBaseModel` walking every
  string leaf) would actually catch this gap's own real modules for free:
  `_VALUE_TOKEN_CANDIDATE_PATTERN` (`\$\{[^}]*\}`) already matches a bare
  `${IMMICH_DB_PASSWORD}`-shaped string even though it has no `kind:`
  prefix, so a generic Phase 1 pass would reject it as malformed today,
  forcing the fix to real `${secret:KEY}` syntax. **But** `modules/gatus.yaml`
  proves this can't be unconditional: its bare `${TOKEN}` is Gatus's own
  chart-native substitution, not a strata token, and a generic Phase 1
  would wrongly reject that already-working document — a real regression,
  not hypothetical. Generalizing Phase 1 therefore needs an escape/skip
  syntax first, designed in
  [docs/design/value-token-resolution.md](value-token-resolution.md)'s
  new "Escape syntax" section — not implemented yet, design only.
- **Phase 1 implemented 2026-09-28** (design validated against source
  first, then built — see the design doc's "Full Solution" Phase 0/1):
  `unresolved_value_tokens()` (`environment_service.py`) now also flags any
  malformed `${...}`-shaped candidate found anywhere in a document's walk
  — `configuration`/`custom` passthrough dicts included — via the new
  `find_malformed_value_tokens()`/`strip_escaped_value_tokens()`
  (`value_tokens.py`). The four existing field-level validators
  (`dns`/`module`/`network`/`firewall`) are untouched; this only adds
  detection where none existed before. 17 new tests, full check suite
  green (1152 tests).
- **Real impact on `.v2-haven`, deliberately not yet fixed**: running
  `strata validate` after this change surfaces **44 new errors** across
  **17** real module files — not the 5-6 originally cited above, a much
  larger real footprint than first thought. Every one is a bare
  `${VARNAME}` (no `kind:` prefix) in a `configuration`/`custom`
  passthrough field, previously invisible to any check. Only
  `modules/gatus.yaml` has an explicit comment justifying this as
  intentional (Gatus's own chart-native substitution — the fix there is
  the new `$${...}` escape); every other file (`immich`, `nextcloud-*`,
  `homarr-*`, `grimoire-secrets`, `pgadmin`, `firefly-*`, `foundryvtt-secrets`,
  `filebrowser`, `huybrechts-web*`) looks like a real secret that should
  become `${secret:KEY}`, per this gap's own already-stated option (a).
  **Per explicit request, left red rather than mass-edited in the same
  pass** — fixing all 17 files is its own dedicated pass, not bundled into
  the code change that revealed the need for it.
- **Module `configuration`/`custom` resolution closed 2026-09-28, for
  free** — `HelmIntegration.resolve_module_values()`/`ComposeIntegration.
  resolve_compose_values()` (gap #9 Phases 4/5) both walk their *entire*
  rendered payload generically (dict/list/str recursion, no field-specific
  scoping), and `_render_values()`/the Compose merge already fold
  `module.spec.configuration`/`service.configuration` into that payload
  before either resolver runs — so this gap's own catalyst
  (`modules/immich.yaml`'s `configuration.controllers.main.containers.
  main.env.DB_PASSWORD`) resolves today with zero new code, confirmed by
  `test_resolve_module_values_matches_the_real_immich_style_configuration_path`
  already passing.
- **A materially bigger, previously undiscovered Terraform-side gap found
  while designing Phase 6 (2026-09-28)** — checked every real
  `configuration`/`custom`-shaped field against what `terraform_projection.py`
  actually projects into a `*.auto.tfvars.json` file at build time:
  `resources_by_category` (`ResourceSpecModel.configuration`/
  `WorkspaceResourceModel.configuration`), `topologies`
  (`TopologyVolumeModel.configuration`), `properties`, `custom`, and
  `tenant` (`TenantSpecModel.configuration`) are all projected but excluded
  from gap #9 Phase 2's `TF_VAR_` deploy-time delivery — the exact shape
  dns/networks/firewalls had *before* Phase 2 fixed them. Zero real
  `.v2-haven` document uses a token in any of the five today — a
  consistency gap, not a currently-broken real document, same honesty
  Phase 2's own original discovery already modeled. Confirmed
  `FirewallSpecModel.configuration` is a separate, out-of-scope gap (never
  projected into any payload at all — a projection-completeness question,
  not a token-resolution one). Full design (decision: broadcast-only,
  `${output:}` rejected outright, one new `build_configuration_payloads()`
  function) lives in
  [docs/design/value-token-resolution.md](value-token-resolution.md)'s
  Phase 6 — **implemented 2026-09-28**, same day as the design: new
  `build_configuration_payloads()` (`terraform_projection.py`) delivers
  all five as `TF_VAR_resx_<type>`/`TF_VAR_topologies`/`TF_VAR_properties`/
  `TF_VAR_custom`/`TF_VAR_tenant`, `deploy_controller.py` pre-flight-rejects
  any `${output:...}` found in them. 8 new tests in
  `test_integrations_terraform_projection.py`, 2 new end-to-end tests in
  `test_deploy_controller.py`; full check suite green (mypy 107 files,
  ruff clean, import-linter 1/0, pytest 1212 passed).

### 9. ~~The deploy-time Value-token resolver is designed for every kind, but only implemented for one field~~ — RESOLVED

- **Found in:** [docs/design/value-token-resolution.md](value-token-resolution.md)
  itself, cross-checked against `deploy_controller.py` and
  [docs/work/deploy-command.md](deploy-command.md) — not found via
  a haven document, but the underlying reason gaps #1 and #8 can't actually
  be closed by a schema-correct rewrite alone.
- **Status:** implemented (2026-09-28) — every kind now resolves at
  deploy time for its intended destination. The design decided (2026-09-25, that doc's
  own "Decision" section) to build "one resolver for every kind, at deploy
  time" — covering `DnsRecordModel.value`, `SubnetModel.cidr`,
  `FirewallRuleModel.from_`/`.to`, and `ModuleServiceEnvironmentModel.value`.
  `strata deploy run` (ADR-0027) shipped since that decision was written,
  but it only wired up **one** call to `resolve_value_tokens_in_mapping()`:
  `provisioner.backend.configuration` (Terraform). Nothing calls it for
  DNS/network/firewall documents or for a module's `services[].environment[]`
  before Compose/Helm actually runs — `deploy-command.md`'s own design
  confirms the only concrete secret-injection mechanism built is Terraform's
  `TF_VAR_<key>` env-var injection.
- **The "never written to disk" mechanism is already specified per
  integration, just not wired up** (confirmed directly in each
  integration's own docstring, not a new design question). There is
  **no** `STRATA_<KEY>` (or any other) env-var secret-injection convention
  today, for any integration — the existing `STRATA_*` env vars
  (`STRATA_OUTPUT`, `STRATA_WORK_PATH`, `ModuleFileModel`'s build-time
  file-templating substitution) are unrelated to this. And a uniform
  env-var scheme across all three integrations isn't possible regardless
  of naming — it's a genuine tool limitation, not a strata choice:
  - **Terraform** and **Compose** both support reading values from the
    process environment natively (`TF_VAR_<name>`; Compose's own `${KEY}`
    interpolation at parse time) — no disk write needed for either.
  - **Helm** has **no environment-variable substitution mechanism at all**
    for `values.yaml`/chart templates — its only inputs are `-f`, `--set`,
    `--set-string`, `--set-json`, `--set-file`, or the chart's own
    defaults; nothing reads `os.environ`. That is why the mechanism below
    is a CLI argument, not an env var — it's the only "don't touch the
    file" option Helm's own CLI offers.
  - **Helm** ([helm.py](../../src/strata/integrations/helm.py) `prepare_namespace()`):
    secrets are meant to go in via `helm upgrade --set-string KEY=<value>`
    — never rewritten into the `values.yaml` `build run` already wrote.
    Caveat: a CLI arg is visible via `ps`/`/proc/<pid>/cmdline` to any
    local process with sufficient privilege — a real, if smaller, exposure
    than an env var. This is Helm's own best available mechanism, not a
    strata design compromise.
  - **Compose** ([compose.py](../../src/strata/integrations/compose.py)
    `prepare_namespace()`): the rendered file is meant to use a bare
    `${KEY}` (Compose's own native interpolation, not strata's typed
    token), satisfied straight from the **process environment** at
    `docker compose up` time — no `.env` file needed. `ComposeIntegration.
    deploy()`/`.plan()` already accept an `env: Mapping[str, str]` kwarg,
    merged into the subprocess environment exactly like Terraform's
    `TF_VAR_` mechanism — the plumbing to do this with zero disk exposure
    already exists, it's just never called with resolved secrets.
- **Consequence:** a schema-correct `${secret:KEY}` in
  `ModuleServiceEnvironmentModel.value` renders into `build run`'s Helm
  values/Compose file as the literal unresolved string — `deploy run` never
  substitutes it before invoking `helm upgrade`/`docker compose up`. Not a
  haven-migration mistake; every real solution hits this today for any
  module secret.
- **Design completed 2026-09-28** (see
  [docs/design/value-token-resolution.md](value-token-resolution.md)'s
  "unify gaps #8/#9/#10" section): concrete per-integration delivery
  (Terraform `TF_VAR_<name>=<json>` for the whole `dns`/`networks`/
  `firewalls` payload; Helm `values.yaml` rewrite + `--set-string` for
  secrets; Compose file rewrite + `env:` kwarg for secrets) and a 6-phase
  implementation plan.
- **Terraform delivery implemented 2026-09-28** (design doc's "Full
  Solution" Phase 2): `deploy_controller.py` now resolves `dns`/`networks`/
  `firewalls` payloads per step (via new `build_dns_networks_firewalls_payloads()`,
  `terraform_projection.py`) and delivers the whole resolved payload as
  `TF_VAR_dns`/`TF_VAR_networks`/`TF_VAR_firewalls`, mirroring
  `backend.configuration`'s existing pattern exactly — same `tokens` dict,
  same never-touches-disk guarantee (the on-disk `*.auto.tfvars.json`
  stays literal).
- **Path-tracking primitive implemented 2026-09-28** (Phase 3): new
  `resolve_value_tokens_tracking_secrets()` (`value_tokens.py`) — the
  piece Helm/Compose delivery (Phases 4-5) need to know *which dotted
  path* is secret-shaped and its resolved value, without resolving it in
  place.
- **Helm resolution primitive implemented 2026-09-28** (Phase 4): new
  `resolve_module_values()` (`helm.py`) applies Phase 3's path-tracking to
  one module's rendered `values.yaml` payload — pure, disk-free, mirroring
  `_render_values()`'s own testable convention. **Now wired into
  `deploy_run()`** via gap #13's `HelmIntegration.deploy_namespace()` — a
  module's `services[].environment[]` secret reaches a deployed container
  via `--set-string`, never written to disk. **Compose closed the same day**
  (Full Solution Phase 5): `ComposeIntegration.deploy_namespace()` rewrites
  the namespace's merged `docker-compose.yml`, renaming each
  `${secret:KEY}` token to Compose's own bare `${KEY}` interpolation syntax
  and delivering the real value only via the `docker stack deploy`
  subprocess's own environment — never written to disk either.
- **Migration action:** none — Terraform/DNS/network/firewall delivery,
  Helm module secrets, and Compose module secrets are all wired end to end
  now that gap #13 covers both container tools.

### 10. ~~`strata validate` cannot detect a Value token that will never be substituted~~ — RESOLVED


- **Found in:** investigating "how would a user even know where `${var:}`/
  `${secret:}`/`${feature:}` is safe to use" (a direct follow-up question,
  not a haven document) — traced the actual Phase 2 check,
  `unresolved_value_tokens()` in `semantic_checks.py`.
- **Status:** closed (2026-09-28, Full Solution Phase 7). The check calls
  `model.model_dump()` and recursively scans **every string in the whole
  document** (`_iter_strings()`) — it does not restrict itself to the
  "known" token-aware fields (gap #8), so it genuinely does catch a
  malformed/undeclared key anywhere, passthrough dict included. But this
  produced two silent failure modes, not one:
  1. **A bare `${VARNAME}` (no `var:`/`secret:`/`feature:` prefix)** — what
     all four gap #8 modules actually wrote — doesn't match
     `VALUE_TOKEN_PATTERN` at all (`value_tokens.py` requires the prefix),
     so `extract_value_tokens()` returns nothing for it. **Zero validation
     signal, anywhere, ever** — `strata validate` has no way to flag this as
     a likely mistake versus a deliberately literal string.
  2. **A correctly-formed `${secret:KEY}` placed inside `configuration:`/
     `custom:` (a token-unaware field, gap #8/#9)** — if `KEY` happens to be
     genuinely declared in the environment, validation **passes clean**: a
     false green light, since gap #9 confirms nothing ever substitutes a
     token sitting in a passthrough field regardless of whether the key
     exists. Only an *undeclared* key in this position gets caught (as
     `undeclared_value_token`) — coincidentally, not because the field
     itself was flagged as unresolvable.
- **Consequence:** there is currently no way for `strata validate` to tell an
  author "this token is syntactically fine and the key exists, but it will
  never actually resolve here" — the one signal that would have caught every
  gap #8 module at authoring time, before it ever reached a real deployment.
- **Design completed 2026-09-28** (see
  [docs/design/value-token-resolution.md](value-token-resolution.md)'s
  "unify gaps #8/#9/#10" section): once gap #9's resolver covers every
  string in a document instead of a fixed field allowlist, this gap's
  false-green-light failure mode disappears by construction — no code
  change needed here specifically if #9 lands completely. Only the
  separate bare-`${KEY}`-token (no `kind:` prefix) blind spot would remain,
  as its own smaller, lower-priority lint-hint item.
- **Phase 7 verification (2026-09-28) found the premise only partially
  held — a real, separate gap, not something Phases 0-6 ever touched:**
  `unresolved_value_tokens()`'s one real call site
  (`_check_deployment_value_tokens()`) only ever passed it DNS/network/
  firewall/module documents (`_documents_reachable_from_workspace()`'s own,
  narrower, pre-Phase-6 scope) — confirmed directly, not assumed. A token
  in `ResourceSpecModel.configuration`, `ProviderSpecModel.configuration`,
  `TenantSpecModel.configuration`, `WorkspaceSpecModel.configuration`/
  `.custom`/`.properties`, `TopologyVolumeModel.configuration`, or an
  `EnvironmentSpecModel`/`DeploymentSpecModel`'s own `properties`/`custom`
  was **never checked at all** by `strata validate` — not a "resolves at
  deploy time or not" question (gap #9/Phase 6's own concern, already
  closed), a "does validate even look at this field" one. Gap #9 Phase 6
  delivers exactly these fields at deploy time now, but nothing validated
  their tokens before that ever ran — the false-green-light was still real
  for all seven of these document kinds.
- **Implemented same day**: `_documents_reachable_from_workspace()`
  (`semantic_checks.py`) now also returns the workspace document itself
  (covering `WorkspaceResourceModel.configuration`, the workspace's own
  per-resource override, for free via the same whole-document walk) plus
  every Resource/Provider/Topology it references.
  `_check_deployment_value_tokens()` additionally checks the deployment
  document itself, its resolved Tenant (if any), and every reachable
  Environment document — a new shared `_reachable_environment_names()`
  helper (extracted from `_merged_declared_keys()`) avoids duplicating the
  tenant-then-deployment environment-merge-order logic. 7 new tests in
  `test_semantic_checks.py` (one per newly-covered document kind, plus one
  proving the topology document itself is now reachable); full check suite
  green (mypy 107 files, ruff clean, import-linter 1/0, pytest 1221
  passed); `.v2-haven` unaffected — confirmed directly (still the same
  known 44 errors: zero real document puts a token in any of these seven
  fields today).
- **Migration action:** none — this was a validation/tooling gap, not a
  migration one, and is now fully closed; the separate bare-`${KEY}`-token
  (no `kind:` prefix) blind spot remains open, as its own smaller,
  lower-priority lint-hint item (unchanged from the original finding).

### 11. ~~`${output:}` tokens crash `strata validate` with an unhandled `KeyError` instead of a clean error~~ — RESOLVED

- **Found in:** not a haven document — found while answering "does the
  gap #8/#9/#10 design cover `${output:}`/other token kinds too?"
  `VALUE_TOKEN_KINDS = ("var", "secret", "feature", "output")` — a real 4th
  kind already exists (no `${context:}` kind exists anywhere; "Context",
  ADR-0006, is the name of the backing store `${output:step.key}` reads
  from, not a token kind itself). `resolve_value_tokens_in_mapping()` (the
  gap #9 design's core primitive) is genuinely kind-agnostic and already
  resolves `${output:}` correctly today for `provisioner.backend.configuration`
  (`deploy_controller.py` merges `visible_outputs` into the same flat
  `tokens` dict). But **validation** was not: `EnvironmentService.
  declared_keys()` only returns `{"var", "secret", "feature"}` — no
  `"output"` entry — so `unresolved_value_tokens()`'s `declared[kind]`
  lookup would raise an unhandled `KeyError` the moment any DNS/network/
  firewall/module document contained a well-formed `${output:...}` token
  (Phase 1's shared `validate_value_tokens()` accepts all four kinds
  unconditionally, regardless of `DnsRecordModel`'s own docstring stating
  `output` isn't wired into that field yet).
- **Status: closed (2026-09-28).** Fixed `unresolved_value_tokens()`
  (`environment_service.py`) to check `kind not in declared` before the
  lookup and emit a clean `unsupported_value_token_kind` diagnostic
  ("`${output:...}` tokens are not supported in this field yet (pending
  Context, ADR-0006)") instead of crashing. Applies everywhere this
  function is used (`EnvironmentService.validate_document_tokens()` and
  `semantic_checks.py`'s deployment-wide `_check_deployment_value_tokens()`
  both call it), so both the single-environment and merged
  tenant+deployment paths are covered by one fix.
- **Verified:** new test `test_output_token_is_rejected_cleanly_not_a_crash`
  (`test_services_environment.py`) confirms a clean diagnostic, not a
  crash. Full check suite green (mypy 107 files, ruff clean, import-linter
  1 kept/0 broken, pytest 1138 passed — 1 new test).
- **Note for gap #9's eventual implementation — CORRECTED 2026-09-28, see
  gap #12:** this originally claimed `${output:}` "needs zero extra design
  work" for DNS/network/firewall once the resolver reaches them. That's
  true for `backend.configuration` (genuinely step-owned data) but wrong
  for DNS/network/firewall specifically — they are workspace-global,
  step-independent documents, and `${output:}` resolution is inherently
  per-step (dependency-scoped). Gap #12 has the full analysis; this note
  is kept, struck through in spirit, rather than deleted, so the original
  (incomplete) reasoning stays visible as a record of what was missed and
  why.

### 12. ~~`${output:...}` in a DNS/network/firewall document has no single correct resolution~~ — RESOLVED via step ownership

- **Found in:** not a haven document — found while implementing gap #9's
  Phase 2 (Terraform `dns`/`networks`/`firewalls` delivery,
  `deploy_controller.py`) and being asked directly: "could firewall or dns
  not have `${output:steps.infra.vm-ip-address}` that could be changed in
  a loop?" Traced the real code to confirm the intuition, not just
  reasoned abstractly.
- **Status:** open design question, currently side-stepped rather than
  solved. `provisioning_model.py`'s `ProvisioningStepModel.targets` names
  only Resource/Namespace references — there is no step-to-DNS/network/
  firewall binding anywhere in the schema; `workspace.spec.dns_zones`/
  `.networks`/`.firewalls` are workspace-global lists, the same three
  documents visible to and shared by every step. `deploy_controller.py`'s
  new `dns_networks_firewalls` payload (gap #9 Phase 2) is therefore built
  **once per run**, deliberately not per step — there is no per-step
  variant of "the workspace's DNS zones" to build.
- **The mismatch:** `${output:...}` resolution is inherently per-step
  (`visible_outputs` is dependency-scoped: a step may only see outputs
  from steps it `depends_on`, enforced in `deploy_controller.py`'s own
  `_upstream_step_names()`). A DNS record referencing
  `${output:apply-hearth.vm_ip}` has no owning step to scope that
  dependency check against. If resolved per step anyway (today's
  `dns_networks_firewalls` re-resolves using each step's own `tokens`,
  mirroring `backend.configuration`'s pattern): a step that depends on
  `apply-hearth` resolves fine; a step that doesn't raises an **uncaught**
  `ValueError` from `resolve_value_tokens_in_mapping()`, crashing
  `deploy_run()` outright; and two steps that both depend on it could in
  principle see different values if outputs change between them — there is
  no single right answer for a document that belongs to no step.
- **Currently side-stepped, not solved:** gap #11's fix means
  `unresolved_value_tokens()` already rejects **any** `${output:...}`
  token in a DNS/network/firewall document at `strata validate` time
  (`declared_keys()` never has an `"output"` entry) — and `strata deploy
  run`'s own command always calls `.require_valid()` before `deploy_run()`
  (`deploy_command.py`), so this scenario cannot reach the crash above via
  the normal CLI today. That block is coincidental, though — gap #11 was
  fixed to stop a *crash in the validator*, not to intentionally forbid
  `${output:}` in these fields forever. If that block is ever lifted (e.g.
  a future `declared_keys()` change that adds an `"output"` entry
  generally), this gap reopens for real.
- **Refined 2026-09-28, investigated before continuing to Phase 3 (per
  request):** the problem is narrower than first logged.
  `validate_provisioning_steps()` (`provisioning_model.py`) already
  enforces that two steps sharing a `target` must have a `depends_on` edge
  between them (ADR-0011: "undefined execution order on a shared target is
  a hard error, not a warning") — and `targets` is validated against
  `resources ∪ namespaces` only (`workspace_model.py`'s
  `validate_execution()`, `target_names = {r.name for r in resources} |
  set(namespaces)`).
  - **Modules are already safe, confirmed** — not a hypothetical for later
    phases: a module lives in a Namespace, and Namespaces are valid
    `targets`, so two steps ever sharing one are already forced into a
    deterministic order by the validator above. Phases 3-5 (Helm/Compose
    delivery, next up) do **not** inherit this gap — `${output:}` in a
    module's `services[].environment[].value` already has a well-defined
    owning step for free, no new design work needed there.
  - **DNS/network/firewall are the only real problem** — they are not
    `targets` at all, so no ordering guarantee exists for them, confirming
    this gap is specific to these three workspace-global categories, not a
    general "any non-step-owned document" problem.
  - **A concrete, low-risk fix exists if evidence ever justifies it**:
    extend `target_names` to also include `dns_zones`/`networks`/
    `firewalls` names, letting a step declare a DNS/network/firewall
    document in its own `targets` — the *existing* ambiguity-ordering rule
    would then apply unchanged, reusing a proven mechanism rather than
    inventing one. `targets` itself is already a plain `list[PlatformName]`
    (no `References()`-baked kind constraint), so this is a validation-rule
    extension, not a schema/type change.
- **The "zero real evidence" recommendation above was wrong — retracted
  2026-09-28.** Checked v1's real source (not the migration fixture)
  directly, per pushback that DNS/firewall/network genuinely need infra
  outputs (a VM IP, cluster data): `e:\SourcesXYZ\strata\src\strata\models\
  dns_model.py`'s `DnsRecordModel` has a real, working `output_key` field
  ("Key into a preceding deployment stage's provisioner outputs, e.g. a
  VM's public IP"), and `terraform_builder.py`'s `_build_dns_vars()` shows
  it in real use. v1's own mechanism there defers entirely to Terraform's
  HCL (bucket record coordinates separately, inject the value as a plain
  `TF_VAR_<output_key>`, let the module read `var.<output_key>` itself) —
  rejected for v2 per explicit direction: v2's resolution model
  (`${output:...}` substituted for real, in Python, exactly like
  `var`/`secret`) was intentional and is kept; only the missing
  *ownership* piece (direction 2 above) was needed.
- **Status: closed (2026-09-28).** Implemented direction 2:
  `workspace_model.py`'s `validate_execution()` now accepts
  `dns_zones`/`networks`/`firewalls` names in `target_names`, so a step's
  `targets` can claim a document (reusing `validate_provisioning_steps()`'s
  existing shared-target ordering rule unchanged — no new mechanism).
  `deploy_controller.py`: a document claimed by some step's `targets` is
  delivered (as `TF_VAR_<category>`) only to its owning step(s), resolved
  using that step's own `tokens` — `${output:...}` now sees exactly the
  right step's dependency-scoped outputs, matching the worked example
  (a `provision-hearth` step producing `vm_ip`, an `apply-dns` step
  targeting both `hearth` and `public-dns`, depending on `provision-hearth`,
  resolving the real IP). An unclaimed document still broadcasts
  `${var:}`/`${secret:}` to every step unchanged (Phase 2's original
  behaviour) — but if it contains `${output:...}` with no owning step, a
  single clear `Diagnostics` error surfaces before any step runs, instead
  of gap #12's original per-step crash/inconsistency.
- **Verified:** 2 new tests (`test_deploy_run_resolves_output_token_in_dns_via_owning_step_targets`,
  `test_deploy_run_rejects_unclaimed_output_token_in_dns`); full check
  suite green (mypy 107 files, ruff clean, import-linter 1/0, pytest 1158
  passed); `.v2-haven` unaffected (same known 44 errors).
- **Migration action:** none — not hit by any real haven document today. To
  use `${output:}` in a DNS/network/firewall document, add its name to the
  owning step's `targets` (see the worked example in
  `docs/design/value-token-resolution.md`).

### 13. ~~Helm/Compose deploy invocations are not wired into `deploy_run()`'s step loop at all~~ — RESOLVED

- **Found in:** not a haven document — found while implementing gap #9's
  Phase 4 (Helm value-token delivery), checking whether the delivery
  mechanism it would wire secrets into (a real `helm upgrade` call per
  step) actually exists yet.
- **Status:** fully implemented, both container tools (2026-09-28). This
  entry's own original text below ("Helm only — Compose still raises
  `IntegrationError`") described the state on the day this gap was
  *found*, before Compose's own `deploy_namespace()` was implemented the
  same week (Full Solution Phase 5) — left as-is below as the historical
  record of what was actually broken; see gap #9's own final status and
  `docs/work/deploy-command.md`'s "Helm/Compose orchestration" section
  for the finished design. At the time this was found: `ProvisionerModel`
  (`provisioning_model.py`) has no
  release/chart/namespace field of any kind — `HelmIntegration.plan()`/
  `.deploy()` both require `release`/`namespace`/`chart` (`_require()`
  raises `IntegrationError` if any is missing), but `deploy_run()`'s
  per-step loop (`deploy_controller.py`) only ever calls
  `integration.plan(path, out_file=..., env=env)` — no
  `release=`/`namespace=`/`chart=` passed, for any tool. Confirmed no test
  exercises a `tool: helm`/`tool: compose` provisioning step through
  `deploy_run()` at all (`test_deploy_controller.py`'s fixtures are all
  `tool: terraform`) — only `HelmIntegration`'s own unit tests call
  `.plan()`/`.deploy()` directly, with explicit `release`/`namespace`/
  `chart` kwargs a human test author supplied, never derived from a real
  step.
- **Structural mismatch, not just a missing field:** Terraform's shape
  (one root module per step, `path` = one directory, one `plan`/`apply`
  pair) doesn't fit Helm at all — a single namespace-targeting step can
  have **many** modules (`workload_controller.build_workload_modules()`
  already handles N modules per namespace at build time), each needing
  its **own** `helm upgrade` invocation (own release, own chart, own
  `values.yaml`). Compose is a closer fit (one `docker-compose.yml` per
  namespace, matching one step per namespace already), but still needs
  `namespace` (the Swarm stack name) derived from somewhere — no field
  supplies it today either.
- **Consequence:** gap #9's Helm/Compose secret delivery (Phases 4-5)
  cannot be wired end-to-end into `deploy_run()` until this is designed
  and built — Phase 4 was scoped down to the pure, disk-free resolution
  primitive (`resolve_module_values()`, `helm.py`) that a future
  orchestrator will consume, rather than blocking on this larger,
  separate gap.
- **Design completed and corrected twice 2026-09-28, implemented same day**
  (see [docs/work/deploy-command.md](deploy-command.md)'s
  "Helm/Compose orchestration" section for the full design history), per
  direct request to design this the way v1 avoided tool-type branching,
  not around it, then per direct pushback that got the mechanism right.
  Final design: `deploy_run()`'s per-step loop keeps its **one** existing
  `resolve_integration(index, provisioner)` call (unchanged from today)
  and branches on `integration.CAPABILITIES` (`"infrastructure"` vs
  `"container"`) — the real "what kind of stage is this" signal, matching
  v1's own `DeployerFactory.resolve_type()` (every stage explicitly names
  a provisioner, whose type picks the deployer — v1 never infers this
  from namespace-linkage). A step's `targets` then only decides *which
  namespaces* a container-capability step concerns; `integration.TYPE`
  filters same-typed modules within them, mirroring
  `HelmDeployer.validate_workspace()`'s own `if module.spec.type != HELM:
  continue` filter. New `InfraIntegration.deploy_namespace()` (mirrors
  `prepare_namespace()`'s own not-abstract/raise-by-default shape, base
  raises `IntegrationError`) consumes gap #9 Phase 4's
  `resolve_module_values()` for Helm, delivering secret-shaped leaves via
  `--set-string` (new kwarg on `HelmIntegration.plan()`/`.deploy()`,
  escaped per v1's real `_escape_set_value()`) and rewriting the module's
  `values.yaml` in place for non-secret leaves. `step.provisioner` is
  always meaningful under this design — an earlier draft that dispatched
  purely on `step.targets` and abandoned `step.provisioner` was corrected
  before implementation.
- **Chart reference resolution** (`_resolve_chart()`, `helm.py`): a
  registry-backed module's `meta.yaml` (`chartName`/`chartVersion`/
  `chartRemote`) is resolved against the solution's declared
  `spec.remotes` (same `{name: SolutionRemoteModel}` lookup
  `build_workload_modules()` already builds at build time, rebuilt
  identically in `deploy_controller.py`). `RemoteType.OCI` needs no `helm
  repo add` (`oci://` refs resolve natively); `RemoteType.HELM` gets a
  `helm repo add <alias> <url>` first, alias derived by
  `_sanitize_repo_name()` (ported verbatim from v1). A git-based/local
  chart `source` (no `chartName` in `meta.yaml`) skips chart-remote
  resolution entirely — the module's own already-materialised
  `source_path` **is** the chart. A new deploy-time module-resolution
  helper, `resolve_namespace_modules()` (`workload_controller.py`),
  mirrors `build_workload_modules()`'s per-reference loop without
  re-materialising any source (deploy time only deploys what `build run`
  already rendered).
- **Verified:** 40 tests in `test_integrations_helm.py` (chart resolution,
  `--set-string` escaping/argv, `deploy_namespace()` for local and
  registry charts, missing-meta.yaml/unresolvable-remote diagnostics,
  dry-run never touching disk), 3 new tests in `test_deploy_controller.py`
  (container-capable step dispatch end-to-end alongside an unchanged
  Terraform step, scope filtering, no-matching-namespace-target error);
  full check suite green (mypy 107 files, ruff clean, import-linter 1/0,
  pytest 1185 passed); `.v2-haven` unaffected (same known 44 errors).
- **Compose implemented the same day** (Full Solution Phase 5):
  `ComposeIntegration.deploy_namespace()` (`compose.py`) — unlike Helm (one
  release per module), Compose already merged every module into **one**
  `docker-compose.yml` at build time, so this is one `docker stack deploy`
  per namespace, not per module. Resolves the merged document via a new
  `resolve_compose_values()` (thin wrapper over `value_tokens.py`'s new
  `resolve_value_tokens_renaming_secrets()`): non-secret tokens resolve to
  their literal value in place, a `${secret:KEY}` token is renamed to
  Compose's own bare `${KEY}` interpolation syntax instead — delivered only
  via the `docker stack deploy` subprocess's own environment (`env=`,
  merged by `Integration.run()`), never written to disk. 6 new tests in
  `test_integrations_compose.py`, 11 new tests in
  `test_utils_value_tokens.py`, 1 new test in `test_deploy_controller.py`
  (compose step dispatch end-to-end, mirroring the Helm one — proves the
  orchestrator's container-capability branch is genuinely tool-agnostic,
  not accidentally Helm-specific); full check suite green (mypy 107 files,
  ruff clean, import-linter 1/0, pytest 1203 passed).
- **Migration action:** none — Helm and Compose namespace deployment are
  both wired end-to-end now.

### 14. ~~`TenantService`'s own docstring says `spec.environments` cross-checking is deferred — it already isn't~~ — RESOLVED

- **Found in:** not a haven/config-deploy document itself — found
  while migrating `.v2-cfg`'s tenant (`customers/c0062/tenant.yaml`,
  2026-09-29), reading `TenantService`'s docstring to check whether
  `Tenant.spec.environments` needed a manual existence check the same way
  `WorkspaceService.validate_topology_references()` supplies one for
  Topology.
- **Status: closed (2026-09-29).** `tenant_service.py`'s class docstring
  said: *"`spec.environments` cross-checking is still deferred: it names
  Environment documents and the `environment` kind is not built yet (it is
  the most authored kind missing from v2). Becomes an index lookup once it
  lands."* This was true when written, but the `environment` kind has
  since been built (gap #9's whole arc depends on it) and
  `TenantSpecModel.environments` is already typed
  `Annotated[PlatformName, References(PlatformKind.ENVIRONMENT)]`
  (`tenant_model.py`) — which `references.py`'s generic walker already
  checks for every document, unconditionally, with no per-kind opt-in
  needed. **Confirmed empirically**, not just by reading the type
  annotation: pointed `.v2-cfg`'s tenant at a nonexistent environment name
  and re-ran `strata validate` — it correctly failed with `spec.environments.0:
  unknown environment 'ghost-env-that-does-not-exist' [unknown_reference]`,
  proving the check already runs today, unconditionally, via the generic
  reference-existence pass (`validate_references()`), not via any
  `TenantService`-specific method.
- **Fixed:** `tenant_service.py`'s class docstring rewritten to state the
  check is already covered generically (no per-kind opt-in needed), and to
  drop the now-inaccurate "most authored kind missing from v2"
  characterization of `environment`, which is one of the most-built-out
  kinds in v2 today — gaps #8/#9/#10/#12/#13 all depend on it. No behavior
  change — the check itself was already correct and already running; only
  the docstring was wrong.
- **Migration action:** none.

### 15. Terraform input validation against `variables.tf` has no v2 equivalent

- **Found in:** not a fresh discovery — `docs/work/build-command.md`'s
  own "Remaining Work / Open Questions" section already tracks this
  ("Terraform input validation against `variables.tf` — v1 fails the build
  on a declared-input/schema mismatch before `apply` would.
  [provisioning-injection-model.md](provisioning-injection-model.md)
  mentions parsing `variables.tf` as a capability lookup, but not as a
  build-time gate."). Cross-referenced into this file's numbered gap list
  for the first time while migrating `.v2-cfg`'s spoke stack (2026-09-29):
  the real `stacks/spoke/environment.yaml`'s own comment names the exact
  mechanism v1 has and v2 lacks — declaring that `tf_state_*` variables are
  "NOT stack-root inputs... strata excludes keys referenced by
  `backend.configuration` from the variables.tf check, so no Terraform root
  should declare them." That exclusion rule only makes sense as one small
  carve-out *of* a real, broader v1 feature: every other declared
  `variables`/`features` key in a v1 environment document IS cross-checked
  against the target Terraform root's real `variables.tf` at build time,
  and `strata build run` fails before `apply` would if a key is undeclared
  there or type-mismatched.
- **Status:** open — deliberate scope limitation so far
  (`build-command.md`'s own framing: "mentions parsing `variables.tf` as a
  capability lookup, but not as a build-time gate"), not yet designed or
  scheduled. No v2 code path reads a Terraform root's `variables.tf` file
  at all today — `.v2-cfg`'s `workspaces/spoke.yaml`/`environments/
  spoke-env.yaml` declare `keyvaults`/`ring_subnet_cidrs`/`aks_config`/etc.
  with no cross-check against the real `spoke/terraform` root's declared
  inputs (out of reach anyway in this fixture, since `iac-int`'s real
  Terraform content isn't checked out here — but the check would be a
  no-op for every v2 solution today regardless of whether the root is
  materialized).
- **Consequence:** a typo'd or renamed variable/feature key in a v2
  Environment document is silently accepted at `strata validate`/
  `build run` time and only surfaces later, as Terraform's own "undeclared
  variable" warning (or worse, `terraform plan` silently ignoring an
  extra `*.auto.tfvars.json` key) — exactly the failure mode
  `stacks/core/environment.yaml`'s own real v1 comment warns about for its
  `properties` block ("unlike `variables`/`features`, `properties` keys
  are NOT cross-checked against `variables.tf` ... a typo here is silently
  emitted and only surfaces as a Terraform... warning").
- **Migration action:** none available yet — no design exists. Would need,
  at minimum: locating the target Terraform root on disk (post-`sync_source()`,
  `docs/work/remotes.md`'s own still-open prerequisite), parsing
  `variables.tf` (HCL, not YAML — a new parsing dependency `provisioning-injection-model.md`
  already flagged as a capability-lookup-only concern today), and deciding
  where in `build run`'s flow the check runs and how strict it is (error
  vs. warning, and whether backend-referenced keys are excluded the same
  way v1's real exclusion rule works).
- **Related:** [docs/design/terraform-tfvars-parity.md](terraform-tfvars-parity.md)
  (2026-09-30, **fixed** the same day) is a distinct but adjacent gap —
  that doc audited (and then fixed) whether v2 even *produced* the right
  tfvars variable names/shapes a real Terraform root expects (found via a
  real, empirical v1-vs-v2 build-output comparison); this gap is about
  *validating* declared inputs against a root's real `variables.tf` once
  produced — still open. That doc's fix only closes the *shape*
  mismatch; a typo'd/undeclared key would still pass silently today,
  which is exactly this gap's remaining concern.

### 16. ~~`strata validate` rejected every `${output:...}` token unconditionally, even ones gap #12 already made work~~ — RESOLVED

- **Found in:** not a haven/config-deploy document — found while
  designing `docs/work/deploy-command.md`'s "Cross-invocation output
  access" section (2026-09-29): reviewing that design against real code
  before writing new checks on top of it, `unresolved_value_tokens()`
  (`environment_service.py`, the function `strata validate`'s Phase 2 pass
  actually calls) still carried a comment unchanged since gap #11 —
  "`${output:}` is not yet wired into any of the fields this function
  checks (DNS/network/firewall/module), pending Context (ADR-0006)" — no
  longer true since gap #12 wired real, tested, step-owned `${output:...}`
  resolution into DNS/network/firewall documents. **Confirmed empirically**:
  ran `unresolved_value_tokens()` directly against the identical DNS
  document gap #12's own passing test uses
  (`test_deploy_run_resolves_output_token_in_dns_via_owning_step_targets`),
  with the token legitimately claimed by a real step — it failed with
  `unsupported_value_token_kind`, even though `deploy_run()` resolves that
  exact document correctly. That test only ever calls `open_solution()`
  (Phase 1) before calling `deploy_run()` directly, never
  `context.resolve()` (the real `strata validate` Phase 2 path), so the
  inconsistency was never caught.
- **Expanded scope found on the same review**: the bug also affects
  Modules, not just DNS/network/firewall — `unresolved_value_tokens()` has
  no per-document-kind special-casing at all, so a Module's `services[].
  environment[].value` using `${output:X.key}` (already correctly resolved
  at deploy time via `HelmIntegration.deploy_namespace()`'s `tokens =
  {**resolved.values, **visible_outputs}`) was rejected by `strata
  validate` for the identical reason. Gap #12's own "modules are already
  safe" note is about deploy-time dependency *ordering*, not validate-time
  *acceptance* — it never touched `unresolved_value_tokens()`.
- **Status: closed (2026-09-29).** New `WorkspaceService.
  claimed_document_names()` generalizes gap #12's dns/network/firewall-only
  claiming rule to the full `target_names` vocabulary
  `workspace_model.py`'s own `validate_execution()` already validates
  against (`dns_zones ∪ networks ∪ firewalls ∪ namespaces`) — a Module
  reached through a targeted Namespace is exactly as claimed as a
  directly-targeted DNS document. `_documents_reachable_from_workspace()`
  (`semantic_checks.py`) now returns `(document, claimed)` pairs instead of
  a bare list, threading each document's claim status through (a module
  inherits the claim of the namespace it was reached through).
  `unresolved_value_tokens()` gained an `output_claimed: bool = False`
  parameter — `True` accepts an `${output:...}` token instead of rejecting
  it (key/step existence is still unvalidatable, matching ADR-0006's own
  admission that Context "does not solve output validation" — only the
  false rejection is fixed, nothing new is checked). Every existing caller
  (`validate_document_tokens()` included) keeps the old default and is
  unaffected. Deliberately **not** touched: `deploy_controller.py`'s own,
  separate, narrower `claimed_by_category`/`_contains_output_token()`
  (per-category, feeds `TF_VAR_` delivery) — the new
  `claimed_document_names()` is an independent, coarser computation
  purpose-built for this validate-time question; the two mechanisms
  coexist rather than being unified.
- **Verified:** 11 new tests — 5 in `test_services_workspace.py`
  (`claimed_document_names()` unit coverage: empty/no-model, a targeted
  DNS zone, a targeted namespace, an untargeted document, a targeted
  resource correctly excluded since it has no Value-token-bearing field of
  its own), 4 in `test_semantic_checks.py` (end-to-end via
  `open_solution(...).resolve()`: a claimed DNS document accepted, an
  unclaimed one still rejected, a Module reached through a claimed
  namespace accepted, one through an unclaimed namespace still rejected),
  2 in `test_services_environment.py` (`unresolved_value_tokens()` direct:
  `output_claimed=True` accepts, omitted/`False` still rejects — matching
  gap #11's original test). Full check suite green: mypy (107 files),
  ruff, import-linter (1 kept, 0 broken), pytest (1254 passed).
  `.v2-cfg`/`.v2-haven` re-validated: `.v2-cfg` still passes clean (12/12),
  `.v2-haven` still fails with exactly its same known 44 pre-existing,
  unrelated errors.
- **Migration action:** none — a document already claimed by an execution
  step's `targets` (DNS/network/firewall directly, or a Module via its
  namespace) now validates cleanly with an `${output:...}` token; an
  unclaimed one is still correctly rejected, unchanged from gap #12's
  original behavior.

### 17. ~~Provider's `configuration`/`custom` passed `strata validate` cleanly but was never projected into any Terraform artifact~~ — RESOLVED

- **Found in:** not a haven/config-deploy document originally — found
  while designing `docs/design/cross-document-value-references.md`'s new
  `${value:kind.name.path}` kind (2026-09-29), whose own "universal
  resolution reach" requirement was checked against every kind's real
  delivery path rather than assumed. **Confirmed live in a real document
  too**: `.v2-haven/providers/hetzner-eu-de.yaml` has both `spec.
  configuration` (`organization`/`version`) and `spec.custom` (`engine`)
  set — neither literal ever reached `providers.auto.tfvars.json` before
  this fix, confirmed by reading `_build_providers_payload()`
  (`terraform_projection.py`) directly: it only ever read `properties.
  type`/`.region`/`.display_name`, never `spec.configuration`/`.custom` at
  all, despite `_documents_reachable_from_workspace()`
  (`semantic_checks.py`) already walking the *whole* Provider document for
  `strata validate` — a `${var:}`/`${secret:}` token there passed
  validation cleanly, then was silently dropped rather than delivered
  unresolved or flagged.
- **Root cause:** `build_configuration_payloads()` (Phase 6, gap #8's
  Terraform-side refinement) delivered a curated 5-category subset
  (`resx_<type>`/`topologies`/`properties`/`custom`/`tenant`) at deploy
  time — a hand-picked list, not a generic "every category
  `build_platform_projection()` builds" walk. `providers` (and, less
  consequentially, `workspace`/`namespaces`/`flags`/`variables`, none of
  which realistically carry a token) was simply never on that list.
  Recorded as a formal decision in
  [value-token-resolution.md](value-token-resolution.md)'s
  "Decision (2026-09-29)" section: resolution reach must match validation
  reach — no curated allowlist, ever, for exactly this reason.
- **Status: closed (2026-09-29).** `_build_providers_payload()` now
  includes `configuration`/`custom` (empty dict when unset, matching every
  other category's own convention); `build_configuration_payloads()`
  extended from 5 to all 10 non-claimable categories (adds `workspace`/
  `providers`/`namespaces`/`flags`/`variables` — the last four are true
  no-ops in practice, included on principle so this function can never
  regress into a curated allowlist again). `dns`/`networks`/`firewalls`
  keep their existing, unchanged per-name claiming rule (gap #12) — this
  fix only touches the ten broadcast-only categories.
- **Verified:** 3 new tests (`test_integrations_terraform_projection.py`:
  provider configuration/custom projection; `test_deploy_controller.py`:
  end-to-end `TF_VAR_providers` delivery with a real `${var:}`/`${secret:}`
  token, on-disk artifact confirmed to stay literal/unresolved) plus 4
  existing tests updated for the wider category set (`workspace`/
  `providers` are unconditionally present in every fixture, since a
  workspace document always has a name and the fixture always seeds one
  provider). Full check suite green: mypy (107 files), ruff, import-linter
  (1 kept, 0 broken), pytest (1258 passed).
- **Migration action:** none required — a document with a token in
  `Provider.spec.configuration`/`.custom` (e.g. `.v2-haven`'s real
  `hetzner-eu-de.yaml`, though it uses only plain literals today) now has
  that value actually delivered to Terraform at deploy time, where before
  it was silently absent from every artifact.

### 18. ~~CycloneDX SBOM generation (`build run`'s `sbom` phase) has no v2 equivalent~~ — PHASE 1 RESOLVED

- **Found in:** not a haven/config-deploy document — found by reading v1's
  real source directly (`e:\SourcesXYZ\strata\src\strata\commands\builders\
  run_build_command.py`/`builders\sbom_builder.py`/`builders\sbom\*.py`,
  2026-10-01), after ADR-0022's original framing ("SBOM ... no confirmed
  `build run` consumer in either real repo") was challenged directly.
- **Status:** Phase 1 implemented (2026-10-01) — **corrects ADR-0022's
  cut, doesn't just confirm it.** SBOM generation is actually an
  *unconditional* phase of every real v1 `build run` (`platform →
  terraform → ansible → bicep → compose → helm → sync → sbom`, no flag
  gates `sbom`) — not the low-confidence, no-real-usage feature
  ADR-0022's phrasing implied. `build run` now writes a real,
  schema-validated `sbom.json` covering 4 of v1's 8 collectors
  (`image`/`compose`/`helm`/`terraform`) via a new, pluggable
  `strata.sbom_collectors` registry — see
  [docs/work/sbom-generation.md](sbom-generation.md). `ansible`/`deps`
  remain deferred; CVE scanning/policies/manifest embedding are separate,
  already-tracked concerns.
- **Migration action:** none — `strata build run` now produces `sbom.json`
  automatically, same as v1, for the 4 covered component types. A
  migrated solution regains this artifact with zero manifest changes.

### 19. No mechanism for multiple documents to each contribute a fragment of the same variable value

- **Found in:** direct real-world report (2026-10-02) — Azure Application
  Gateway + WAF, where the real hand-vendored Terraform module
  (`iac_aks_core`'s `agw.tf`) expresses every listener/path-rule/backend-pool
  as a `dynamic` block driven entirely by one input variable,
  `appgateway_config` (a deeply nested map, keyed by customer/domain).
  Confirmed directly in the real `cfg-int-deployment/deploy/hubs/z00/s01/
  environment.yaml`: that whole nested structure is today one `store:
  constant` variable's literal `value:`, in one file, with exactly one
  customer (`unisonplanning`) live as of 2026-09-30 ("WAF/AGW first real
  test"). Adding a second customer means editing that same shared value —
  "all customer paths on agw are in one block... not really a manageable
  solution" (direct quote).
- **Status:** **Resolved (2026-10-02), no new schema** — see
  [docs/design/composite-variable-merge.md](composite-variable-merge.md)'s
  Decision. `spec.properties`/`.custom` (existing `EnvironmentSpecModel`
  fields) already run through a real, generic, recursive `deep_merge()`
  (`strata/utils/dict_merge.py`, already proven at two other call sites —
  `merge_deployment_specs()`, `merge_workspace_environment_deployment_
  properties()`) and are already delivered to Terraform via
  `properties.auto.tfvars.json`. Experimentally confirmed end to end:
  three real `EnvironmentModel` documents, one per customer plus a second
  document adding a new ring to an existing customer, correctly combined
  with nothing dropped. A second real "nested-by-key" variable
  (`ring_subnet_cidrs`, same repo, same file, keyed by ring instead of
  customer) confirms this is a recurring shape, not AGW-specific. v1 had
  two narrower precedents, neither generalized nor ported to v2:
  `merge_networks`/`merge_firewalls` (document-level merge by name,
  Network/Firewall kinds only — ADR-0007 §5/ADR-0008 §4 already deferred
  porting this) and `EnvironmentIncludeModel`/`TerraformLoader.
  concatenate()` (raw `.tf` text merging — traced its `strategy: merge`
  option too; it wouldn't have solved the real AGW case either, duplicate
  resource declaration not a merge). ADR-0025 ("strata supplies input,
  never rewrites IaC source") is satisfied: `properties`'s value stays
  fully opaque to strata either way.
- **Migration action:** author the shared value (e.g. `appgateway_config`)
  under `spec.properties` instead of `spec.variables`/`store: constant`,
  split across one Environment document per owner (customer/ring/team) —
  see [docs/how-to/composite-variable-fragments.md](../how-to/composite-variable-fragments.md).
  No strata change required. If the composed value also needs to generate
  repeated static Terraform blocks (e.g. one `provider`/`module` pair per
  customer — Terraform's own `providers` meta-argument can't vary per
  `for_each`/`count` instance), see
  [docs/how-to/generate-per-instance-terraform-blocks.md](../how-to/generate-per-instance-terraform-blocks.md)
  for looping `output.template` over the same merged value.



Confirmed during the same migration to have zero loss of expressiveness:
namespace grouping, firewall rules (`from`/`to`/`port`/`proto`/`interface`),
module services/mounts/healthchecks/environment, secret `generate` specs,
and the provider/providerconfig/topologyconfig registry split.

## Related

- `.v2-cfg/` at the workspace root — a small hand-migrated slice of the
  real `config-deploy` repo (`e:\sources\config-deploy`): one
  tenant (`c0062`), one provider (`westeurope`), and the spoke stack
  (`stacks/spoke/*` — the reusable hub x spoke shared-AKS-cluster
  template), deliberately not a full migration. Chosen because the spoke
  stack is v1's real, most concrete example of gap #5's exact structural
  change (v1's `workspace.yaml` binds `provider`/`provisioner` directly
  onto a `topology[].components` entry). **Passed `strata validate` clean
  (12/12 documents, zero findings) as of 2026-09-29** — gap #14 (now
  resolved) and gap #15 (cross-linked from `docs/work/build-command.md`'s
  existing Remaining Work) were both found/logged while building it; no
  other new schema/feature gap surfaced. **Stale as of 2026-10-01
  (ADR-0028):** this fixture still uses the pre-ADR-0028 standalone
  `kind: topology` document for `spoke-cluster` — `WorkspaceModel` no
  longer parses that shape (`spec.topology[]` is now inline blocks, not
  name references), so `strata validate` against `.v2-cfg/` as-is will now
  fail until someone hand-migrates it inline. Deliberately left unfixed
  (Phase 5 of the ADR-0028 reversal, skipped — `.v2-cfg` is gitignored, not
  exercised by the automated test suite); see
  [docs/design/topology-standalone-kind-reconsideration.md](topology-standalone-kind-reconsideration.md).
- `.v2-haven/` at the workspace root — a full 52-document hand-migration of
  every real document in `e:\SourcesXYZ\haven\config`, kept as a live
  fixture. **Currently failing `strata validate` (44 errors, 17 module
  files) as of 2026-09-28** — an expected, deliberately-deferred
  consequence of gap #8's Phase 1 implementation (generalized
  malformed-token detection), not an unrelated regression. Fixing the
  fixture (escape `gatus.yaml`'s placeholders, convert the rest to
  `${secret:KEY}`) is its own dedicated follow-up pass. Until that lands,
  a *new* failure here needs to be diffed against this known 44 to confirm
  it's actually new, not just re-triggering the logged ones. Re-run
  validate after any model change touching provider/resource/firewall/
  module/namespace/workspace/topology/deployment/environment.
- [docs/decisions/0011-topology-and-provisioning-decoupling.md](../decisions/0011-topology-and-provisioning-decoupling.md)
- [docs/decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md)
- [docs/decisions/0015-solution-manifest-and-document-discovery.md](../decisions/0015-solution-manifest-and-document-discovery.md)
- [docs/work/deploy-command.md](deploy-command.md)
- [docs/design/value-token-resolution.md](value-token-resolution.md) — the
  Per-Kind Status table and Remaining Work section gaps #1/#8/#9/#10 are all
  grounded in
- `src/strata/controllers/semantic_checks.py`'s `unresolved_value_tokens()`
  (gap #10) and `src/strata/utils/value_tokens.py`'s `VALUE_TOKEN_PATTERN`/
  `extract_value_tokens()` (why a bare `${KEY}` is invisible to it)
- `src/strata/utils/value_tokens.py`'s `resolve_value_tokens_in_mapping()` and
  its one real call site in `src/strata/controllers/deploy_controller.py`
  (gaps #8, #9)

## Changelog

- 2026-09-28: Created, seeded with 7 gaps found migrating the full haven
  repo (`.v2-haven/`, 52 documents, `strata validate` clean).
- 2026-09-28: Added gap #8 — value tokens inside `spec.configuration`/
  `spec.custom` passthrough dicts are never resolved (confirmed in source,
  not just a schema-validation gap like #1's passing mention); found via
  `modules/immich.yaml`, `nextcloud-secrets.yaml`, `homarr-secrets.yaml`,
  `grimoire-secrets.yaml`, `pgadmin.yaml` in `.v2-haven/`.
- 2026-09-28: Corrected gap #1 — it previously implied a `${secret:KEY}`
  token inside a raw `configuration:`/`custom:` dict was an equally valid
  alternative to `ModuleServiceEnvironmentModel.value`, which gap #8 shows
  is false (never resolved either way). Now cross-references #8 instead of
  repeating the outdated claim.
- 2026-09-28: Investigated whether gap #1 is by-design or an oversight, per
  request. Read `docs/design/value-token-resolution.md` directly: the field
  choice (`ModuleServiceEnvironmentModel.value`, not `configuration`) is
  confirmed deliberate (ADR-0002 + that doc's Per-Kind Status table). But
  found a deeper, more consequential gap while checking whether the
  "correct" rewrite actually works: it doesn't, because the general
  deploy-time Value-token resolver that doc calls for was never built for
  module/dns/network/firewall fields — only `provisioner.backend.
  configuration` (Terraform) got a real implementation in `deploy run`
  (ADR-0027). Added as gap #9; gap #1 updated to point at it.
- 2026-09-28: Refined gap #9 with the concrete, already-designed
  no-disk-write mechanism per integration (Helm `--set-string`, Compose
  native `${KEY}` env-var interpolation via the `env:` kwarg already
  plumbed) — neither is implemented, but neither needs new design either.
  Confirmed no `STRATA_<KEY>` (or any) env-var convention exists for
  secrets today, and that Helm has no environment-variable input mechanism
  at all — a real tool limitation, not a strata choice.
- 2026-09-28: Added gap #10 — `strata validate` has no way to detect a
  Value token that will never be substituted. Traced
  `unresolved_value_tokens()` directly: it scans every string in a
  document via `model_dump()`, so it isn't restricted to token-aware
  fields, but that produces two silent failure modes — a bare `${KEY}`
  (no kind prefix) is invisible to `VALUE_TOKEN_PATTERN` entirely, and a
  correctly-formed `${secret:KEY}` in a passthrough field only gets
  caught if the key is *undeclared* (a false green light otherwise, since
  gap #9 means it never resolves regardless). Explains why gap #8's
  modules had zero validate-time signal of their mistake.
- 2026-09-28: Refined gap #2, per request ("declaring 6 chart registries as
  remotes feels like overkill"). Confirmed the pattern is deliberately not
  Terraform/git-specific (`RemoteType.HELM` is first-class, and
  `SolutionRemoteModel`'s docstring cites Helm's own `repositories.yaml`/
  `@alias` convention as a precedent) — but confirmed via real usage that
  all 6 of haven's chart remotes have a strict 1:1 mapping to their
  consuming module, so the design's actual payoff (preventing multiple
  consumers from silently diverging) never materializes here. Not a
  modeling mistake, just friction real for the common single-consumer case.
- 2026-09-28: Reopened gap #2 as a design question, per request. Logged two
  candidate directions: (1) let a module's `source` declare a chart
  repository inline as an alternative to `remote:`, mutually exclusive like
  `ModuleServiceModel.image`/`.artifact`; (2) route chart registry location
  through the `artifact` kind. Flagged that direction (2) needs to reconcile
  with `artifact_model.py`'s own existing rejection of a "chart mode" —
  though that rejection was about version pinning (already served by
  `kind: version`), not registry location, so it doesn't automatically rule
  this out. Neither direction designed yet; the mechanical migration action
  stays the only path until one is.
- 2026-09-28: Cross-referenced gap #3 to #2, per request ("does #3 depend on
  #2's resolution?"). Not automatically: direction 2 of #2 (artifact-routed
  charts) would likely close #3 as a side effect, but direction 1 (inline
  `chart_repository` as an alternative, not a replacement) only reduces how
  often #3 is hit, since `remote:` stays required for shared/credentialed
  registries. Also noted #3 is broader than charts — any non-chart OCI
  artifact source with no natural single ref hits the same friction
  independent of #2's outcome.
- 2026-09-28: **Resolved gap #4.** Made `default_tags`/`default_labels`
  optional on `Resource`/`Firewall`/`Module`/`Namespace` (as gap #4 named)
  and, per a broadened follow-up request, also on `Network`/`DNS` (same
  required-field pattern, just not hit by haven specifically since it has
  no network/dns documents) — checked every kind with a tags/labels field
  first; `custom_tags`/`custom_labels` and `Provider`/`Workspace`'s
  `default_tags` were already optional. Full check suite green (1137
  tests, one outdated test rewritten); `.v2-haven/` still validates clean;
  docs/config/*.md and Sphinx build updated and re-verified.
- 2026-09-28: Reconfirmed gaps #5 and #7 as accurate on review, per
  request — no changes needed to either.
- 2026-09-28: **Corrected gap #6 — it was stale, not open.** Per request
  ("this is moved to workspace right"), checked directly:
  `ProvisioningStepModel.scope` is a real, fully-wired v2 field
  (deliberately workspace-owned, not deployment-owned — already resolved
  in `deploy-command.md`'s own Remaining Work item 1, dated 2026-09-27),
  and `deploy run --scope` already filters on it with a passing unit test.
  The actual gap was in the `.v2-haven` fixture only (no `scope:` set on
  its workspace's execution steps) — fixed by adding `scope: infra`/
  `scope: apps` matching v1's real stage scoping; `strata validate` still
  clean. Also fixed a stale "Missing: `scope`" note in `deploy-command.md`
  itself that contradicted its own already-resolved Remaining Work item
  and its own pseudocode (which already showed `ProvisioningStepModel.scope`
  in use).- 2026-09-28: **Designed the unified resolver for gaps #8/#9/#10**, per
  request ("we are going to need to do design for the value resolvement").
  Key finding: `resolve_value_tokens_in_mapping()` is already field-agnostic
  (recurses into any nested dict/list), so gap #8 needs new call sites, not
  a new algorithm. Designed concrete per-integration delivery grounded in
  real code (`terraform_projection.py`, `helm.py`, `compose.py`,
  `deploy_controller.py`'s existing `backend.configuration` precedent):
  Terraform via `TF_VAR_<name>=<json>` for the whole `dns`/`networks`/
  `firewalls` payload; Helm via `values.yaml` rewrite (non-secrets) +
  `--set-string <path>=<value>` (secrets); Compose via file rewrite
  (non-secrets) + bare `${KEY}` rename + `env:` kwarg (secrets, reusing
  plumbing that already exists). Found this design resolves gap #10 as a
  side effect once implemented. Full design recorded in
  `docs/design/value-token-resolution.md`; all three gap entries updated
  to reference it. Implementation not started — this was design only.
- 2026-09-28: **Found and fixed gap #11** — a real crash bug, discovered
  while confirming the gap #8/#9/#10 design covers all token kinds
  (`${output:}` included, `${context:}` doesn't exist). `unresolved_value_tokens()`
  would raise an unhandled `KeyError` on any `${output:...}` token in a
  DNS/network/firewall/module document, since `declared_keys()` only
  covers `var`/`secret`/`feature`. Fixed to emit a clean
  `unsupported_value_token_kind` diagnostic instead. New test added; full
  check suite green (1138 tests).
- 2026-09-28: **Designed an escape/skip syntax for Value tokens** (`$${...}`,
  doubled leading `$`), triggered by asking whether Phase 1 syntax
  validation could just be generalized to every `spec.*` field instead of
  the current per-field allowlist. Found it can — but only safely with an
  escape hatch: `modules/gatus.yaml`'s own bare `${TOKEN}` (Gatus's
  chart-native substitution, not a strata token, per that file's own
  comment) would otherwise become a false-positive rejection under a
  generic Phase 1 check. Chose `$${...}` specifically because it's not a
  new convention — it doubles the same escape idiom Terraform (`$${`) and
  Compose (`$$`) already use for their own native interpolation syntax.
  Full design recorded in `docs/design/value-token-resolution.md`'s new
  "Escape syntax" section and cross-referenced from gap #8. Design only —
  nothing implemented, Phase 1 generalization itself also still undecided.
- 2026-09-28: Validated the Full Solution design against real source before
  implementing (per request), found and corrected two real problems — see
  `docs/design/value-token-resolution.md`'s Changelog for the full
  writeup. Then implemented Phase 0/1 (escape primitives + generalized
  malformed-syntax detection). Full check suite green (1152 tests). Ran
  `strata validate` against `.v2-haven` to observe real impact per
  request ("design, plan, and implement phase 1"): 44 new errors across 17
  module files, much larger than gap #8's originally-cited 5-6. Per
  explicit request, left the fixture red rather than mass-editing 17 real
  files in the same pass — logged as gap #8's own deferred follow-up above.
- 2026-09-28: **Implemented gap #9's Terraform delivery** (Phase 2), per
  request ("design, plan, and implement phase 2"). New
  `build_dns_networks_firewalls_payloads()` (`terraform_projection.py`)
  wired into `deploy_controller.py`: `dns`/`networks`/`firewalls` payloads
  now resolve per step via `TF_VAR_dns`/`TF_VAR_networks`/`TF_VAR_firewalls`,
  reusing the exact same per-step `tokens` dict `backend.configuration`
  already uses (dependency-scoped `${output:}` support falls out for
  free). Also made `resolve_value_tokens()` itself escape-aware first
  (Phase 0's one deferred piece — needed now that a real resolution call
  site exists): a combined regex now matches the escaped-literal shape
  before the real-token shape, fixing the exact bug the design validation
  pass had flagged but not yet fixed. Confirmed via test that the on-disk
  `dns.auto.tfvars.json` stays literal/unresolved. 4 new tests; full check
  suite green (1156 tests); `.v2-haven` unaffected (same known 44 errors —
  this phase is deploy-time only). Helm/Compose delivery (Phases 3-5)
  still open — module secrets still don't reach a deployed container.
- 2026-09-28: **Found gap #12**, per a direct question ("could firewall or
  dns not have `${output:steps.infra.vm-ip-address}` that could be changed
  in a loop?") asked right after Phase 2 shipped. Traced it to confirm,
  not just reasoned abstractly: `ProvisioningStepModel.targets` only names
  Resource/Namespace references — DNS/network/firewall are workspace-global
  (`workspace.spec.dns_zones`/`.networks`/`.firewalls`), owned by no step,
  while `${output:...}` resolution is inherently per-step
  (`_upstream_step_names()`'s dependency-scoping). A DNS record referencing
  an output token would resolve inconsistently across steps, or crash
  outright (an uncaught `ValueError`) for any step that doesn't depend on
  the referenced one. Confirmed this is coincidentally blocked today (gap
  #11's fix rejects `${output:}` in these fields at `strata validate` time,
  and `deploy run`'s command always validates first) but not by design —
  corrected gap #11's own closing note, which had claimed the opposite.
  Three candidate directions logged, none chosen; per request, investigating
  this before continuing to Phase 3.
- 2026-09-28: **Investigated gap #12 before continuing**, per request
  ("let's first look into this before continuing"). Found
  `validate_provisioning_steps()` already enforces ordering for two steps
  sharing a `target` (ADR-0011) — and confirmed `targets` covers
  `resources ∪ namespaces` only (`workspace_model.py`'s
  `validate_execution()`). This narrows the gap considerably: modules
  (namespace-owned) are already safe — Phases 3-5 do not inherit this
  problem — leaving DNS/network/firewall (not `targets` at all) as the
  only real gap. Recommended direction 1 (keep `${output:}` permanently
  unsupported there, no real evidence justifies more) as the default, with
  direction 2 (extend `target_names` to include DNS/network/firewall,
  reusing the existing ambiguity-ordering rule unchanged) documented as
  the concrete low-risk path if evidence ever appears. No code changed —
  analysis only. Cleared to continue to Phase 3.
- 2026-09-28: **Resolved gap #12**, per pushback that the "zero evidence"
  recommendation was wrong and a direct question ("we can get the full
  terraform output correct? we need some way to make them become
  available in the next steps"). Checked v1's real source (not the
  fixture): `DnsRecordModel.output_key` is real, working v1 code — v1
  defers entirely to Terraform's own HCL for resolution (bucket
  coordinates, inject `TF_VAR_<output_key>`, module reads `var.X` itself).
  Confirmed that's a different feature model than v2's embedded-token
  design and explicitly rejected switching to it — v2's Python-side
  substitution (already built, Phase 2) stays. Implemented the missing
  ownership piece instead (direction 2 from the investigation above):
  `workspace_model.py`'s `target_names` now accepts `dns_zones`/
  `networks`/`firewalls`, reusing `validate_provisioning_steps()`'s
  existing ordering rule; `deploy_controller.py` delivers a claimed
  document only to its owning step(s), resolved with that step's own
  outputs, and raises one clear diagnostic for an unclaimed `${output:}`
  document instead of the original crash/inconsistency. 2 new tests; full
  check suite green (1158 tests); `.v2-haven` unaffected (same known 44
  errors).
- 2026-09-28: **Implemented gap #9's Phase 3** (secret path-tracking
  primitive), per request ("design, plan phase 3" then "implement phase
  3"). New `resolve_value_tokens_tracking_secrets()` (`value_tokens.py`) —
  a sibling to `resolve_value_tokens_in_mapping()`, not a modification of
  it. A secret-shaped leaf's original literal is left untouched in the
  returned dict (Helm's `--set-string`/Compose's `env:` override it
  regardless); its dotted path and resolved value are reported separately.
  Verified against the real `immich.yaml` nested-path shape and a mixed
  `${var:}`+`${secret:}` connection string. 7 new tests; full check suite
  green (1165 tests). Helm/Compose delivery (Phases 4-5) still open — this
  only adds the primitive they'll consume.
- 2026-09-28: **Found gap #13, implemented gap #9's Phase 4 scoped down**,
  per request ("design, plan, and implement phase 4"). Before wiring
  secret delivery into a real `helm upgrade` call, checked whether that
  call already exists in `deploy_run()` — it doesn't:
  `ProvisionerModel` has no release/chart/namespace field at all, and no
  test exercises a `tool: helm`/`tool: compose` step through
  `deploy_run()`. Also a structural mismatch, not just a missing field —
  Helm needs one release **per module** in a namespace, not one per step
  (Terraform's shape). Logged as gap #13, separate from gap #9. Scoped
  Phase 4 to what's genuinely buildable now: new `resolve_module_values()`
  (`helm.py`) — pure, disk-free, mirrors `_render_values()`'s own testable
  convention, applies Phase 3's path-tracking to one module's rendered
  `values.yaml`. 4 new tests; full check suite green (1169 tests). Not
  wired into `deploy_run()` — blocked on gap #13.
- 2026-09-28: **Designed gap #13**, per direct request to look at how v1
  avoided `if tool == "helm"`/`if tool == "terraform"` branching and design
  v2's fix the same way, rather than around it. Read v1's real
  `deployers/` package directly: `DeployerFactory.create()` is pure
  lookup-table dispatch; `HelmDeployer` discovers every namespace+module
  combination for a whole stage up front, then every lifecycle step loops
  over that flat list — one `helm upgrade` per module, release/chart read
  back from `meta.yaml`, zero extra schema. Confirmed v2 already mirrors
  this exact pattern, proven, at build time:
  `workload_controller.build_workload_modules()` groups by `module.spec.type`
  and calls `integration.prepare_namespace(...)` polymorphically, zero
  type branching. Designed a `deploy_namespace()` deploy-time counterpart
  with the identical shape, consuming gap #9 Phase 4's
  `resolve_module_values()`. Full write-up in
  `docs/work/deploy-command.md`'s new "Helm/Compose orchestration"
  section. Design only — nothing implemented yet.
- 2026-09-28: **Corrected gap #13's design twice more.** First, validated
  against literal-name coupling (per request) and found the pseudocode
  resolved one integration from `step.provisioner` and wrongly reused it
  for every module regardless of type — fixed by grouping `by_type` and
  resolving per group, matching `build_workload_modules()`. Then, per
  direct pushback asking whether v1 really inferred Helm/Compose from
  namespace-linkage, re-checked v1's real `DeployerFactory.resolve_type()`
  directly: it doesn't — every stage explicitly names a provisioner (or a
  topology naming one), uniformly for every tool; namespace-linkage only
  decides which modules an already-known-to-be-Helm stage touches, one
  level further in. Corrected the design to branch on
  `integration.CAPABILITIES` (`"infrastructure"` vs `"container"`) instead
  — one integration resolution, shared by both branches, `step.provisioner`
  always meaningful (resolving the previous draft's own "unused field"
  consequence). Full history kept visible in `docs/work/deploy-command.md`'s
  new "Superseded" subsection rather than silently overwritten. Design
  only, still nothing implemented.
- 2026-09-28: **Resolved gap #2**, per request ("good. design, plan,
  implement"). Chose Direction 1 (inline `SourceModel.chart_repository`)
  over Direction 2 (route through `artifact`) — rejected the latter with
  concrete evidence: `ArtifactModel.registry`'s own docstring says it is
  inert ("strata never fetches an image"), while Helm's registry location
  is actively read by `HelmIntegration._resolve_chart()` to run
  `helm repo add`/build a chart ref, so reusing the same field for both
  would make it mean two different things. Implemented: `SourceModel.
  chart_repository` + mutual-exclusivity validator against `remote`
  (`common_models.py`); `HelmIntegration._render_meta()`/`_resolve_chart()`
  updated to write/resolve `chartRepository` alongside the existing
  `chartRemote` path, sharing a new `_chart_ref_from_url()` helper
  (`helm.py`). New tests in `test_models_common.py` (`SourceModel` mode/
  validator coverage) and `test_integrations_helm.py` (`_render_meta()`,
  `_resolve_chart()`, an end-to-end `deploy_namespace()` case with no
  `remotes=` passed); one pre-existing `test_models_module.py` test updated
  for the new error message. Full check suite green: mypy (107 files),
  ruff, import-linter (1 kept, 0 broken), pytest (1236 passed).
- 2026-09-29: **Designed gap #3**, per request ("do the full design in the
  _gap_v1 doc"). Split the rule the same way gap #2 split `SourceModel`'s
  own mode selection: what `SolutionRemoteModel.reference` *allows* stays a
  Phase 1 schema check (require it for `git` only now, still forbid it for
  `helm`/`local`, newly make it optional-not-forbidden for `oci`); what it
  actually *requires* for an OCI remote depends on how it's used elsewhere
  in the solution (chart-serving vs a real pinned image/artifact), which
  needs the loaded manifest, so that half becomes a new Phase 2 check,
  `SolutionRemoteService.validate_oci_reference_usage()` — flagged as
  genuinely new plumbing, not an extension of proven code, since no
  existing check validates remote-type-vs-use-site-mode at all yet
  (`common_models.py`'s own docstring already parks that as a separate,
  broader, still-unbuilt gap). Considered and rejected a simpler
  always-optional relaxation (drop `reference` for every OCI remote
  regardless of use) — rejected because it silently loses real pinning for
  the non-chart case, a genuine regression, not just lost convenience.
  Design only, recorded inline in gap #3 itself (not a separate design doc,
  per request); nothing implemented yet.
- 2026-09-29: **Resolved gap #3**, per request ("design, plan, and implement
  gap 3"). Implemented per the design: `SolutionRemoteModel.reference`
  (`solution_model.py`) now required only for `git`, merely optional (not
  forbidden) for `oci`; new Phase 2 check `semantic_checks._check_remotes()`
  (`run_semantic_checks()`'s 8th check, now also taking `solution:
  SolutionModel | None` — `solution_context.py`'s call site updated to pass
  it, same reason `check_version_pins()` already does) scans every
  `ModuleModel.spec.source` + `WorkspaceModel.spec.provisioners[].source`
  and raises `oci_remote_missing_reference` only when a real non-chart
  consumer names an OCI remote with no `reference`. Implemented as a
  standalone function rather than the design sketch's `SolutionRemoteService`
  method — no single natural "referencing kind" exists here, since Module
  and Workspace are peer consumers, not a service validating against
  another document (same shape `check_version_pins()` already uses). 7 new
  tests (2 model, 5 semantic-check integration tests covering chart-based/
  git-based Module and Workspace-provisioner consumers, a remote with
  `reference` already set, and the no-remotes-need-checking no-op). Full
  check suite green: mypy (107 files), ruff, import-linter (1 kept, 0
  broken), pytest (1243 passed). `strata validate .v2-haven` confirmed to
  add zero new findings — its pre-existing 44 `malformed_value_token`
  errors (unrelated) were confirmed via `git stash` to already exist on the
  unmodified branch.
- 2026-09-29: **Built `.v2-cfg`**, per request to start the same
  coverage-check migration exercise `.v2-haven` already does against a
  second, differently-shaped real repo (`e:\sources\config-deploy` —
  multi-tenant Azure platform, vs. haven's single-operator homelab).
  Scoped to one tenant (`c0062`), one provider (`westeurope`) and the spoke
  stack (`stacks/spoke/*`), per request, not a full migration. Chose the
  spoke stack specifically because its real `workspace.yaml` binds
  `provider`/`provisioner` directly onto a `topology[].components` entry —
  v1's most concrete real example of gap #5's exact structural change
  (ADR-0011), converted here to a pure-grouping Topology + explicit
  `execution:` step, mirroring `.v2-haven`'s own precedent. Caught one real
  discrepancy against the source material: the real `stacks/spoke/
  README.md` describes `enable_key_vault`/`enable_aks` as "flags it turns
  on", but the actual current `environment.yaml` has both forced `false`
  (with dated comments explaining why) — used the file's real values, not
  the README's description. **Result: `strata validate .v2-cfg` passes
  clean, 12/12 documents, zero findings, on the first run.** Found and
  logged gap #14 (a stale `TenantService` docstring) while migrating the
  tenant document — confirmed empirically (temporarily pointed
  `spec.environments` at a nonexistent name, `strata validate` correctly
  caught it, then reverted) that the docstring's claimed deferral is
  already false; no other new schema/feature gap surfaced by this pass.
  Separately (not a new finding, already documented in
  `docs/work/build-command.md`'s Remaining Work): the real spoke
  environment's own comment about `tf_state_*` variables being "excluded
  from strata's variable declaration check" is live evidence for that
  doc's already-tracked "Terraform input validation against `variables.tf`"
  gap — not yet cross-referenced into this file's numbered gap list, left
  as a candidate rather than added unasked.
- 2026-09-29: **Resolved gap #14, added gap #15**, per request ("fix gap
  14, add gap 15 but put is as documented for the remaining work in the
  build command"). Gap #14: rewrote `tenant_service.py`'s class docstring
  to state `spec.environments` existence is already covered generically
  (no per-kind opt-in needed) and dropped the stale "most authored kind
  missing from v2" characterization of `environment` — no behavior change,
  the check itself was already correct, only the docstring was wrong. Gap
  #15: cross-referenced `docs/work/build-command.md`'s existing
  Remaining Work item ("Terraform input validation against `variables.tf`")
  into this file's numbered gap list for the first time, per request —
  explicitly framed as already-documented, not a fresh discovery here; the
  real `.v2-cfg` spoke environment's own comment about `tf_state_*` being
  excluded from that check is the concrete evidence tying it to this
  migration pass.
- 2026-09-29: **Found and resolved gap #16**, discovered while reviewing
  the "Cross-invocation output access" design (`docs/work/deploy-command.md`)
  before implementing new checks on top of it, per request ("lets review
  the design first"). `unresolved_value_tokens()` still unconditionally
  rejected every `${output:...}` token — confirmed empirically against
  gap #12's own passing DNS test fixture, which only ever exercises
  `deploy_run()` directly, never `strata validate`'s real Phase 2 path.
  Expanded scope on the same review, per direct follow-up ("well not only
  terraform - but for all provisioners" / design review): the identical
  bug affects Modules too, not just DNS/network/firewall, since
  `unresolved_value_tokens()` has no per-document-kind special-casing.
  Wrote the full 4-step implementation plan into `deploy-command.md` first
  ("lets create a design first so we can review it"), then implemented it
  per request ("ok lets fix the bug, we will be revisiting the value
  resolver after this"): new `WorkspaceService.claimed_document_names()`
  generalizes gap #12's claiming rule to the full `target_names`
  vocabulary (dns_zones/networks/firewalls/namespaces, so a Module via a
  targeted namespace counts too); `_documents_reachable_from_workspace()`
  now threads `(document, claimed)` pairs;
  `unresolved_value_tokens()` gained `output_claimed: bool = False`.
  11 new tests across 3 files; full check suite green (mypy 107 files,
  ruff clean, import-linter 1/0, pytest 1254 passed); `.v2-cfg` still
  clean (12/12), `.v2-haven` still exactly its same known 44 pre-existing,
  unrelated errors. The remaining, larger "Cross-invocation output access"
  design (`SUPPORTS_OUTPUT` capability, the two new Phase 2 static checks,
  and the deploy-time opportunistic re-collection itself) is intentionally
  deferred — per the user's own framing, next up is revisiting the value
  resolver more broadly rather than continuing straight through those
  remaining steps.
- 2026-09-29: **Found and resolved gap #17** ("design, plan, and implement
  phase 1" — Phase 1 of
  [cross-document-value-references.md](cross-document-value-references.md)'s
  Implementation Plan). Confirmed the real gap directly against source
  before changing anything: `_build_providers_payload()`
  (`terraform_projection.py`) never read `ProviderSpecModel.configuration`/
  `.custom`, and grep confirmed nothing else in the codebase reads them
  either — the two fields were validated (whole-document walk,
  `_documents_reachable_from_workspace()`) but never actually delivered
  anywhere, live in `.v2-haven`'s real `hetzner-eu-de.yaml`. Fixed
  `_build_providers_payload()` to include both; extended
  `build_configuration_payloads()` from its curated 5-category subset to
  all 10 non-claimable categories (`workspace`/`providers` newly included
  unconditionally, `namespaces`/`flags`/`variables` newly included as
  true no-ops) — `deploy_controller.py` needed no logic changes, only
  updated comments, since it already called `build_configuration_payloads()`
  generically. 3 new tests, 4 existing tests updated for the wider
  category set. Full check suite green: mypy (107 files), ruff,
  import-linter (1 kept, 0 broken), pytest (1258 passed).
- 2026-09-29: **Split gap #7, resolved its `security` piece**, per request
  ("lets find the next prio so we can deploy the cfg-int-deployemtns" ->
  narrowed via `vscode_askQuestions` to "resolve remotes placement + model
  security/zones"). Read `config-deploy`'s real, current `config/`
  directory directly (`e:\sources\config-deploy`, 7 files) rather than
  relying on ADR-0020's older workflow-only survey — found gap #7's
  blanket "all deferred" framing was too coarse: `zones` and `remotes` are
  both already resolved by existing, deliberate v2 design (`TenantSpecModel.
  geographies`/`SolutionRemoteModel`'s docstrings respectively), not open
  gaps at all; `integrations` (the real repo's embedded-list style) is a
  shape-translation migration action, not a modeling gap, now that
  ADR-0021's standalone `kind: integration` exists; only `security` had no
  v2 equivalent anywhere. Implemented `security`: new
  `ConfigurationSecurityModel` (`allowed_secret_stores`/
  `allowed_variable_stores`/`allowed_feature_stores`, each independently
  optional, no `additional_*: bool` escape hatch since real usage is a
  strict allow-list) on `ConfigurationSpecModel.security`; new
  `EnvironmentService.validate_allowed_stores()`, following
  `validate_artifact_references()`'s exact precedent; wired into
  `semantic_checks.py`'s `_check_environments()` using
  `_check_workspace_topology_components()`'s existing "exactly one
  Configuration document, else skip" pattern (multiple same-kind
  Configuration documents already known-unhandled, ADR-0003). 13 new tests
  (4 model, 5 service unit, 4 semantic-check integration). Full check
  suite green: mypy (108 files), ruff, import-linter (1 kept, 0 broken),
  pytest (1318 passed — same pre-existing, unrelated `config/`
  example-solution drift as the sole failure). `strata validate .v2-cfg`
  re-confirmed clean (12/12), unaffected. `policies`/`audit`/`paths`/
  `promotions` remain open — real active usage in the source repo is
  narrower than their documented feature catalogs (only 2 of many
  documented policy types are actually enabled today), a future design
  should scope to that real subset first.
- 2026-09-29: **Resolved gap #7's `paths` sub-item**, per request ("lets
  look at paths, lets create the design first" -> "design, plan, and
  implement"). Full design written first
  ([docs/design/path-conventions.md](path-conventions.md)), all 3
  open questions resolved before implementing. Grounded in the real,
  current `config-deploy/config/paths.yaml` and a real deep leaf
  deployment's actual `layers:` usage. Found v1's generic `validate:`
  sub-block isn't worth porting (both real instances already covered by
  more precise v2 checks elsewhere), and caught a real discrepancy while
  grounding implementation against test fixtures: v1's own real `hub-path`/
  `control-path` patterns match only the containing directory (no
  filename); this implementation's `pattern` always matches a document's
  full relative path instead — simpler, documented as a deliberate
  difference. Implemented: `PathConventionModel`/`PathSegmentModel`/
  `ConfigurationSpecModel.paths` (`segments` required when
  `resolves: layers`, model-validator-enforced); `strata/utils/
  path_conventions.py` (pure regex matching — `in_scope()`/
  `compile_pattern()`/`match_pattern()`/`matches_segment_pattern()`, no
  Pydantic dependency, mirrors `path_safety.py`'s own placement);
  `semantic_checks.py`'s `_check_paths()` (structural shape matching, plus
  `resolves: tenant` code-vs-`meta.name` and `resolves: layers`
  segment-pattern cross-checks) and `_check_deployment_layers()` (finally
  activates `DeploymentLayersModel`, previously "inert in v2" — cross-checks
  a declared `layers.segments` against the real path, unknown `follows`
  names always error, agreement/absence otherwise skip cleanly);
  `run_semantic_checks()`/`solution_context.py` thread a new `root: Path`
  parameter through, same shape as the existing `solution` parameter.
  Findings' severity (warning vs. error) follows each convention's own
  `enforcement` field (default `warn`, matching the one real v1 policy
  enabled today) — the full generic policy engine (phase/type dispatch
  across `tenant_zone`/`naming_pattern`/`cve_max_severity`/etc.) is not
  built; `enforcement` is modeled directly on `PathConventionModel`
  instead. 32 new tests (9 model, 11 utility, 12 semantic-check
  integration). Full check suite green: mypy (109 files), ruff,
  import-linter (1 kept, 0 broken), pytest (1350 passed — same
  pre-existing, unrelated `config/` example-solution drift as the sole
  failure). `strata validate .v2-cfg` re-confirmed clean (12/12) — the
  fixture declares no `paths`/`layers` at all, so both new checks skip
  cleanly, zero new findings. `policies`/`audit`/`promotions` remain open.
- 2026-09-30: **Redesigned and re-implemented `pattern`** as `pattern` +
  independently optional `filename_pattern`, per direct feedback on the
  2026-09-29 pass ("we will need to drop the file part" -> clarified over
  several turns to "not everyone will call the file a specific thing? at
  least it should be optional... should it be a regex too?"). `pattern`
  now matches only a document's containing directory; `filename_pattern`
  (also `{name}`-capable, also optional) independently matches just the
  filename — fixes the discrepancy the first pass had already flagged
  (v1's real `hub-path`/`control-path` never mention a filename at all)
  *and* is required for `provider-path`'s real shape, discovered while
  designing this: its one real capture (`{region}`) lives entirely in the
  filename, with no directory capture at all — a case the single
  combined-pattern design could express but conflated two independent
  questions into one string. Design completed and its Implementation Plan
  finalized first (a dedicated "design, plan" pass per explicit request),
  *then* implemented as a rework of the 2026-09-29 code (not net-new):
  `strata/utils/path_conventions.py`'s `match_pattern()` replaced by
  `match_directory()`/`match_filename()` (both thin wrappers over the
  unchanged `compile_pattern()`) plus a new `split_directory_and_filename()`;
  `semantic_checks.py`'s `_check_document_against_path_convention()` and
  `_check_deployment_layers()` updated to the two-step match;
  `PathSegmentModel`, `enforcement`, `resolves`, and the single-
  Configuration-document/unknown-`follows` rules are all unchanged, since
  they only ever cared about the merged captures dict, not which half
  produced them. 12 new tests (3 model, 8 utility rewritten for the split
  API, 2 semantic-check integration covering `provider-path`'s real
  filename-only-capture shape) on top of the existing 32 (mostly rewritten
  in place, not additive, to match the new shape). Full check suite
  green: mypy (109 files), ruff, import-linter (1 kept, 0 broken), pytest
  (1362 passed — same pre-existing, unrelated `config/` example-solution
  drift as the sole failure). `strata validate .v2-cfg` re-confirmed
  clean (12/12), zero new findings. `policies`/`audit`/`promotions`
  remain open.
- 2026-10-01: **Revised gap #5** for [ADR-0028](../decisions/0028-topology-inline-reversion.md)
  (reverted Topology from a standalone `kind: topology` document back to
  an inline `Workspace.spec.topology[]` field — zero real cross-workspace
  reuse found, and the inverted component-reference direction was a real,
  self-admitted structural cost). Gap #5 is now genuinely *less* migration
  work than previously documented (1 v2 document instead of 3-4) — the
  provisioning-decoupling half of ADR-0011 is unaffected and still the real
  non-mechanical part. Updated the migration guide in place (not
  retracted — the provisioner/execution-recipe half it documents is still
  fully accurate) and flagged `.v2-cfg/`'s own fixture as now stale
  (still on-disk in the pre-ADR-0028 standalone shape; `WorkspaceModel` no
  longer parses it; left unfixed deliberately, Phase 5 of the ADR-0028
  reversal — see [docs/design/topology-standalone-kind-reconsideration.md](topology-standalone-kind-reconsideration.md)).