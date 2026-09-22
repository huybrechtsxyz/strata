# ProviderConfig / TopologyConfig — Promoting Configuration's Registries to Standalone Kinds

- Status: implemented
- Date: 2026-09-21
- Related: [ADR-0011](0011-topology-and-provisioning-decoupling.md) (Topology's own
  promotion out of Workspace — same reasoning applied here one level up),
  [ADR-0013](0013-configuration-topology-registry.md) (introduced the topology
  registry this ADR promotes out of `Configuration`), the `Integration` kind
  (`integration_model.py`, promoted the same session, for the same reason),
  [ADR-0015](0015-solution-manifest-and-document-discovery.md)
  (**supersedes decision 4 below**: the `{name, file, description}` pointers
  became plain `list[PlatformName]` once documents are found by discovery —
  the promotion to standalone kinds itself stands unchanged)

## Context and Problem Statement

`ConfigurationSpecModel.providers`/`.topologies` were embedded lists of full
registry entries (`ConfigurationProviderModel`/`ConfigurationTopologyModel`).
v1's real `ConfigurationModel` goes much further — crams `providers`,
`topologies`, `integrations`, `security`, `zones`, `policies`, `audit`,
`cost`, `drift`, `change_tracking`, `promotions`, `paths`, `logging` into
**one file**. Discussed this shape with the user through a devops-architect
lens: one growing monolithic configuration file causes real, concrete
problems — merge conflicts between unrelated teams editing the same file,
no per-concern ownership/RBAC, noisy PR reviews, no reuse of one entry
without checking out the whole file. This is the same problem `Topology`
solved by being promoted out of `Workspace` (ADR-0011) and the reasoning
`Integration` was built with directly (this session, mirrored Topology's
promotion instead of embedding it in `Configuration` the way v1 did).

## Decision

Promote both registries to standalone kinds, same shape as everything else
`Workspace`/`Configuration` reference by name+file:

1. **`ProviderConfigModel`** (kind `providerconfig`) replaces the embedded
   `ConfigurationProviderModel`. `meta.name` is the provider type (e.g.
   `kamatera`, `azure` — was `ConfigurationProviderModel.name`). `spec`
   carries `description`, `version`, `additional_regions`, `regions`,
   `additional_resources`, `resources` — unchanged content, just moved into
   a real document's `spec`. Nested `ConfigurationProviderResourceModel`/
   `ConfigurationSchemaField` renamed to `ProviderConfigResourceModel`/
   `ProviderConfigSchemaField` for consistency.
2. **`TopologyConfigModel`** (kind `topologyconfig`) replaces the embedded
   `ConfigurationTopologyModel`. `meta.name` is the topology type (e.g.
   `kubernetes` — was `ConfigurationTopologyModel.type`). `spec` carries
   `description`, `additional_components`, `components` — unchanged content.
3. **Naming**: `Config` suffix (not `Class`, despite Kubernetes'
   `StorageClass`/`PriorityClass` being the closer semantic precedent for
   "an admin-authored policy profile referenced by name") — judged most
   obvious to a general audience over a less-familiar Kubernetes-flavored
   term. Accepted risk: Crossplane's own `ProviderConfig` means something
   different there (an *instance's* credentials, closer to this codebase's
   existing `Provider` kind) — a deliberate, documented naming collision,
   not an oversight.
4. **`ConfigurationSpecModel.providers`/`.topologies` become thin
   `{name, file, description}` pointers** — identical shape to
   `WorkspaceProviderModel`/`WorkspaceTopologyModel`. `validate_unique_topology_types`
   renamed to `validate_unique_topology_names` (checks `.name`, not `.type`,
   now that the pointer's key is a plain reference name like every other
   pointer model).
5. **Two new thin services** (`ProviderConfigService`/`TopologyConfigService`),
   Phase 1 only — a registry entry is entirely self-contained, same as
   `TopologyService`.
6. **Downstream consumers updated to the two-tier "existence now, deep
   check once loaded" pattern** already established for
   `WorkspaceService.validate_topology_components()`:
   - `ProviderService._validate_dynamic()`: now only checks that
     `spec.properties.type` is a *registered pointer name* in
     `configuration_model.spec.providers` (self-contained, still fits the
     base class's fixed hook). The real region check moved to a new public
     method, `validate_against_provider_config(provider_config: ProviderConfigModel)`
     — the caller resolves the pointer's file and passes in the loaded
     document, same contract as `WorkspaceService`'s topology methods.
   - `ResourceService._validate_dynamic()`: same split —
     provider-type-pointer existence stays in `_validate_dynamic()`; the
     resource-type/configuration-schema check moved to
     `validate_against_provider_config(provider_config: ProviderConfigModel)`.
   - `WorkspaceService.validate_topology_components()`: gained a new
     `topology_config_models: dict[str, TopologyConfigModel]` parameter
     (already-loaded registry entries keyed by topology type) alongside the
     existing `configuration_model` (kept only for the `additional_topologies`
     flag) and `topology_models` parameters.

## Consequences

- Good: `Configuration` is now consistently a thin pointer aggregator across
  every registry it holds (`providers`, `topologies`), matching how
  `Workspace` references every other kind — no more embedded-list special
  case.
- Good: each provider/topology type gets its own file, own PR, own
  reviewer — directly solves the "one growing monolithic file" problem this
  ADR set out to fix.
- Good: caught and fixed a second real consumer during the refactor —
  `ResourceService` also read the old embedded `ConfigurationProviderModel`
  directly (resource-type + configuration-schema check), not just
  `ProviderService`. Both now follow the identical two-tier split.
- Neutral: the "deep check" methods (`validate_against_provider_config` on
  both `ProviderService`/`ResourceService`, `validate_topology_components`
  on `WorkspaceService`) all require a caller that has already resolved a
  file-pointer into a loaded model — still no solution-wide file-loading
  layer exists in v2 (same accepted gap as every other Workspace Phase 2
  item all session).
- Cost: broke and had to update already-tested code
  (`test_services_provider.py`, `test_services_resource.py`,
  `test_services_workspace.py`, `test_models_configuration.py`) — expected,
  deliberate cost of the refactor, not a regression.

## Remaining Work

- No file-loading layer exists yet to actually call
  `validate_against_provider_config()`/`validate_topology_components()`
  automatically — same deferred item as everywhere else this session.
