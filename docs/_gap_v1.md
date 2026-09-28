# v1 -> v2 Coverage Gaps

- Status: living — update in place as gaps are closed or new ones are found
- Last updated: 2026-09-28 (gap #8 Phase 1 + escape-syntax amendment)

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

### 1. Module `spec.references.secrets` has no v2 equivalent

- **Found in:** every haven module that declared a `references.secrets`
  list (e.g. `authentik.yaml`, `vaultwarden.yaml`, `homarr-secrets.yaml`).
- **Status:** the *field choice* is deliberate (ADR-0002) and confirmed by
  [docs/design/value-token-resolution.md](design/value-token-resolution.md)'s
  Per-Kind Status table: v1's declared-keys list is replaced by inline
  `${secret:KEY}` value tokens at the point of use
  (`ModuleServiceEnvironmentModel.value` — the only Value-token field a
  Module has; that doc states outright that `configuration`/`custom` is
  "raw passthrough, not token-aware" for every kind, not just Resource).
  **But rewriting to the correct field does not fully work today either** —
  see gap #9: the deploy-time resolver that would substitute a module's
  `${secret:KEY}` into the already-rendered Helm/Compose artifact is
  designed but not implemented for any kind except one Terraform-specific
  field. So this is by-design at the schema level, but blocked on #9 at the
  runtime level.
- **Closes when:** gap #9 alone is fixed (specifically, the resolver
  reaching `ModuleServiceEnvironmentModel.value` via Helm `--set-string`/
  Compose env-var injection) — gap #9 is both necessary and sufficient
  here. Gap #8 (`configuration:`/`custom:` passthrough) doesn't apply,
  since this entry's own migration action already avoids that field; gap
  #10 (`strata validate` can't detect an unresolvable token) is a
  discoverability improvement only, not a functional prerequisite.
- **Migration action:** rewrite every `environment[].secret: KEY` entry to
  `environment[].value: "${secret:KEY}"` under `spec.services[]` — this is
  the schema-correct target, but confirm #9 is resolved before relying on
  it actually reaching a deployed container/pod. If the target chart's
  values shape doesn't fit `spec.services` at all (a third-party chart with
  fixed top-level keys — see gap #8's `immich.yaml` example), there is
  currently no working migration path for that secret through strata's
  token engine.

### 2. Module `source.chart_repository` (inline Helm registry URL) has no v2 equivalent

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
- **Reopened as a design question, per request** (2026-09-28): two candidate
  directions to remove the 1:1-case friction, neither designed yet:
  1. **Let a module's `source` declare a chart repository inline**
     (bring back something like v1's `chart_repository`, as an *alternative*
     to `remote:` — not a replacement), for the common single-consumer case,
     while still requiring `remote:` when a registry genuinely needs
     credentials (`integration:`) or is shared across modules. Needs a
     validation rule for the two being mutually exclusive, same shape as
     `ModuleServiceModel.image`/`.artifact`.
  2. **Route chart registry location through the `artifact` kind** ("make
     OCI artifact-aware"). Needs to reconcile with an existing, explicit
     decision: `artifact_model.py`'s own docstring already rejected a "chart
     mode" — but for a narrower reason (chart *version* pinning is already
     served by `kind: version`'s `pins.charts` overlaying a module's
     `SourceModel.chart_version` — ADR-0026). That rejection was about the
     version pin, not the registry *location* a chart is pulled from, so
     this direction isn't automatically blocked by it, but needs to
     explicitly address why registry location is a different concern before
     reusing `artifact` for it.
  Until one of these is designed, the migration action below is the only
  path.
- **Migration action:** declare one `spec.remotes` entry per distinct chart
  source in `strata.yaml`, then reference it via `source.remote` + `chart_name`
  instead of `chart_repository`. For haven: 6 remotes needed
  (jetstack, twin/gatus, homarr-labs, immich-app, jellyfin.github.io,
  nextcloud.github.io) — mechanical, no design question left open, just
  more entries than the 1:1 mapping makes feel worthwhile.

### 3. OCI-type remotes require a `reference`, but an OCI chart index has no natural one

