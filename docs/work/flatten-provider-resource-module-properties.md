# Flatten Provider/Resource/Module's typed `properties` — Work

- Status: decided, not yet implemented — needs its own ADR before implementation
- Last updated: 2026-10-08
- Related: [flat-category-secret-taint-size-limit.md](flat-category-secret-taint-size-limit.md)
  (split out of that doc's "Streamlining" section — that doc's own subject
  is the unrelated secret-taint gap; this is a separate naming/schema
  consistency concern that grew large enough to need its own doc),
  [build-time-value-categories.md](../design/build-time-value-categories.md)
  (the design doc describing the current, working `properties`/
  `configuration`/`custom` mechanism this proposal changes the shape of)

## Streamlining: freeing up `properties` to mean the same thing everywhere

**The problem**: `properties` means two unrelated things depending on
which kind you're looking at.

- On **Workspace/Environment/Deployment/Tenant**: a free-form
  `dict[str, Any]`, merged across documents — "values that *drive*
  strata's own composition."
- On **Provider/Resource/Module**: a *typed*, required, strata-interpreted
  model (`ProviderPropertiesModel`/`ResourcePropertiesModel`/
  `ModulePropertiesModel`) — the kind's own core, defining attributes
  (`type`/`region` for Provider, `provider_type`/`resource_type` for
  Resource — the field `_build_resources_payload()` groups
  `resx_<type>.auto.tfvars.json` by). These are **not** free-form, **not**
  merged, and **not** the same concept as the first group at all — they
  just happen to share the word "properties."

This is exactly what caused the confusion this doc started from: expecting
one consistent meaning for `properties` across every kind, when there are
actually two unrelated concepts sharing one name.

### Decided (2026-10-08): no wrapper, no rename — flatten, and make `properties` a uniform free-form field everywhere

Before inventing a new word, worth checking what every *other* kind
already does: DNS/Network/Firewall never nest their own core fields under
a `properties` sub-model at all — `DnsSpecModel.provider`/`.zones`,
`NetworkSpecModel.networks`, `FirewallSpecModel.deny`/`.allow`/`.defaults`
all sit **directly on `spec`**, no wrapper. Provider/Resource/Module are
the odd ones out, nesting their equivalent core fields under a sub-model
that happens to be named `properties`.

So the chosen fix needs no new vocabulary at all: **inline
`ProviderPropertiesModel`'s/`ResourcePropertiesModel`'s/
`ModulePropertiesModel`'s fields directly onto their own `SpecModel`**
(`ProviderSpecModel.type`/`.region` instead of
`ProviderSpecModel.properties.type`/`.region`, etc.) — matching the
convention every other kind already follows. Once that's done,
`properties` is free to mean the same free-form, mergeable thing on
every kind that wants it, with zero naming collision anywhere.

**Decided second part, superseded (2026-10-08) — see "Amendment" below**: ~~once vacated, add `properties: dict[str, Any] | None` ... to `ProviderSpecModel`/`ResourceSpecModel`/`ModuleSpecModel`~~. Caught a real inconsistency while reviewing Module's flattened shape: re-adding `properties` to these 3 kinds would be purely a naming-consistency move with **no functional difference from `custom`** on any of them (both flat, non-merging, per-document passthrough — Provider/Resource/Module don't participate in the merge chain either). That's the *exact same redundancy* already used to reject adding `properties` to Namespace/DNS/Network/Firewall/Integration two paragraphs below. Applying that rule consistently means Provider/Resource/Module shouldn't get it back either — see the Amendment section after "Cost of this change" for the corrected final shape (flatten only, no new field).

Extending `properties` further, to the kinds that don't have it at all
today (DNS/Network/Firewall/Namespace/Integration), is a separate,
not-yet-raised question — those kinds already have `configuration`+
`custom` covering the same free-form-passthrough need, so adding a third,
redundant field there would need its own justification. Out of scope for
this decision, which is specifically about resolving the Provider/
Resource/Module collision.

#### First level of `spec`, before and after, for all three

Every other field on these three specs stays exactly where it is — only
`properties`'s own children move up to sit beside them, one level higher.

**`provider.spec`** — before: `lifecycle`, `properties` (nested: `type`,
`region`, `display_name`), `authentication`, `configuration`, `custom`,
`default_tags`. After:

```
provider.spec
├── lifecycle
├── type              ← was properties.type
├── region            ← was properties.region
├── display_name      ← was properties.display_name
├── authentication
├── configuration
├── custom
└── default_tags
```

