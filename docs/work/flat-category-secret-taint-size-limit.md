# FLAT_CATEGORIES Secret Taint Forces Whole-Key Env Var Delivery — Work

- Status: **superseded (2026-10-09)** — see
  [unified-terraform-projection.md](unified-terraform-projection.md),
  which deletes this gap structurally (per-leaf secret granularity by
  construction) rather than patching it per-category. This doc is kept
  for its evidence record and reasoning trail — the gap analysis, the
  per-kind reference tables, the real-consumer review, and the
  file-vs-env-var mechanism walkthrough are all still accurate and were
  what the new design was built from. It is no longer a live proposal.
- Last updated: 2026-10-09
- Related: [build-time-value-categories.md](../design/build-time-value-categories.md)
  (the "Reference: `properties`/`configuration`/`custom` by kind" section
  this was split out of — describes the current, working mechanism; this
  doc tracks the one real gap in it), [terraform-variable-precedence.md](../design/terraform-variable-precedence.md)
  (the file-vs-`TF_VAR_` precedence design this gap is a side effect of)

## Overview

Found while explaining `FLAT_CATEGORIES`' dual file/env-var delivery in
conversation, not from a real incident yet. `resolve_deploy_time_files()`'s
secret taint check is tracked **per top-level key, not per leaf**:

```python
tainted = {path.split(".", 1)[0] for path in secrets}
```

So if even one leaf deep inside a large nested value (e.g.
`tenant_gateway.c0062.some_secret_field`) is secret-shaped, the **entire**
top-level key — however large, however many entries — is omitted from the
`*.auto.tfvars.json` file and pushed whole through its `TF_VAR_<key>` env
var instead, JSON-encoded as one blob.

This is a real mismatch of assumptions: the env-var channel was sized
around secrets (small, scalar — a password, a token), not around "a
growing multi-tenant map that happens to contain one secret somewhere
inside it." A structure that's 99% safe to put in the file gets dragged
entirely into an env var the moment any one nested field is secret-shaped.

## How resolved values actually reach Terraform: file vs `TF_VAR_` env var

Background for the gap above — came up while tracing whether Tenant's
`properties`/`configuration`/`custom` are actually used anywhere, and
worth keeping close to the rest of this doc since it's the exact
mechanism the gap is a side effect of. Nothing new found here — all of
this is fully documented already; kept as a condensed, doc-local summary
rather than making every reader re-derive it from
[terraform-variable-precedence.md](../design/terraform-variable-precedence.md)
and ADR-0022/ADR-0031 from scratch:

- **Tenant's three fields are used via two separate, non-overlapping
  paths** (a good worked example of the file/env-var split): `configuration`
  is read directly into the `tenant` category -> `TF_VAR_strata_tenant`
  (`_build_tenant_payload()` doesn't touch `properties`/`custom` at all);
  `properties`/`custom` instead fold in separately, as a base layer onto
  the deployment's own `properties`/`custom`, in `deployment_resolution.py`,
  before the merge chain runs.
- **How `TF_VAR_strata_tenant` is used downstream**: a real consuming
  Terraform module would declare `variable "strata_tenant" {}` and read
  `var.strata_tenant.configuration`. No such declaration exists in this
  repo — that lives in a real consumer repo (haven/cfg-int-deployment),
  outside strata-v2 itself.
- **The file is always the primary channel; the env var is the
  secret-safety fallback**, not a competing mechanism — both are
  populated independently by strata from the same resolved value;
  Terraform's own built-in precedence (file always outranks env var)
  decides which one actually takes effect. Fully documented in
  [terraform-variable-precedence.md](../design/terraform-variable-precedence.md).
- **Why both channels exist at all** (not redundant-by-accident): the env
  var is the original, pre-existing delivery mechanism; the file-rewrite
  was an *additive* fix (ADR-0031) for a real, externally-reported bug
  (Terraform's own precedence meant the stale, unresolved build-time file
  was silently shadowing the already-correct env var). Both are still set
  unconditionally, every time, specifically so "did my secret-detection
  logic classify this correctly" is the only judgment call in the whole
  mechanism, not two independently-fallible ones.
- **Why the file has to exist regardless of the env var** — `build run`'s
  own job (ADR-0022) is to produce a complete, standalone Terraform
  working directory as a real deliverable artifact (hashable/cacheable/
  uploadable in CI) — usable with `terraform plan`/`apply` directly, with
  no `strata deploy run` wrapper and therefore no env var ever set. The
  file isn't scaffolding for the env-var mechanism; the env-var mechanism
  is the *addition* layered on top of a file that already had to exist
  and be correct for its own, separate reason.

### Follow-on Q&A: does Terraform have an in-file env-var reference, and could `build run` be skipped?

A chain of questions that came up reasoning through the above, worth
keeping together since each answer builds on the last:

- **Does `.tfvars.json` support anything like `${SOME_ENV_VAR}` as an
  in-file pointer to an environment variable?** No — Terraform has no
  such mechanism anywhere in its language. `.tfvars`/`.tfvars.json` files
  hold only literal values, no interpolation/functions/expressions; there
  is no `env()`-equivalent function; the *only* env-var mechanism
  Terraform has at all is the fixed `TF_VAR_<name>` CLI convention, which
  isn't referenced *from inside* a file — it's a separate, parallel
  precedence tier. This is exactly why the only lever available is
  omitting a key from the file entirely (see "blanked to `{}`" above) —
  there's no in-file syntax to point at an env var instead.
- **So is the only alternative "replace every non-secret token with its
  value in the file, and clean secrets out of it"?** Yes — and that's
  precisely what already happens. Confirmed in `resolve_deploy_time_files()`:
  every non-secret token is replaced with its resolved literal value
  directly in the file; every secret-shaped key is omitted from the file
  (not replaced with a reference — there's nothing to reference), falling
  through to `TF_VAR_<name>` by Terraform's own precedence instead.
- **Is that "replace tokens in the file" an in-place edit, or a full
  regenerate-and-overwrite?** Full regeneration, confirmed in
  `deploy_controller.py`: `rewrite_payloads` is built fresh from the
  resolved graph (the same `_build_<category>_payload()` functions
  `build run` itself calls, invoked again), not read back from the old
  file and patched — the whole file is overwritten
  (`(path / filename).write_text(json.dumps(data))`) with freshly
  recomputed, fully resolved content every time.
- **Then do we even need `TF_VAR_` for the non-secret keys, since the
  file is already correctly regenerated?** No — functionally it's inert
  for those; Terraform will use the file's value regardless, since the
  file always outranks the env var. It's still set unconditionally for
  every key (ADR-0031's deliberate simplicity choice — one less
  conditional branch to get wrong), but **only secret-shaped keys
  genuinely need the `TF_VAR_` channel to do real work**.
- **Is full regeneration actually better than in-place token patching?**
  Yes, and not just incidentally: the expensive part (resolving every
  token, detecting every secret-shaped leaf) has to run in full either
  way, so once that fully-resolved dict exists in memory, overwriting the
  file with it is strictly simpler than also solving the harder,
  unnecessary problem of surgically patching the old file's JSON in
  place. Regeneration is also inherently driftless and idempotent — it's
  always a pure function of the current resolved graph, so re-running
  `deploy run` any number of times converges to the same correct file,
  with no risk of leftover state from a prior patch.
- **So, strictly, could a CI/CD pipeline skip `build run` and just call
  `deploy run`?** No. `deploy run` only *rewrites* the
  `*.auto.tfvars.json` files inside a directory — confirmed directly in
  `deploy_controller.py`'s own docstring: it executes "against `build_path`
  (already rendered by a prior `build run` — this never re-renders it)."
  It never creates the directory, copies `.tf` module sources, or renders
  the provisioner layout — that's entirely `build run`'s job (ADR-0022).
  Skip `build run` and there is nothing for `deploy run` to rewrite or for
  Terraform to run against at all. Confirmed as real practice too: both
  real reference repos checked for this codebase "universally follow
  `build run` with `strata deploy run`" in their actual CI.
- **Does any of this tfvars/`TF_VAR_` machinery apply workspace-wide, or
  only where Terraform is actually used?** Gated **per provisioning
  step**, not per-workspace. Each step's `Provisioner` declares its own
  `tool` (resolved in `integration_resolution.py` via `provisioner.tool`);
  only a step whose resolved tool has an `ENV_VAR_PREFIX` at all goes
  through this mechanism — confirmed directly in `deploy_controller.py`:
  `if integration.ENV_VAR_PREFIX is not None:` gates the entire
  rewrite-file/set-env-var block, with the comment "a tool with no
  `ENV_VAR_PREFIX` has no `*.auto.tfvars.json` convention to rewrite
  either." A workspace with zero `tool: terraform` steps (e.g.
  Helm/Compose-only) never touches any of this at all; a mixed workspace
  only applies it to its own Terraform-tooled steps. This entire doc's
  secret-taint gap is therefore specifically a Terraform-integration
  concern, not a workspace-wide one.

## Mental model: what `properties`/`configuration`/`custom` are actually *for*

Initial framing was close but not quite the real distinction — worth
stating properly, since it's not obvious from the schema alone:

- **`properties`** — the fields meant to *drive* deployment-varying,
  strata-aware composition: values contributed by a Workspace/Environment/
  Deployment/Tenant, deep-merged across that whole chain before reaching
  the provisioner. This is the one channel that's genuinely *composed*
  across several documents, not just carried through from one.
- **`configuration`** — per-document, opaque, **unmerged** passthrough to
  whatever tool actually consumes it (Terraform/Bicep for IaC kinds,
  Helm/Compose for app kinds). "Configure the system" is the right
  instinct, but strata itself never validates or interprets the contents —
  it's forwarded byte-for-byte to the underlying tool's own config surface
  (a Terraform provider-block argument, a Helm chart value). Stays local
  to the one document that declared it; never merged with a sibling
  document's `configuration`.
- **`custom`** — free-form bookkeeping/automation data, same
  opaque-passthrough treatment as `configuration` on most kinds, with no
  assumed consumer at all. **Exception**: on Workspace/Environment/
  Deployment/Tenant specifically, `custom` merges like `properties` does,
  not like `configuration` — it's the odd one out.

**Correction to the initial framing**: it isn't a three-way split *by
meaning* (properties = strata-driven, configuration = tool-config,
custom = user-data) — `properties` and `configuration` are both equally
free-form `dict[str, Any]`, unvalidated by strata either way. The real
axis that decides behavior is **which kind the field lives on**,
specifically whether that kind participates in the Workspace→Environment→
Deployment(+Tenant) merge chain:

- **Workspace/Environment/Deployment/Tenant**: `properties`+`custom`
  merge across the whole chain; `configuration` does not (Tenant's
  `configuration` isn't even forwarded anywhere at all).
- **Every other kind** (Provider/Resource/DNS/Network/Firewall/
  Namespace/Module/Integration): `configuration`+`custom` travel
  together, unmerged, as a flat passthrough attached to that one
  document only.

### Which capability each document's values ultimately reach

This matches the "iac vs. app" split from the initial framing exactly:

- **IaC capability (Terraform/Bicep)** — Provider, Resource, DNS, Network,
  Firewall: `configuration`/`custom` ride into a Terraform structured
  category (`providers`/`resx_<type>`/`dns`/`networks`/`firewalls`),
  ending up in a `.auto.tfvars.json` file and/or `TF_VAR_<category>`.
- **App capability (Helm/Compose/ArgoCD)** — Namespace, Module:
  `configuration`/`custom` feed the workload pipeline instead —
  `values.yaml`/`meta.yaml`/`docker-compose.yml` — a structurally separate
  path from Terraform (ADR-0022 D5–D7). (Namespace's own
  `configuration`/`custom` are also separately echoed into the
  `namespaces` Terraform category, for visibility only.)
- **Neither** — Integration (feeds the connection/auth layer only),
  `kind: configuration` (audit/policy only): both genuinely inert as far
  as the deploy-value pipeline is concerned.

### Worked examples

**Workspace** (participates in the merge chain):

```yaml
spec:
  properties:
    deploy_tier: standard        # merges across Workspace -> Environment -> Deployment
  configuration:
    some_raw_passthrough: true   # this workspace's own value only — embedded as one
                                  # JSON blob (TF_VAR_configuration), never merged
  custom:
    cost_center: "1234"          # ALSO merges across the chain, unlike configuration
```

**Provider** (does not participate in the merge chain — flat passthrough):

```yaml
spec:
  properties:                             # NOTE: a typed ProviderPropertiesModel
    type: azurerm                         # here, a different field entirely —
    region: westeurope                    # not the free-form dict
  configuration:
    skip_provider_registration: true      # a real Terraform provider-block argument,
                                           # forwarded as-is, this document only
  custom:
    billing_account: "acme-corp"          # bookkeeping only, not consumed by any provisioner
```

**Namespace** (app capability):

```yaml
spec:
  modules: [...]
  configuration:
    annotation_quota: "10Gi"   # k8s namespace annotation/quota passthrough,
                               # not validated by strata
  custom:
    owning_team: platform
```

The practical takeaway for a Resource/Provider author: think of
`configuration` as "whatever I'd normally put in that tool's own native
config block, just handed through strata unexamined" — not as a
strata-level concept with its own semantics.

## Streamlining: `properties` naming/schema consistency — split to its own doc

A genuinely separate concern from this doc's own secret-taint subject —
what `properties` means on Provider/Resource/Module (typed core fields)
vs. Workspace/Environment/Deployment/Tenant (free-form merge dict), and
whether Configuration's own unused `properties`/`configuration`/`custom`
should be dropped. Grew into its own large, mostly-decided body of work;
moved to
[flatten-provider-resource-module-properties.md](flatten-provider-resource-module-properties.md)
in full — see that doc for the decision, the Amendment, the full
per-kind field overview, and the concrete schema change list.

## Reference tables (copied from `build-time-value-categories.md`, kept in sync manually)

Context needed to work this gap without re-deriving it from source every
time — which kinds actually carry `properties`/`configuration`/`custom`,
and which Terraform category (if any) each one's data flows through. The
gap above only affects `FLAT_CATEGORIES` rows (`workspace`/`flags`/
`variables`/`properties`/`custom`) — every other shape (single-variable
broadcast, claimed/broadcast, grouped broadcast) tracks its own secret
taint at the whole-category level already, not per-key, so this specific
gap does not apply to them.

| Kind                      | `properties`                                                               | `configuration`          | `custom`                 | How it actually reaches Terraform                                                                                                                                                                                                                                                                                                                                                                                    |
| ------------------------- | -------------------------------------------------------------------------- | ------------------------ | ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **workspace**             | ✅ top-level                                                                | ✅ top-level              | ✅ top-level              | `properties`/`custom` go through the merge chain below. **Separately**, `configuration`+`custom` are *also* embedded raw/unmerged inside the `workspace` category's own payload (`_build_workspace_payload()`) → one `TF_VAR_configuration`/`TF_VAR_custom` each, as a whole JSON blob of the workspace's own value only (not merged with environment/deployment). `properties` is **not** double-embedded this way. |
| **environment**           | ✅ top-level                                                                | ❌                        | ✅ top-level              | Both feed the merge chain, one layer among Workspace→Environment→Deployment.                                                                                                                                                                                                                                                                                                                                         |
| **deployment**            | ✅ top-level                                                                | ❌                        | ✅ top-level              | Both are the *last*, highest-precedence layer of the same merge chain.                                                                                                                                                                                                                                                                                                                                               |
| **tenant**                | ✅ top-level                                                                | ✅ top-level              | ✅ top-level              | `properties`/`custom` fold into the **deployment's own spec as an outer base layer** (`deployment_resolution.py`, applied before the merge chain runs — deployment always wins on conflict) — so they ride the same `TF_VAR_<key>` delivery as the deployment's own. **`configuration` is explicitly excluded** — confirmed in source, only `environments`/`properties`/`custom` travel from Tenant.                 |
| **`kind: configuration`** | ✅ top-level                                                                | ✅ top-level              | ✅ top-level              | **None of the three reach Terraform at all** — confirmed zero references in `build_controller.py`'s payload functions. Used for audit/policy/security (`cve_allowed`, path conventions), a structurally separate concern.                                                                                                                                                                                            |
| **provider**              | ❌ (has a *typed* `properties: ProviderPropertiesModel`, a different thing) | ✅ top-level              | ✅ top-level              | Both documented as **"inert until that layer exists"** — validated, never delivered.                                                                                                                                                                                                                                                                                                                                 |
| **resource**              | ❌                                                                          | ✅ top-level              | ✅ top-level              | `configuration` merges two-layer (resource + workspace-instance override) into `resx_<type>.auto.tfvars.json`, all types combined into one `TF_VAR_resources`. `custom` is not part of that merge — same "not consumed" caveat as Provider's.                                                                                                                                                                        |
| **integration**           | ❌                                                                          | ✅ top-level              | ✅ top-level              | Both "raw passthrough... not validated... passed through as-is" — Integration isn't part of the deploy-value pipeline; these feed the integration's own connection logic, not `TF_VAR_*`.                                                                                                                                                                                                                            |
| **dns**                   | ❌ (on `DnsZoneModel`, nested, per-zone)                                    | ✅ per-zone               | ✅ per-zone               | Feeds the `dns` structured broadcast category (its own `.auto.tfvars.json`), not `FLAT_CATEGORIES`.                                                                                                                                                                                                                                                                                                                  |
| **network**               | ❌ (on `NetworkDefinitionModel`/`SubnetModel`, nested)                      | ✅ per-network/per-subnet | ✅ per-network/per-subnet | Feeds the `networks` structured broadcast category, same shape as DNS.                                                                                                                                                                                                                                                                                                                                               |
| **firewall**              | ❌ (on `FirewallDefinitionModel`, nested)                                   | ✅ per-definition         | ✅ per-definition         | Feeds the `firewalls` structured broadcast category, same shape.                                                                                                                                                                                                                                                                                                                                                     |
| **module**                | ✅ (typed `ModulePropertiesModel`, a different thing)                       | ✅ top-level              | ✅ top-level              | **Not Terraform at all** — Module deploys via the Helm/Compose workload pipeline, a structurally separate, disconnected input shape (ADR-0022 D5–D7). These become `values.yaml`/`docker-compose.yml` content, not `TF_VAR_*`.                                                                                                                                                                                       |
| **namespace**             | ❌                                                                          | ✅ top-level              | ✅ top-level              | Reaches Terraform too — `namespace.spec.configuration`/`.custom` are read directly into the `namespaces` single-variable broadcast category (`_build_namespaces_payload()`) → `TF_VAR_namespaces`. A *separate* consumer of the same document, `NamespaceSpecModel.modules`, independently feeds the Helm/Compose workload pipeline (module names only, not the full config, in the Terraform-facing payload).       |
| **solution**              | —                                                                          | —                        | —                        | Discovery root only — no `properties`/`configuration`/`custom` fields exist on this kind at all.                                                                                                                                                                                                                                                                                                                     |
| **providerconfig**        | —                                                                          | —                        | —                        | None of these three fields exist — registry/validation only.                                                                                                                                                                                                                                                                                                                                                         |
| **topologyconfig**        | —                                                                          | —                        | —                        | None of these three fields exist — registry/validation only.                                                                                                                                                                                                                                                                                                                                                         |
| **version**               | —                                                                          | —                        | —                        | None of these three fields exist — consumed by pin-resolution logic only.                                                                                                                                                                                                                                                                                                                                            |
| **artifact**              | ✅ top-level                                                                | ✅ top-level              | ✅ top-level              | All three exist, but the model's own docstring states `properties` is "never merged anywhere"; none of the three reach Terraform — inert by design.                                                                                                                                                                                                                                                                  |

| Kind                      | Category                         | Shape                                                                                                                                                                                                                                                                                                                      | Real variable name                                      |
| ------------------------- | -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| **workspace**             | `workspace` **and** `topologies` | `workspace`: FLAT (9 keys: `workspace_name`/`_version`/`deployment_name`/`environment`/`platform_version`/`labels`/`configuration`/`custom`/`default_tags`/`metadata`). `topologies`: single-variable broadcast, source is `workspace.spec.topology` itself (an inline block, ADR-0028), **not** the `topologyconfig` kind | `TF_VAR_<key>` per key (workspace); `TF_VAR_topologies` |
| **environment**           | *(no category of its own)*       | contributes constant/environment-store values into `flags`/`variables`, and `properties`/`custom` into the merge chain                                                                                                                                                                                                     | n/a                                                     |
| **deployment**            | *(no category of its own)*       | same contribution as Environment, plus the final/highest-precedence merge layer                                                                                                                                                                                                                                            | n/a                                                     |
| **tenant**                | `tenant`                         | single-variable broadcast (`{code, name, zones, onboarded, configuration}`); empty → file/var skipped entirely                                                                                                                                                                                                             | `TF_VAR_strata_tenant` (`_REAL_VARIABLE_NAME` rename)   |
| **provider**              | `providers`                      | single-variable broadcast (`name -> {type, region, display_name, description, labels, tags, configuration, custom, default_tags}`)                                                                                                                                                                                         | `TF_VAR_platform_providers`                             |
| **resource**              | `resx_<type>`                    | grouped broadcast — one file per resource type, ALL types merged into the one shared variable regardless of how many types exist                                                                                                                                                                                           | `TF_VAR_resources`                                      |
| **dns**                   | `dns`                            | claimed/broadcast                                                                                                                                                                                                                                                                                                          | `TF_VAR_dns_zones`                                      |
| **network**               | `networks`                       | claimed/broadcast                                                                                                                                                                                                                                                                                                          | `TF_VAR_networks`                                       |
| **firewall**              | `firewalls`                      | claimed/broadcast                                                                                                                                                                                                                                                                                                          | `TF_VAR_firewalls`                                      |
| **namespace**             | `namespaces`                     | single-variable broadcast (`name -> {description, labels, tags, modules: [names], configuration, custom, default_labels, custom_labels}`)                                                                                                                                                                                  | `TF_VAR_namespaces`                                     |
| **module**                | *(none)*                         | not Terraform at all — Helm/Compose workload pipeline (`values.yaml`/`meta.yaml`/`docker-compose.yml`), a structurally disconnected input shape (ADR-0022 D5–D7)                                                                                                                                                           | n/a                                                     |
| **integration**           | *(none)*                         | not Terraform at all — consumed directly by the integration/auth layer (credentials, endpoints)                                                                                                                                                                                                                            | n/a                                                     |
| **`kind: configuration`** | *(none)*                         | not Terraform at all — audit/policy/security only                                                                                                                                                                                                                                                                          | n/a                                                     |
| **providerconfig**        | *(none)*                         | registry/validation only — checked against (e.g. "is this a known provider type"), never itself delivered                                                                                                                                                                                                                  | n/a                                                     |
| **topologyconfig**        | *(none)*                         | registry/validation only — checked against (e.g. "is this a known topology type"), never itself delivered                                                                                                                                                                                                                  | n/a                                                     |
| **solution**              | *(none)*                         | discovery root only                                                                                                                                                                                                                                                                                                        | n/a                                                     |
| **version**               | *(none directly)*                | consumed by pin-resolution (`apply_remote_version_pins()`) — changes *which* artifact/tag another category ends up containing, never has a category of its own                                                                                                                                                             | n/a                                                     |
| **artifact**              | *(none)*                         | referenced by `kind: version`'s pins/variables; its own `properties`/`configuration`/`custom` explicitly "never merged anywhere"                                                                                                                                                                                           | n/a                                                     |

**Note on the pending Provider/Resource/Module flatten** (tracked in
[flatten-provider-resource-module-properties.md](flatten-provider-resource-module-properties.md),
not this doc): none of this table's rows change as a result. Confirmed
directly in `_build_providers_payload()`/`_build_resources_payload()` —
both already read `spec.properties.type`/`.region`/etc. and produce an
output payload that's *already* flat (`{type, region, display_name,
...}`). Flattening only moves where the *source* field lives on the
Pydantic model (`spec.properties.type` → `spec.type`); the
Terraform-facing category, shape, and `TF_VAR_` name for
`provider`/`resource` are unaffected either way. Module was already
`*(none)*`/not-Terraform-at-all, so its flatten doesn't touch this table
either.

## Why this matters

Real OS limits exist on environment variables — Windows has a ~32KB
single-variable limit (and process-wide env block limits), Linux has
`ARG_MAX`-tied limits on the whole environment block. A large enough
tainted structure could genuinely fail to deliver, or get silently
truncated by the OS/shell before Terraform ever sees it. Nothing in this
repo currently checks for or warns about this — confirmed via grep, no
existing doc addresses it.

Not yet observed as a real failure. No real consumer has hit this yet, as
far as is known — this is a found-by-inspection gap, not a reported bug.

## Candidate directions (none decided)

- **Per-leaf taint instead of per-top-level-key.** Only omit the specific
  secret-shaped leaf from the file (replacing it with some placeholder),
  not the whole top-level key — lets the rest of a large structure still
  go through the file. Real complexity: the file would then need to carry
  a *partial* structure with a hole in it, and the consuming `.tf` module
  would need the missing leaf reconstructed from somewhere — likely still
  an env var, but now for just the one leaf's path, not the whole
  structure. Needs a real design for how a `.tf` module would merge a
  file-sourced partial object with an env-var-sourced leaf.

  **Chosen (2026-10-08), narrowed to per-document-entry rather than
  arbitrary per-leaf — design started below, not complete.**
- **A size check + hard error at deploy time.** If a tainted key's
  JSON-encoded size would exceed some safe threshold, fail loudly
  instead of silently risking OS truncation. Cheapest fix, doesn't solve
  the underlying problem, just converts a possible silent failure into a
  loud one.
- **An authoring convention + lint warning**: never mix a secret into the
  same top-level `properties`/`custom` key as a large non-secret
  structure — give the secret its own small, dedicated key. Zero schema
  change; could be a `validate`-time warning if a tainted key's resolved
  size is "large" by some heuristic, nudging authors toward the
  convention rather than enforcing a hard split.
- **Do nothing until a real consumer hits it.** Consistent with this
  repo's own evidence-over-assumption convention — no real incident has
  happened yet, and the workaround convention above is free (just needs
  stating, not building).

## Redesign: generalize per-key taint tracking to every category (chosen direction, in progress)

- Status: design started, not complete — real open problem identified
  (see below), not yet solved.
- **Sequencing**: execute only *after* the Provider/Resource/Module
  properties rename
  ([flatten-provider-resource-module-properties.md](flatten-provider-resource-module-properties.md))
  lands — this design assumes the final, uniform shape (`properties`
  exists only on the 4 merging kinds; every other kind has
  `configuration`+`custom` only) as its stable foundation, not today's
  transitional one.

### Confirmed foundation (unchanged by this redesign)

- `properties` (and `custom`, on the same 4 kinds) globally merges:
  Tenant → Environment → Deployment, with Workspace folding in its own
  `properties`/`custom` too. One single, final, already-merged dict
  reaches the Terraform category — never per-document after the merge
  runs.
- Every other document's own `configuration`/`custom` stays local/
  per-document, never merged with a sibling — "each item keeps its own
  `configuration`/`custom`", exactly as stated.
- **Build** writes `*.auto.tfvars.json` with every value in its raw,
  build-time form — tokens (`${var:}`/`${secret:}`/`${feature:}`/
  `${output:}`) still literal text, unresolved.
- **Deploy** regenerates the same file from the resolved graph: every
  non-secret value is replaced by its real resolved value, written
  directly into the file; every secret-shaped value is instead dropped
  from the file and delivered via its own `TF_VAR_<name>` env var — the
  only mechanism Terraform offers for keeping a resolved secret out of a
  plaintext file, since there is no in-file env-var-reference syntax.

### The gap this redesign fixes

Today, "drop the secret-shaped value, swap it for a `TF_VAR_`" already
happens at the correct per-key granularity for `FLAT_CATEGORIES`
(`workspace`/`flags`/`variables`/`properties`/`custom`) — each is its own
independent Terraform variable, so one secret taints only its own key, not
its siblings.

Everywhere else (`tenant`/`providers`/`topologies`/`namespaces` —
single-variable broadcast; `dns`/`networks`/`firewalls` — claimed/
broadcast), the granularity is coarser: the **whole category** collapses
into **one** Terraform variable (e.g. `TF_VAR_platform_providers` holding
every provider's own entry, keyed by name) — so any secret anywhere
inside it taints the entire variable, blanking every document's data, not
just the one document whose own value happens to contain a secret.

### Proposed design: treat each document's own entry as its own "key", the same way `FLAT_CATEGORIES` already treats each top-level dict key

For every single-variable-broadcast / claimed-broadcast category, the
natural, pre-existing unit of structure is already "one entry per document
name" (`providers: {name -> {...}}`, `dns: {name -> {...}}`, etc.). This
redesign applies `FLAT_CATEGORIES`' own per-key rule one level down, to
these per-document entries, instead of to the whole category:

- **No secret anywhere in a given document's own entry**: that entry is
  written into the file as part of the category's shared dict, exactly as
  today.
- **A secret anywhere inside one document's own entry**: only *that*
  document's entry is omitted from the file (the category's dict in the
  file is now missing just that one key) — every *other* document's entry
  in the same category still rides through the file untouched.
- The omitted entry is delivered instead through a **second, separately-
  named "lookup" variable** — a map/dict keyed by document name, carrying
  *only* the entries that got pulled out this run (e.g.
  `TF_VAR_platform_providers_secrets` holding `{name -> value}` for just
  the tainted provider(s), never every provider). Not a fixed, per-name
  variable declared ahead of time for every possible document name — a
  single lookup map, sized to however many entries actually got tainted
  this run (often zero, meaning the variable is simply empty/unset).

**Worked example** — two providers, `azure` (secret-free) and `kamatera`
(has a `${secret:api_token}` somewhere in its `configuration`):

`providers.auto.tfvars.json` (file — `kamatera` omitted, `azure` written
normally, exactly as today's per-key rule already does for
`FLAT_CATEGORIES`):

```json
{
  "platform_providers": {
    "azure": { "type": "azurerm", "region": "westeurope", "configuration": {} }
  }
}
```

Env vars set for this step:

```
TF_VAR_platform_providers={"azure":{"type":"azurerm","region":"westeurope","configuration":{}}}
TF_VAR_platform_providers_secrets={"kamatera":{"type":"kamatera","region":"eu","configuration":{"api_token":"<resolved-value>"}}}
```

(Today, by contrast, `kamatera`'s one secret would blank the *entire*
`platform_providers` file and env var — `azure`'s entry would be dragged
into `TF_VAR_platform_providers` too, even though it has nothing
secret-shaped in it at all.)

### Full example: every file + env var for one workspace/deployment

Checked against v1 first (`E:\SourcesXYZ\strata\src\strata\builders\terraform_builder.py`)
before assuming anything changes here: v1's own `_build_provider_vars()`/
`_build_resources_by_category()` build a **dict keyed by document name**
(`providers_dict[provider.name] = {...}`), exactly like v2's
`_build_providers_payload()` — not a list. The "lists" that actually exist
in a Workspace/Deployment are the *reference* arrays declaring which
documents are in play, e.g. `workspace.spec.providers: [name, ...]` — a
list of names to look up, not the shape the Terraform-facing payload ends
up in. Both are real and worth showing together:

**Workspace** (`my-workspace`) — its own reference lists:

```yaml
spec:
  providers: [azure, kamatera]       # -> providers.auto.tfvars.json
  resources: [...]                   # 2 compute + 1 networking resource
                                      # -> resx_compute / resx_networking
  namespaces: [hearth]               # -> namespaces.auto.tfvars.json
  firewalls: [edge-fw]               # -> firewalls.auto.tfvars.json
  dns_zones: [example-zone]          # -> dns.auto.tfvars.json
  networks: [spoke-net]              # -> networks.auto.tfvars.json
  topology: [...]                    # inline -> topologies.auto.tfvars.json
```

**Deployment** (`prd-deploy`) — its own reference lists:

```yaml
spec:
  workspace: my-workspace
  environments: [base-env, prd-env]  # merge chain -> properties/custom
  tenant: c0062                      # -> tenant.auto.tfvars.json, and
                                      #    folds into properties/custom too
  configurations: [...]
```

**Every file `build run` produces** for this one workspace/deployment (13
categories total — confirmed in `build_configuration_payloads()`'s own
docstring, which lists all 13 by name):

```
workspace.auto.tfvars.json
providers.auto.tfvars.json
topologies.auto.tfvars.json
resx_compute.auto.tfvars.json
resx_networking.auto.tfvars.json
namespaces.auto.tfvars.json
firewalls.auto.tfvars.json
dns.auto.tfvars.json
networks.auto.tfvars.json
tenant.auto.tfvars.json
flags.auto.tfvars.json        # only if any declared
variables.auto.tfvars.json    # only if any declared
properties.auto.tfvars.json   # the merged Tenant->Environment->Deployment dict
custom.auto.tfvars.json       # same merge chain
```

**With the redesign applied, and `kamatera` tainted** (one secret
somewhere in its own `configuration`): this file list does **not**
change at all — the whole point of the mechanism is that a secret never
touches disk, so there is never a new *file* for it. What changes is the
**env var set** for this step gains exactly one extra entry:

```
TF_VAR_platform_providers           # unchanged name, now missing "kamatera"
TF_VAR_platform_providers_secrets   # NEW — {"kamatera": {...}} only
# every other TF_VAR_<category> (topologies/resx_*/namespaces/firewalls/
# dns/networks/tenant/flags/variables/properties/custom) is untouched —
# this redesign only ever adds a second env var for a category that
# actually has a tainted entry this run; most categories most runs add
# nothing at all.
```

### The real open problem this doesn't solve by itself

A `.tf` module today reads **one** variable (e.g. `var.platform_providers`)
and expects it to be the complete dict of every provider. With this
redesign, that dict may now be **missing** the one provider whose entry
got pulled into the lookup variable instead — the `.tf` module has no way
to know that happened, or where to find the missing entry, unless it's
specifically written to look.

This needs real design work on the **Terraform-consuming side**, not just
strata's own build/deploy logic:

- The `.tf` module would need its own `merge()` call, reassembling the
  main variable with the lookup variable — something like
  `merge(var.platform_providers, var.platform_providers_secrets)` — since
  a plain dict `merge()` with an empty second argument is a no-op, this
  costs the module nothing in the common (zero-secrets-tainted) case.
  Every real consumer's `.tf` code that reads one of these shared
  variables needs to adopt this convention, not just strata's own
  internals.
- Naming convention needed: `TF_VAR_<category>_secrets` (or similar) as
  the fixed, predictable lookup-variable name per category, computable
  deterministically from the category alone (not per-document-name) —
  the module always knows to declare and merge in exactly one extra
  variable per category, regardless of which (if any) document names end
  up inside it on a given run.
- This is the same complexity already flagged under "Per-leaf taint" in
  "Candidate directions" above, just scoped to per-document-entry rather
  than per-arbitrary-leaf — narrower, and therefore more tractable, but
  not yet a worked design.

### Reconsidered: v2 is alpha — is matching v1's exact variable shapes actually a hard constraint here?

Worth being honest about scope: this doc is a work doc exploring a fix,
not a committed design, and strata-v2 is alpha specifically so this kind
of thing *can* still be reconsidered before it hardens into a real
compatibility promise. The previous section's "every real consumer's
`.tf` code needs to adopt this convention" framing assumed the *existing*
one-variable-per-category shape (`platform_providers`, `resx_<type>`,
`dns_zones`, ...) has to stay fixed forever, matching v1 — that's true
*today* (real, already-written `.tf` code out there depends on exactly
those names), but it's a current-convention fact, not a law. If a
category-specific `_secrets` lookup variable has to be invented and
adopted by every real consumer root anyway, it's worth asking whether a
**simpler, universal** mechanism is actually less total work than a
**per-category** one — not assumed, just a real open question to weigh:

- **Per-category** (the design above): one `_secrets` lookup variable per
  broadcast category, each independently named
  (`platform_providers_secrets`, `dns_zones_secrets`, ...). Every real
  `.tf` root needs N new `merge()` calls, one per category it actually
  uses.
- **Universal, single mechanism** (not yet designed, worth exploring):
  **one** shared secrets-lookup variable across *every* category,
  keyed by a path (e.g. `{"providers.kamatera.configuration.api_token":
  "<value>"}`) rather than per-category/per-document-entry — true
  per-leaf granularity, not just per-document-entry, since a single
  generic mechanism no longer needs to special-case each category's own
  shape. Every real `.tf` root would need to adopt exactly **one** new
  convention (read this one shared map, look up by path, fill in any
  leaf it finds there) instead of N category-specific ones — fewer,
  more uniform `.tf`-side changes, at the cost of that one convention
  being a slightly less natural fit for plain HCL (`lookup()`-by-dotted-
  path rather than a direct `merge()`).

Not decided — genuinely "let's see how" territory, consistent with this
doc's own status (draft, not designed). Recorded here so the per-category
design above isn't mistaken for the only option considered, now that the
v1-parity framing that motivated it has been explicitly questioned.

### Immediate next step

The `.tf`-side convention now has a concrete shape to validate (main
variable + per-category `_secrets` lookup map, merged via `merge()`) —
next is checking it against a real consumer root (e.g. `cfg-int-deployment`'s
`customer/terraform`/`spoke/terraform`) to confirm `merge()`-ing in an
empty map really is a no-op for every real usage of `var.platform_providers`
(and the other broadcast categories) there, before committing to this
shape. The strata-side change itself (track taint per-entry instead of
per-category, and emit the second lookup-map env var) is small and
mechanical once `resolve_value_tokens_tracking_secrets()`'s own
secrets-path output is already available at that granularity.

**Before that**, though: decide per-category vs. universal (see
"Reconsidered" above) — the per-category validation step only makes
sense if per-category is actually the chosen shape.

## Remaining Work / Open Questions

- No real-usage evidence gathered yet — unknown whether any real
  consumer's `properties`/`custom` values are large enough for this to be
  a practical risk today, as opposed to a theoretical one.
- Pick a direction from the candidates above once (or if) real evidence
  of the problem shows up.
- If "do nothing" is the outcome, at minimum keep the authoring-convention
  note (already in `build-time-value-categories.md`'s reference table) as
  the documented mitigation.
- **Direction chosen (2026-10-08)**: per-document-entry taint tracking —
  see "Redesign" section above. The strata-side change is well
  understood; the real blocker is designing the `.tf`-side merge
  convention every consumer root would need to adopt. Not started.
  Sequenced after the Provider/Resource/Module properties rename lands.
- **Separate, split-out item**: flattening Provider/Resource/Module's
  typed `properties` sub-model fields, and Configuration's own dead
  `properties`/`configuration`/`custom` — moved to
  [flatten-provider-resource-module-properties.md](flatten-provider-resource-module-properties.md),
  not tracked here anymore.

## Changelog

- 2026-10-08: Created — split out of `build-time-value-categories.md`'s
  "Reference: `properties`/`configuration`/`custom` by kind" section,
  since this is a real, unresolved gap likely to need an actual fix, not
  a settled design fact appropriate for a finished design doc. No code
  changed, no direction chosen yet.
- 2026-10-08: **Split out** the "Streamlining"/"Configuration's own dead
  weight"/"Full Overview"/"Concrete schema changes" content to its own
  doc, [flatten-provider-resource-module-properties.md](flatten-provider-resource-module-properties.md)
  — grew into its own large, mostly-decided body of work unrelated to
  this doc's actual subject (the `FLAT_CATEGORIES` secret-taint gap).
  Content moved verbatim, nothing changed as part of the move. This doc
  keeps only the Mental model, Reference tables, Why this matters,
  Candidate directions, and Remaining Work sections — plus a new
  "How resolved values actually reach Terraform" background section
  (promoted out of a since-removed "Parked" tangent, moved up next to
  "Overview" since it directly explains this doc's own mechanism).
- 2026-10-08: Reviewed the one real v2 consumer repo
  (`cfg-int-deployment`) directly — no `${secret:}` tokens found anywhere
  inside any `properties`/`custom`/`configuration` block across every
  file checked; this consumer's own authoring convention already keeps
  secrets out of these fields entirely (dedicated `variables:`/`store:`
  entries instead). Low risk today for the one real tester, by
  convention, not because the gap isn't real.
- 2026-10-08: **Started the redesign** — chose and began designing
  per-document-entry taint tracking (generalizing `FLAT_CATEGORIES`'
  existing per-key rule to the single-variable-broadcast/claimed-
  broadcast categories' own per-document-entry structure), sequenced
  after the Provider/Resource/Module properties rename. Identified the
  real remaining blocker: the consuming `.tf` module needs its own new
  merge convention to reassemble a partial dict missing one pulled-out
  entry — not yet designed. See "Redesign" section above.
- 2026-10-09: Reopened the v1-parity framing ("Reconsidered" section) —
  v2 is alpha and this is a work doc, so matching v1's exact per-category
  variable shapes isn't a fixed constraint, just today's convention.
  Added per-category vs. universal-path-keyed as an explicit, undecided
  fork. Then added the "Clean sheet" appendix below for comparison.
- 2026-10-09: **Superseded.** The clean-sheet appendix was promoted into
  its own full design,
  [unified-terraform-projection.md](unified-terraform-projection.md) —
  one `strata` variable, one path-keyed `TF_VAR_strata_secrets` map,
  accepted as deliberately breaking (two consumers, both absorbable,
  alpha). That design deletes this doc's entire subject structurally
  instead of patching it, so everything from "Redesign" onward here is
  historical. The evidence sections above (gap analysis, reference
  tables, real-consumer review, mechanism walkthrough) remain accurate
  and are what the new design was built from.

## Appendix: clean-sheet design (what this would look like with no v1 history)

Written deliberately as a *comparison baseline only* — not a proposal,
not sequenced, not costed. Everything above is constrained by real v1
output shapes and real `.tf` code already written against them; this is
what the same requirements produce when those constraints are dropped
entirely. Useful mainly to see which of today's complexity is *essential*
versus *inherited*.

### The requirements, restated with nothing assumed

1. Strata resolves a graph of documents into values Terraform can consume.
2. Secrets must never touch disk — only a process env var.
3. A build artifact must be standalone-usable (`terraform plan` with no
   strata wrapper, for everything that isn't a secret).
4. `build` renders unresolved; `deploy` renders resolved. Same directory.
5. Terraform's own precedence: `*.auto.tfvars.json` > `TF_VAR_*`.

### Clean-sheet answer: two files, one env var, one convention

**`strata.auto.tfvars.json`** — *everything* non-secret, one file, one
top-level variable:

```json
{
  "strata": {
    "workspace":  { "name": "my-workspace", "environment": "prd", "...": "..." },
    "tenant":     { "code": "c0062", "name": "GSK", "...": "..." },
    "providers":  { "azure": {"type": "azurerm", "...": "..."},
                    "kamatera": {"type": "kamatera", "...": "..."} },
    "resources":  { "compute":    { "vm1": {"...": "..."} },
                    "networking": { "net1": {"...": "..."} } },
    "namespaces": { "hearth": {"...": "..."} },
    "dns":        { "example-zone": {"...": "..."} },
    "networks":   { "spoke-net": {"...": "..."} },
    "firewalls":  { "edge-fw": {"...": "..."} },
    "topologies": { "...": "..." },
    "properties": { "deploy_tier": "standard" },
    "custom":     { "cost_center": "1234" },
    "flags":      { "enable_x": true },
    "variables":  { "customer_code": "c0062" }
  }
}
```

**`TF_VAR_strata_secrets`** — the *only* env var, one flat map keyed by
dotted path into the structure above:

```
TF_VAR_strata_secrets={"providers.kamatera.configuration.api_token":"<resolved>","variables.db_password":"<resolved>"}
```

**One `.tf`-side convention**, declared once per root:

```hcl
variable "strata"         { type = any }
variable "strata_secrets" { type = map(string), default = {} }

locals {
  # one helper, used everywhere a secret might appear
  s = var.strata_secrets
}
# usage: lookup(local.s, "providers.kamatera.configuration.api_token",
#               var.strata.providers.kamatera.configuration.api_token)
```

### What this fixes, structurally rather than by patching

- **The entire secret-taint gap disappears.** Granularity is per-*leaf*
  by construction — a secret is one entry in one flat map, keyed by its
  own exact path. Nothing else is ever omitted, blanked, or dragged
  along. No `FLAT_CATEGORIES`-vs-broadcast split exists to be
  inconsistent about, because there's only one shape.
- **The env-var size risk mostly disappears too.** Only actual secrets
  ride the env var, never "a large structure that contains a secret" —
  so the channel is finally sized the way it was always assumed to be
  (a handful of short scalars).
- **One variable name to version, not 13+.** No `_REAL_VARIABLE_NAME`
  rename table (`providers` → `platform_providers`, `dns` →
  `dns_zones`, `tenant` → `strata_tenant`), no per-resource-type
  `resx_<type>` file explosion all declaring the same `resources`
  variable (the collision that forces today's unconditional-blanking
  special case), no wrapped-vs-flat-vs-passthrough three-shape
  convention to remember.
- **`properties`/`custom`'s merge-chain output is just another key**,
  not a special `FLAT_CATEGORIES` member with its own delivery rule.
- **Only one `.tf`-side convention to adopt**, once, per root — rather
  than N per-category `merge()` calls, or N category-specific `_secrets`
  variables.

### What it costs

- **Total break with every real consumer `.tf` root.** Every
  `var.platform_providers` / `var.resources` / `var.strata_tenant`
  reference becomes `var.strata.providers` / `var.strata.resources` /
  `var.strata.tenant`. Mechanical, but it's every file in every real
  root — the exact thing the current design exists to avoid.
- **`type = any` on the main variable** gives up Terraform's own
  per-variable type checking at the root boundary (today, a root can
  declare `variable "platform_providers" { type = map(object({...})) }`
  and have Terraform validate the shape for it). Recoverable by
  declaring a full nested `object({...})` type instead, at the cost of
  one large type declaration per root.
- **`lookup()`-by-dotted-path is less idiomatic HCL** than a plain
  `merge()` of two maps — readable, but noisier at every secret-bearing
  use site, and it puts the burden on the module author to use the
  helper *everywhere* a secret could appear (miss one, and that field
  silently reads the unresolved/absent value instead of failing loudly).
- **Loses v1-output parity entirely** — the migration path from a real
  v1 repo stops being "same variables, new tool" and becomes a rewrite.

### Honest comparison

The clean sheet is meaningfully simpler as a *mechanism* — it deletes the
category-shape taxonomy, the rename table, the `resx_<type>` collision
special case, and the entire taint-granularity problem this doc exists
about. It's worse as a *migration*: it trades a contained,
strata-internal fix for a breaking change in every consumer's Terraform
code.

The useful takeaway isn't "do this instead" — it's that **most of the
complexity this doc has been working around is inherited, not
essential**. If a future break is ever taken for other reasons (and v2
being alpha means that's not unthinkable), this is the shape worth
breaking *toward*, and the "universal, path-keyed `_secrets` map" option
from the "Reconsidered" section above is the halfway point: it adopts the
clean sheet's secret mechanism without touching the existing per-category
variable names at all.