- **Found in:** `strata.yaml`'s `jetstack`/`homarr-charts`/`immich-charts`
  remotes (all `type: oci`).
- **Status:** minor schema friction, not blocking. `SolutionRemoteModel`
  requires `reference` for every remote type uniformly, but for a chart
  registry the actual version pin lives on `chart_version` at the module's
  `source` (module-level, not remote-level) — there is no single meaningful
  "ref" for the whole registry the way a git branch/tag/commit is.
- **Relationship to gap #2:** related but not automatically closed by it —
  depends which direction #2 takes. Direction 2 (route chart registries
  through `artifact`, replacing the remote-based path for charts entirely)
  would likely close this as a side effect: the remaining `type: oci`
  remotes would be genuine artifact sources with a real ref, or would route
  through `artifact` instead (no `reference` constraint there at all).
  Direction 1 (inline `chart_repository` as an *alternative* to `remote:`,
  not a replacement — `remote:` stays required for shared/credentialed
  registries) only shrinks how often this is hit, it doesn't fix the
  underlying schema rule. Either way, this gap is broader than charts —
  `RemoteType.OCI`'s own docstring says OCI "serves container images, Helm
  charts and arbitrary artifacts alike," so a non-chart OCI source with no
  natural single ref could hit the same friction regardless of #2's outcome.
- **Migration action:** use a placeholder (`reference: latest`) until/unless
  the model special-cases chart-serving OCI remotes.

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

### 5. Workspace/Topology/Provisioning decoupling (ADR-0011) is the biggest structural rewrite

- **Found in:** haven's single monolithic `config/stack/workspace.yaml` —
  topology + resources + namespaces + firewalls + provisioners all nested
  together, with `provisioner`/`topology` bound per-stage in
  `deployment.yaml`.
- **Status:** deliberate (ADR-0011) — not an open gap, converts cleanly, but
  is real, non-mechanical work: one v1 document becomes 4 v2 documents
  (`Workspace` + 2 standalone `Topology` documents + an explicit
  `execution:` step recipe replacing v1's per-stage `topology:`/
  `provisioner:` binding).
- **Migration action:** no shortcut — a migration guide/tool needs to spend
  real explanation budget here; this is the one part of the migration that
  isn't a mechanical field rename.

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
- **Status:** deliberate, already deferred per `ConfigurationSpecModel`'s
  own docstring (ADR-0003) — reconfirmed here with a concrete example, not
  newly discovered.
- **Migration action:** none — dropped with a comment pointing here until
  the corresponding v2 kind/feature is built.

### 8. Value tokens inside `spec.configuration`/`spec.custom` passthrough dicts are never resolved

- **Found in:** `modules/immich.yaml` (`configuration.controllers.main.
  containers.main.env.DB_PASSWORD: "${IMMICH_DB_PASSWORD}"` — deliberately
  using raw `configuration` instead of `spec.services` per that module's own
  comment, since this chart's top-level keys are fixed by the chart and
  don't fit strata's per-service shape); `modules/nextcloud-secrets.yaml`,
  `modules/homarr-secrets.yaml`, `modules/grimoire-secrets.yaml`, and
  `modules/pgadmin.yaml` (all using a bare `${VARNAME}` placeholder inside
  `configuration.env`, not even `${secret:KEY}` syntax).
- **Status:** open gap, confirmed in source, more specific than gap #1's
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
  [docs/design/value-token-resolution.md](design/value-token-resolution.md)'s
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
  [docs/design/value-token-resolution.md](design/value-token-resolution.md)'s
  new "Escape syntax" section — not implemented yet, design only.

### 9. The deploy-time Value-token resolver is designed for every kind, but only implemented for one field

- **Found in:** [docs/design/value-token-resolution.md](design/value-token-resolution.md)
  itself, cross-checked against `deploy_controller.py` and
  [docs/design/deploy-command.md](design/deploy-command.md) — not found via
  a haven document, but the underlying reason gaps #1 and #8 can't actually
  be closed by a schema-correct rewrite alone.