**`resource.spec`** — before: `lifecycle`, `properties` (nested:
`provider_type`, `resource_type`, `unit_cost`, `category`, `subcategory`),
`dependencies`, `storage`, `configuration`, `custom`, `default_tags`,
`custom_tags`. After:

```
resource.spec
├── lifecycle
├── provider_type     ← was properties.provider_type
├── resource_type     ← was properties.resource_type
├── unit_cost         ← was properties.unit_cost
├── category          ← was properties.category
├── subcategory       ← was properties.subcategory
├── dependencies
├── storage
├── configuration
├── custom
├── default_tags
└── custom_tags
```

**`module.spec`** — before: `source`, `type`, `lifecycle`, `properties`
(nested: `mounts`, `checks`, `endpoints`), `configuration`, `custom`,
`default_labels`, `custom_labels`. After:

```
module.spec
├── source
├── type
├── lifecycle
├── mounts            ← was properties.mounts
├── checks            ← was properties.checks
├── endpoints         ← was properties.endpoints
├── configuration
├── custom
├── default_labels
└── custom_labels
```

**One real naming collision this surfaces**: `module.spec` already has
its own top-level `type` field (the deployer tool — `helm`/`compose`/
`argocd`), unrelated to `provider.spec.properties.type` (the cloud
provider). Flattening Provider's `properties.type` up to `provider.spec.
type` is fine in isolation (Module and Provider are different kinds, no
cross-kind clash) — flagged here only because it's the kind of thing
worth double-checking per kind, not assuming safe by analogy.

### Rejected: a nested sub-model under a new name

**Decision (2026-10-08): rejected** — no wrapper for anyone, see above.
Kept for the record, since the naming-collision reasoning (particularly
the `values` rejection) is worth preserving.

If flattening onto `spec` directly wasn't desired for some reason (e.g.
wanting the core fields visually grouped in YAML), the sub-model would
need a new name — **not** `values`. Checked against this codebase's own existing
vocabulary first: "Value"/"Value binding" is already a heavily-used,
precisely-defined term (ADR-0002) for the `${var:}`/`${secret:}`/
`${feature:}` token mechanism (`ValueResolution`, `resolve_values()`,
`Value binding` throughout the ADRs) — a field named `spec.values` holding
something completely unrelated (a kind's own typed core attributes) would
collide with that established meaning and reintroduce exactly the kind of
confusion this section exists to remove, just under a new name.

Better candidates, checked for collision against this repo's own existing
vocabulary (nothing else currently uses these):

- **`spec.definition`** — "the type-specific definition of this
  document." Clear, unused elsewhere, reads naturally
  (`provider.spec.definition.type`).
- **`spec.attributes`** — common in other IaC/resource-modeling systems
  for exactly this ("the resource's own defining attributes"). Also
  unused elsewhere in this schema.
- `spec.identity` — considered and rejected: `meta.name` already owns
  "identity" in this schema's own vocabulary; reusing the word for a
  different concept recreates the exact ambiguity this section is trying
  to resolve.

Moot now that flattening was chosen over nesting — no wrapper name is
needed either way.

### Resolved fork: a universal `spec.attributes` convention, or just fix the three outliers?

**Decision (2026-10-08): Option 2** — see below. Kept for the record.

Picking a wrapper name (above) only matters once it's decided *whether*
every kind should carry it — not just Provider/Resource/Module. Checked
against the reference table already built earlier in this doc: **8 kinds
already put their core fields directly on `spec`, with no wrapper at
all** — DNS, Network, Firewall, Namespace, Workspace (its merge-dict
`properties` is the *other* concept, not a core-fields wrapper),
Environment, Deployment, Tenant, Integration. **Only 3 — Provider,
Resource, Module — currently wrap.** The wrapped shape is the minority
pattern, not the majority one.

That framing makes this an actual two-option fork, not a free choice:

- **Option 1 — universal wrapper.** Introduce `spec.attributes` (or
  whichever name) as the standard parent for every kind's own "loose"
  core fields, and retrofit the 8 already-flat kinds to match (e.g.
  `DnsSpecModel.attributes.provider`/`.zones` instead of today's
  `DnsSpecModel.provider`/`.zones` directly). Buys real uniformity —
  every kind's spec would read `lifecycle` / `attributes` /
  `configuration` / `custom` / kind-specific extras, in that shape, with
  no exceptions.
- **Option 2 — just fix the three outliers.** Flatten Provider/Resource/
  Module to match the other 8 (this doc's existing recommendation);
  leave DNS/Network/Firewall/etc. exactly as they are. No new wrapper
  anywhere, for any kind.

**Decided: Option 2.** Three reasons, not just one:

1. **Scale of the break.** Option 1 touches every kind's schema and every
   existing document in `config/` (plus any real consumer repo) for
   fields that have no naming problem today — DNS/Network/Firewall don't
   have a `properties`-style collision to fix, so retrofitting them has
   zero functional payoff, only a uniformity one.
2. **The "core fields" shape isn't uniform across kinds anyway.**
   Provider/Resource/Module's core fields are flat scalars (`type`,
   `region`, `provider_type`, `resource_type`...) — a wrapper groups them
   cleanly. DNS/Network/Firewall's core fields are themselves lists of
   structured sub-models (`zones`, `networks`, `deny`/`allow`) — wrapping
   `NetworkSpecModel.networks` as `NetworkSpecModel.attributes.networks`
   adds a layer without actually clarifying anything, since there's only
   ever the one field under it.
3. **The problem this section exists to solve is narrower than "every
   kind should look identical."** The actual bug is `properties` meaning
   two unrelated things on different kinds — fixed the moment
   Provider/Resource/Module stop using that name for their core fields.
   Universal consistency is a nice-to-have, not something this gap
   requires.

If this is accepted, the wrapper-naming question two sections up becomes
moot — flattening (no wrapper, no new name) wins over both candidate
names.

### Once unblocked: `configuration`/`custom` need no further change

They're already consistent today in the one way that matters — both are
already plain, unvalidated `dict[str, Any] | None` on every kind that has
them (confirmed in the earlier reference table), never typed models.
The only inconsistency is which kinds *participate in the merge chain*
(Workspace/Environment/Deployment/Tenant) versus which are flat
per-document passthrough (everyone else) — that's a deliberate,
already-reasoned architectural split (ADR-0011-style: Workspace's chain
is for deployment-varying composition; per-document passthrough is for
values that belong to one specific IaC/app resource and never vary by
deployment), not an accidental inconsistency to "fix." Only `properties`
needs the rename/inline above — `configuration`/`custom` are already
uniform in shape, just intentionally different in merge behavior by kind.

### Cost of this change, if pursued

A real breaking schema change, not a free refactor: every existing
document using `provider.spec.properties.type`/
`resource.spec.properties.resource_type`/etc. would need rewriting to the
flattened path — including this repo's own `config/` dogfooding solution,
and any real consumer repo. Still needs its own ADR before implementation
(the naming/shape decision is made; the ADR documents it and the
migration) — not scheduled here, captured for when this is picked up.

### Amendment (2026-10-08): drop the new free-form `properties` from Provider/Resource/Module too

Found by reviewing Module's flattened shape side-by-side with Namespace's
(both feed the identical Helm/Compose workload pipeline, both are
non-merging kinds): after flattening frees up the `properties` name on
Provider/Resource/Module, the "Decided second part" above said to
immediately refill it with a new free-form `dict[str, Any] | None` — but
that field would behave **exactly like `custom` already does** on these 3
kinds (flat, per-document, non-merging passthrough). No functional gap it
fills that `custom` doesn't already fill.

That's precisely the redundancy argument this doc already used to reject
adding `properties` to Namespace/DNS/Network/Firewall/Integration. Keeping
it only on Provider/Resource/Module — just because they happened to have
the *name* `properties` before (for something unrelated) — isn't a
principled reason to treat them differently from Namespace, which sits in
the exact same "non-merging, `configuration`+`custom` already covers it"
bucket. Nothing of value is lost by not refilling it either: the flattened
core fields (`type`/`region`/etc.) keep their own names and meaning
regardless; only the *wrapper* and its reused name go away.

**Corrected final decision**: flatten Provider/Resource/Module's typed
core fields onto `spec` as already described above, and stop there — do
**not** add a new `properties` field back to any of the three. Final
shape matches Namespace/DNS/Network/Firewall/Integration exactly:
`configuration` + `custom` only, no `properties`, on every kind that isn't
part of the Workspace/Environment/Deployment/Tenant merge chain. This is
now the one consistent rule across all 18 kinds: **`properties` exists
only on kinds that actually merge it** (Workspace/Environment/Deployment/
Tenant) — everyone else gets `configuration`+`custom` and nothing else.
The "First level of `spec`, before and after" trees and the "Full
Overview" entries below are updated to drop the now-reversed `properties`
row for Provider/Resource/Module.

## Configuration's own `properties`/`configuration`/`custom`: likely dead weight, not a system-wide values channel

A second, separate finding — raised as a question, worth recording even
though no decision has been made yet.

`ConfigurationSpecModel` carries all three fields (`properties`,
`configuration`, `custom` — see its own list in "Full Overview" below),
but the reference table in
[flat-category-secret-taint-size-limit.md](flat-category-secret-taint-size-limit.md)
already states what source confirms: **zero references in
`build_controller.py`'s payload functions** — these three fields are
validated and stored, never read by anything. Unlike Provider/Resource/
Module's `properties` (a real naming collision with a genuine, consumed
field), Configuration's version looks like it was added on the assumption
that `kind: configuration` — being the one document every workspace in
the solution implicitly shares — would be a natural place to park values
meant to reach "the entire system."

That assumption looks wrong once compared against the mechanism that
*actually* does this job today, and does it well: an **Environment**
document. Nothing stops the same Environment being referenced from every
Tenant's (or Deployment's) `environments` list solution-wide — that's
exactly what `variables`/`secrets`/`features` stores plus `properties`/
`custom` on `EnvironmentSpecModel` are for, and that mechanism is real,
wired, and merges in a well-defined order (Tenant → Environment →
Deployment). A solution-wide "globals" file is just a convention
(reference the same Environment name everywhere that needs it), not a new
field anywhere — Configuration doesn't need to grow its own competing
copy of the same idea.

**Working hypothesis, not yet decided**: drop `properties`/
`configuration`/`custom` from `ConfigurationSpecModel` entirely, rather
than flattening or renaming them like Provider/Resource/Module — there's
no naming collision to fix here, just three fields with no consumer and
no concrete plan to ever give them one, duplicating a need a shared
Environment document already meets better. Checked this repo's own
dogfooding `config/configuration.yaml` — it sets none of the three
either, consistent with nothing depending on them yet.

## Full Overview

Every field on every kind's `spec`, one list per kind, in `PlatformKind`
enum order — `field: type -> short purpose`. Built directly from each
model file, not from memory.

### SolutionSpecModel

  configuration: str                           -> path to the Configuration document(s) = ok
  discovery: SolutionDiscoveryModel | None     -> extra discovery exclude patterns = ok
  remotes: list[SolutionRemoteModel] | None    -> named remote artifact sources ("@name/path") = ok
  description: str | None                      -> documentation only = ok

### ConfigurationSpecModel

  properties: dict[str, Any] | None            -> free-form, not consumed anywhere yet = remove
  configuration: dict[str, Any] | None         -> free-form, not consumed anywhere yet = remove
  custom: dict[str, Any] | None                -> free-form, not consumed anywhere yet = remove

  providers: list[PlatformName] | None         -> provider type registry (ProviderConfig names) = ok
  additional_topologies: bool                  -> allow topology types not in spec.topologies = ok
  topologies: list[PlatformName] | None        -> topology type registry (TopologyConfig names) = ok
  security: ConfigurationSecurityModel | None  -> allow-lists for variable/secret/feature store types = ok
  paths: list[PathConventionModel] | None      -> directory-family naming conventions = ok
  audit: AuditConfigModel | None               -> deployment audit trail config (manifest/metrics/SIEM) = ok
  cve_allowed: list[CveAllowedEntryModel] | None -> solution-wide CVE suppression list = ok

### ProviderConfigSpecModel

  description: str                                  -> description of the provider type = ok
  version: str | None                               -> provider version constraint (e.g. ~>3.0) = ok
  additional_regions: bool                          -> allow regions not listed below = ok
  regions: list[ProviderConfigRegionModel] | None   -> supported regions registry = ok
  additional_resources: bool                        -> allow resource types not listed below = ok
  resources: list[ProviderConfigResourceModel] | None -> supported resource types registry = ok

### TopologyConfigSpecModel

  description: str | None                              -> description of the topology type = ok
  additional_components: bool                          -> allow component roles not listed below = ok
  components: list[TopologyConfigComponentModel] | None -> expected component roles registry = ok

### ProviderSpecModel (flattened shape — decided above, not yet implemented; current code still nests `type`/`region`/`display_name` under `properties: ProviderPropertiesModel`)

  lifecycle: CommonLifecycleModel | None       -> IaC workflow lifecycle phases = ok
  type: str                                    -> cloud/infra provider (was properties.type) = ok
  region: str                                  -> primary region/datacenter (was properties.region) = ok
  display_name: str | None                     -> optional human-readable region label (was properties.display_name) = ok
  authentication: AuthenticationModel | None   -> cloud provider access credentials = ok
  configuration: dict[str, Any] | None         -> raw provisioner-block passthrough (e.g. partner_id) = ok for instance-level configuration
  custom: dict[str, Any] | None                -> bookkeeping/automation data, not consumed by provisioner = ok for instance-level bookkeeping
  default_tags: dict[str, str] | None          -> baseline cloud tags for resources under this provider = ok

No `properties` field (reconsidered, see "Amendment" above) — would have been
redundant with `custom` on this non-merging kind, same as Namespace/DNS/
Network/Firewall/Integration.

### ResourceSpecModel (flattened shape — decided above, not yet implemented; current code still nests `provider_type`/`resource_type`/`unit_cost`/`category`/`subcategory` under `properties: ResourcePropertiesModel`)

  lifecycle: CommonLifecycleModel | None            -> IaC workflow lifecycle phases = ok
  provider_type: PlatformName                       -> cloud/infra provider (was properties.provider_type) = ok
  resource_type: str                                -> type of the resource (was properties.resource_type) = ok
  unit_cost: float | None                            -> unit cost for the resource (was properties.unit_cost) = ok
  category: str | None                               -> resource category (was properties.category) = ok
  subcategory: str | None                            -> resource subcategory (was properties.subcategory) = ok
  dependencies: list[ResourceDependencyModel] | None -> resource dependency list = ok
  storage: ResourceStorageModel | None               -> VM-specific storage configuration = ok
  configuration: dict[str, Any] | None               -> raw passthrough, schema-checked against the
                                                        referenced ProviderConfig's resource schema = ok for instance-level configuration
  custom: dict[str, Any] | None                      -> user-defined data, not part of that schema check = ok for instance-level bookkeeping
  default_tags: dict[str, str] | None                -> baseline cloud tags for this resource = ok
  custom_tags: dict[str, str] | None                 -> additional ad-hoc cloud tags = ok

No `properties` field either (reconsidered, see "Amendment" above) — same
redundancy-with-`custom` reasoning as Provider.

### DnsSpecModel

  provider: str | None            -> DNS provider name (e.g. inwx, cloudflare, route53) = ok
  zones: list[DnsZoneModel]       -> DNS zones (required, at least one) = ok

No `properties`/`configuration`/`custom` at this level — by design, confirmed
in `DnsZoneModel`'s own docstring: "Each zone is a real, independently-tagged
cloud resource (e.g. an Azure DNS Zone or Route53 Hosted Zone) —
configuration/custom/tags live here, not on `DnsSpecModel`, since a single
file can declare multiple zones." `configuration`/`custom`/`default_tags`/
`custom_tags` are per-zone fields on `DnsZoneModel` instead. Same reasoning
applies to Network (per-network) and Firewall (per-definition) — any kind
whose one document can declare several independent cloud resources pushes
these fields down to the per-item model, not the top-level `spec`.

### NetworkSpecModel

  networks: list[NetworkDefinitionModel] -> network definitions (required, at least one) = ok

  `configuration`/`custom`/`default_tags`/`custom_tags` are per-network (inside
  `NetworkDefinitionModel`), not at this top level — same per-item reasoning as
  DNS, see above. Also like DNS: no top-level `properties` free-form merge dict
  either — consistent with the "Streamlining" section's note that extending
  `properties` to DNS/Network/Firewall/Namespace/Integration is a separate,
  out-of-scope question, not something missing by accident.

### FirewallSpecModel

  reset: bool | None                           -> reset all existing rules before applying these = ok
  defaults: list[FirewallDefaultsModel] | None -> baseline allow/deny rule per direction = ok
  deny: list[FirewallRuleModel] | None         -> explicit deny rules = ok
  allow: list[FirewallRuleModel] | None        -> explicit allow rules = ok
  configuration: dict[str, Any] | None         -> raw provisioner passthrough (e.g. NSG arguments) = ok
  custom: dict[str, Any] | None                -> user-defined data = ok
  default_tags: dict[str, str] | None          -> baseline cloud tags for this firewall/NSG = ok
  custom_tags: dict[str, str] | None           -> additional ad-hoc cloud tags = ok

  No top-level `properties` free-form merge dict either, same as DNS/Network —
  out of scope per the "Streamlining" section above, not a gap.

### ModuleSpecModel (flattened shape — decided above, not yet implemented; current code still nests `mounts`/`checks`/`endpoints` under `properties: ModulePropertiesModel`)

  source: SourceModel                           -> module deployment source configuration = ok
  type: PlatformName | None                     -> deployer tool (helm, compose, argocd, script...) = ok
  lifecycle: CommonLifecycleModel | None        -> module-specific lifecycle hooks = ok
  mounts: list[ModuleMountModel] | None         -> module mount configurations (was properties.mounts) = ok
  checks: list[ModuleCheckModel] | None         -> module health check configurations (was properties.checks) = ok
  endpoints: list[ModuleEndpointModel] | None   -> module endpoint configurations (was properties.endpoints) = ok
  configuration: dict[str, Any] | None          -> module-specific configuration data = ok for instance-level configuration
  custom: dict[str, Any] | None                 -> user-defined data, rides inside the module's own build output = ok for instance-level bookkeeping
  default_labels: dict[str, str] | None         -> baseline k8s/compose workload labels = ok
  custom_labels: dict[str, str] | None          -> additional ad-hoc workload labels = ok
  services: list[ModuleServiceModel] | None     -> multi-container/sub-chart service definitions = ok
  compose_file: str | None                      -> external docker-compose.yml passthrough path = ok
  files: list[ModuleFileModel] | None           -> extra files copied verbatim into build output = ok
  release_name: str | None                      -> Helm release / ArgoCD Application name = ok
  kubernetes_namespace: str | None              -> k8s namespace to deploy this module into = ok

No `properties` field either (reconsidered, see "Amendment" above) — this is
what makes Module's shape finally match Namespace's exactly: both are
non-merging, workload-pipeline kinds, both get `configuration`+`custom` and
nothing else.

### NamespaceSpecModel

  type: NamespaceType                       -> dedicated (default) vs shared cross-layer overlap rule = ok
  lifecycle: CommonLifecycleModel | None    -> namespace lifecycle phases = ok
  modules: list[ModuleReferenceModel] | None -> modules placed in this namespace = ok
  configuration: dict[str, Any] | None      -> raw passthrough (e.g. k8s annotations/quotas) = ok
  custom: dict[str, Any] | None              -> user-defined data = ok
  default_labels: dict[str, str] | None     -> baseline k8s Namespace object labels = ok
  custom_labels: dict[str, str] | None      -> additional ad-hoc namespace labels = ok

### WorkspaceSpecModel

  lifecycle: CommonLifecycleModel | None        -> workspace lifecycle phases = ok
  properties: dict[str, Any] | None             -> merges across Workspace -> Environment -> Deployment = ok
  configuration: dict[str, Any] | None          -> this workspace's own passthrough, NOT merged = ok
  custom: dict[str, Any] | None                 -> ALSO merges across the chain, unlike configuration = ok
  default_tags: dict[str, str] | None           -> solution-wide baseline cloud tags = ok
  providers: list[PlatformName]                 -> Provider document names (required, at least one) = ok
  provisioners: list[ProvisionerModel]          -> tool definitions (required, at least one) = ok
  execution: list[ProvisioningStepModel] | None -> the ordered build/deploy recipe = ok
  topology: list[TopologySpecModel] | None      -> inline grouping (type, components, namespaces, volumes) = ok
  resources: list[WorkspaceResourceModel] | None -> workspace resource definitions = ok
  namespaces: list[PlatformName] | None         -> Namespace document names = ok
  firewalls: list[PlatformName] | None          -> Firewall document names = ok
  dns_zones: list[PlatformName] | None          -> DNS document names = ok
  networks: list[PlatformName] | None           -> Network document names = ok
  cve_policy: CveMaxSeverityPolicyModel | None  -> gates `build run` on CVE findings in the SBOM = ok
  checkov_policy: CheckovPolicyModel | None     -> gates `build run` on Checkov static-IaC findings = ok

### IntegrationSpecModel = ok

  type: PlatformName                               -> tool/service identity (terraform, vault, git...) = ok
  capabilities: set[str]                           -> what this integration provides (core + "x-" extensions) = ok
  description: str | None                          -> documentation only = ok
  required: bool                                   -> required for platform operation = ok
  enabled: bool                                    -> whether this integration is active = ok
  authentication: AuthenticationModel | None       -> auth config for reaching the integration = ok
  transport: str | None                            -> how it's reached (cli, http, sdk) = ok
  version: str | None                               -> expected tool/service version constraint, asserted at preflight = ok
  command: str | None                              -> override the default executable name = ok
  endpoints: IntegrationEndpointsModel | None      -> service endpoint address (networked transports only) = ok
  lifecycle: IntegrationLifecycleModel | None      -> hook scripts (e.g. connect_before, teardown) = ok
  configuration: dict[str, Any] | None             -> raw passthrough (e.g. SDK-specific setup) = ok
  custom: dict[str, Any] | None                    -> user-defined data = ok

### TenantSpecModel

  display_name: str                            -> human-readable tenant name = ok
  geographies: list[str] | None                 -> allowed data-residency boundaries = ok
  onboarded: date | None                        -> onboarding date, informational only = ok
  environments: list[PlatformName] | None       -> Environment documents merged in before a deployment's own = ok
  properties: dict[str, Any] | None             -> base layer merged into every referencing deployment's properties = ok
  configuration: dict[str, Any] | None          -> tenant-specific settings, NOT merged into deployment = ok
  custom: dict[str, Any] | None                 -> base layer merged into every referencing deployment's custom = ok

All three are used, but via two separate, non-overlapping mechanisms
(confirmed in `_build_tenant_payload()`): `configuration` is read directly
into the `tenant` category -> `TF_VAR_strata_tenant` (that function doesn't
touch `properties`/`custom` at all); `properties`/`custom` instead fold in
separately, as a base layer onto the deployment's own `properties`/`custom`,
in `deployment_resolution.py`, before the merge chain runs.

### EnvironmentSpecModel

  properties: dict[str, Any] | None         -> merged layer into deployments using this environment = ok
  custom: dict[str, Any] | None              -> ALSO merges, same rule as properties = ok
  variables: list[VariableStoreModel] | None -> what "${var:KEY}" tokens resolve against = ok
  secrets: list[SecretStoreModel] | None     -> what "${secret:KEY}" tokens resolve against = ok
  features: list[FeatureStoreModel] | None   -> what "${feature:KEY}" tokens resolve against   = ok

### DeploymentSpecModel

  partial: bool                                 -> reusable base only, not deployable on its own = ok
  extends: PlatformName | None                   -> base Deployment merged in before validation = ok
  workspace: PlatformName | None                -> Workspace whose provisioning recipe this runs = ok
  environments: list[PlatformName] | None       -> Environment documents merged in order, later wins = ok
  tenant: PlatformName | None                    -> owning Tenant = ok
  configurations: list[PlatformName] | None     -> additional Configuration documents = ok
  version: PlatformName | None                   -> Version document supplying pins = ok
  layers: DeploymentLayersModel | None           -> hierarchy position (inert in v2) = ok
  locking: DeploymentLockingModel | None         -> pipeline-level state locking = ok
  stages: list[DeploymentStageModel] | None     -> runtime params per workspace provisioning step = ok
  lifecycle: CommonLifecycleModel | None        -> deployment lifecycle phases = ok
  properties: dict[str, Any] | None              -> last merge layer, over tenant + environment properties  = ok
  custom: dict[str, Any] | None                  -> last merge layer, same rule as properties = ok

### VersionSpecModel

  workspace: PlatformName | None               -> Workspace this version document applies to = ok
  promotion: VersionPromotionModel | None      -> ring/order/wave rollout-visibility tags = ok
  pins: VersionPinsModel                       -> version pins grouped by target category = ok
  description: str | None                      -> documentation only = ok

### ArtifactSpecModel

  image_name: str                              -> container image path, the pinnable identity = ok
  image_tag: str | None                        -> image tag/digest, overridden by kind: version's pins = ok
  registry: str | None                         -> registry host, documentation only = ok
  integration: PlatformName | None             -> Integration providing registry credentials = ok
  description: str | None                      -> documentation only = ok
  properties: dict[str, Any]    -> merged properties = ok
  configuration: dict[str, Any] -> item configuration for intended tooling = ok
  custom: dict[str, Any]        -> user-defined custom data for scripts or extensions = ok

## Concrete schema changes needed, per kind (derived from "Full Overview"'s `= ok`/`= remove` markers)

Everything not listed here is `= ok` in the "Full Overview" section above —
no schema change needed for Solution/ProviderConfig/TopologyConfig/DNS/
Network/Firewall/Namespace/Workspace/Integration/Tenant/Environment/
Deployment/Version/Artifact. Only 4 kinds have a real, decided change
pending implementation:

1. **`kind: configuration`** (`ConfigurationSpecModel`) — **remove**
   `properties`, `configuration`, `custom` entirely (all 3 marked
   `= remove`). Confirmed dead: zero references in `build_controller.py`'s
   payload functions, and `config/configuration.yaml` sets none of them
   either. See "Configuration's own `properties`/`configuration`/
   `custom`" section above.

2. **`provider`** (`ProviderSpecModel`) — **remove** the nested
   `properties: ProviderPropertiesModel` wrapper; **flatten** its 3
   children directly onto `spec`: `type`, `region`, `display_name`. Do
   **not** add a new free-form `properties` field back (reversed per the
   "Amendment" section — would be redundant with `custom`).

3. **`resource`** (`ResourceSpecModel`) — **remove** the nested
   `properties: ResourcePropertiesModel` wrapper; **flatten** its 5
   children directly onto `spec`: `provider_type`, `resource_type`,
   `unit_cost`, `category`, `subcategory`. Same "no new `properties`"
   rule as Provider.

4. **`module`** (`ModuleSpecModel`) — **remove** the nested
   `properties: ModulePropertiesModel` wrapper; **flatten** its 3
   children directly onto `spec`: `mounts`, `checks`, `endpoints`. Same
   "no new `properties`" rule as Provider/Resource.

All 4 are real breaking schema changes (every existing document using the
old nested path needs rewriting — see "Cost of this change" above) and
still need their own ADR before implementation. None of the 4 are
scheduled yet.

**Note on the Terraform-facing output shape**: confirmed directly in
`_build_providers_payload()`/`_build_resources_payload()` — both already
read `spec.properties.type`/`.region`/etc. and produce an output payload
that's *already* flat (`{type, region, display_name, ...}`). Flattening
only moves where the *source* field lives on the Pydantic model
(`spec.properties.type` → `spec.type`); the Terraform-facing category,
shape, and `TF_VAR_` name for `provider`/`resource` are unaffected either
way. Module was already not-Terraform-at-all, so its flatten doesn't
touch the build pipeline's Terraform categories either.

## Remaining Work / Open Questions

- Pick between `definition`/`attributes` if a nested sub-model is ever
  reconsidered instead of flattening — currently moot (flattening won).
- Configuration's own "drop `properties`/`configuration`/`custom`
  entirely" direction is a working hypothesis, not yet a final decision
  the way Provider/Resource/Module's flatten is.
- All 4 changes need their own ADR before implementation (breaking schema
  change, real migration cost for `config/` and any real consumer repo).

## Changelog

- 2026-10-08: Split out of
  [flat-category-secret-taint-size-limit.md](flat-category-secret-taint-size-limit.md)
  — that doc's own subject is the unrelated `FLAT_CATEGORIES` secret-taint
  gap; this streamlining proposal had grown into its own large, mostly
  decided body of work and didn't belong mixed into that doc anymore. All
  content (Streamlining, Configuration's dead-weight finding, Full
  Overview, Concrete schema changes) moved verbatim, no content changed as
  part of the move.
- 2026-10-08: Recommends inlining Provider/Resource/Module's typed
  `properties` sub-model fields directly onto their own `SpecModel`
  (matching every other kind's existing convention) rather than inventing
  a new name; if a nested sub-model is still wanted, rejected `values` as
  a name (collides with this codebase's own established "Value
  binding"/`${var:}` vocabulary, ADR-0002) in favor of
  `definition`/`attributes`.
- 2026-10-08: **Decided** the streamlining direction — no wrapper, for
  anyone (rejected both a universal `spec.attributes` convention and a
  Provider/Resource/Module-only nested rename). Flatten
  `ProviderPropertiesModel`/`ResourcePropertiesModel`/
  `ModulePropertiesModel`'s fields directly onto their own `spec`.
- 2026-10-08: **Amended** the above — while reviewing Module's flattened
  shape against Namespace's, caught that re-adding `properties` to
  Provider/Resource/Module would be purely cosmetic: functionally
  identical to `custom` on these non-merging kinds, the exact redundancy
  already used to reject adding `properties` to Namespace/DNS/Network/
  Firewall/Integration. Corrected final rule: flatten the typed core
  fields, but do **not** re-add `properties` anywhere it wasn't already
  part of the merge chain — `properties` now exists only on Workspace/
  Environment/Deployment/Tenant, full stop. No code changed yet.