- **Status:** partially open. The design decided (2026-09-25, that doc's
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
  - **Helm** ([helm.py](../src/strata/integrations/helm.py) `prepare_namespace()`):
    secrets are meant to go in via `helm upgrade --set-string KEY=<value>`
    — never rewritten into the `values.yaml` `build run` already wrote.
    Caveat: a CLI arg is visible via `ps`/`/proc/<pid>/cmdline` to any
    local process with sufficient privilege — a real, if smaller, exposure
    than an env var. This is Helm's own best available mechanism, not a
    strata design compromise.
  - **Compose** ([compose.py](../src/strata/integrations/compose.py)
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
  [docs/design/value-token-resolution.md](design/value-token-resolution.md)'s
  "unify gaps #8/#9/#10" section): concrete per-integration delivery
  (Terraform `TF_VAR_<name>=<json>` for the whole `dns`/`networks`/
  `firewalls` payload; Helm `values.yaml` rewrite + `--set-string` for
  secrets; Compose file rewrite + `env:` kwarg for secrets) and a 6-phase
  implementation plan.
- **Migration action:** none — implementation not started; the phased plan
  in the linked design section is the concrete next step, no longer
  blocked on undesigned questions.

### 10. `strata validate` cannot detect a Value token that will never be substituted

- **Found in:** investigating "how would a user even know where `${var:}`/
  `${secret:}`/`${feature:}` is safe to use" (a direct follow-up question,
  not a haven document) — traced the actual Phase 2 check,
  `unresolved_value_tokens()` in `semantic_checks.py`.
- **Status:** open gap, confirmed in source. The check calls
  `model.model_dump()` and recursively scans **every string in the whole
  document** (`_iter_strings()`) — it does not restrict itself to the
  "known" token-aware fields (gap #8), so it genuinely does catch a
  malformed/undeclared key anywhere, passthrough dict included. But this
  produces two silent failure modes, not one:
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
  [docs/design/value-token-resolution.md](design/value-token-resolution.md)'s
  "unify gaps #8/#9/#10" section): once gap #9's resolver covers every
  string in a document instead of a fixed field allowlist, this gap's
  false-green-light failure mode disappears by construction — no code
  change needed here specifically if #9 lands completely. Only the
  separate bare-`${KEY}`-token (no `kind:` prefix) blind spot would remain,
  as its own smaller, lower-priority lint-hint item.
- **Migration action:** none — this is a validation/tooling gap, not a
  migration one, and is now expected to close as a side effect of gap #9's
  implementation rather than needing its own separate fix.

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
- **Note for gap #9's eventual implementation:** when the deploy-time
  resolver's DNS/network/firewall/module call sites get built, they should
  route through the same kind-agnostic resolution path already proven for
  `backend.configuration` (merging `visible_outputs` into the flat `tokens`
  dict) — `${output:}` needs zero extra design work there. This gap was
  purely about the *validator* not crashing before that resolver exists;
  it does not change gap #9's design or its 6-phase plan.

## Not gaps (converted cleanly)

Confirmed during the same migration to have zero loss of expressiveness:
namespace grouping, firewall rules (`from`/`to`/`port`/`proto`/`interface`),
module services/mounts/healthchecks/environment, secret `generate` specs,
and the provider/providerconfig/topologyconfig registry split.

## Related

- `.v2-haven/` at the workspace root — a full 52-document hand-migration of
  every real document in `e:\SourcesXYZ\haven\config`, kept as a live
  fixture. `strata validate` passes clean against it. Re-run validate there
  after any model change touching provider/resource/firewall/module/
  namespace/workspace/topology/deployment/environment to catch a real
  regression.
- [docs/decisions/0011-topology-and-provisioning-decoupling.md](decisions/0011-topology-and-provisioning-decoupling.md)
- [docs/decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md](decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md)
- [docs/decisions/0015-solution-manifest-and-document-discovery.md](decisions/0015-solution-manifest-and-document-discovery.md)
- [docs/design/deploy-command.md](design/deploy-command.md)
- [docs/design/value-token-resolution.md](design/value-token-resolution.md) — the
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