# Solution-Wide Document Loading & Phase 2 Validation — Design

- Status: current
- Last updated: 2026-11-20

## Overview

A document is validated in two phases. Phase 1 is its own schema: one document, no others needed. Phase 2 needs the
whole solution loaded, because a document names others (a Workspace its Network, a Provider or Resource its
ProviderConfig, a Namespace its Modules) and those references, and what they point at, can only be checked once
every document is in the index.

The loader finds and schema-validates every document; `strata validate` then runs the Phase 2 passes over the index
(see [validate-command.md](validate-command.md) for the command's full flow).

## The loader

- `SolutionController` and `find_solution_root()`
  ([solution_controller.py](../../src/strata/controllers/solution_controller.py)) scan recursively from
  `strata.yaml`'s root, schema-validate every document and index it by `(kind, meta.name)`, the kind taken from the
  document's own `kind:` field ([ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md)).
- `open_solution()` and `SolutionContext.resolve()`
  ([solution_context.py](../../src/strata/controllers/solution_context.py)) run the four Phase 2 passes in order:
  reference existence, `extends` and tenant resolution, semantic checks, version pin checks.

## Cross-document checks

Reference existence is generic: every field annotated with `References(<kind>)` is walked, and a name that does not
resolve is one finding. That covers a Namespace's `modules[].module` and every other name-based reference.

What a reference must also agree with is a semantic check. Each is a small public method on the referencing kind's
Service that takes the loaded target model as a parameter, called from
[semantic_checks.py](../../src/strata/controllers/semantic_checks.py). They are not wired into the Service's
`_validate_dynamic()` hook, whose signature only carries a `ConfigurationModel`.

| Check                                                | Referencing kind                     | Checks against                        | Called from                                |
| ---------------------------------------------------- | ------------------------------------ | ------------------------------------- | ------------------------------------------ |
| `WorkspaceService.validate_topology_components()`    | Workspace (inline `spec.topology[]`) | the loaded `TopologyConfigModel`      | `_check_workspace_topology_components()`   |
| `ProviderService.validate_against_provider_config()` | Provider                             | the loaded `ProviderConfigModel`      | `_check_providers()`                       |
| `ResourceService.validate_against_provider_config()` | Resource                             | the loaded `ProviderConfigModel`      | `_check_resources()`                       |
| `WorkspaceService.validate_resource_subnets()`       | Workspace (`resource.subnet.subnet`) | the referenced Network's real subnets | `_check_workspace_subnets()`               |

**Subnets are checked against the whole Network document.** `subnet.subnet` must be a subnet name in the union of
every internal network in the document `subnet.network` names. A Network document may declare several
independently named networks ([network.md](../config/network.md)), and `WorkspaceResourceSubnetModel` names only
the document, not which network in it. A `subnet.network` that does not resolve is skipped here, because reference
existence already reports it.

Topology checks are not Phase 2: since Topology became an inline Workspace field
([ADR-0028](../decisions/0028-topology-inline-reversion.md)), they are Phase 1 validators on `WorkspaceSpecModel`.

## Related Decisions

- [ADR-0010](../decisions/0010-namespace-model-design-decisions.md),
  [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md),
  [ADR-0012](../decisions/0012-workspace-model-design-decisions.md),
  [ADR-0013](../decisions/0013-configuration-topology-registry.md),
  [ADR-0014](../decisions/0014-provider-topology-config-standalone-kinds.md),
  [ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md),
  [ADR-0028](../decisions/0028-topology-inline-reversion.md)
- [validate-command.md](validate-command.md) — the command that runs these passes.
- [firewall-network-cidr-references.md](firewall-network-cidr-references.md) — `${value:}` addressing into a
  Network's subnets, by position or by `.name`. A separate mechanism from the subnet check above, which resolves
  `WorkspaceResourceSubnetModel.subnet` by name on its own field.

## History

- The doc started as one place for a note repeated across ADR-0010 to ADR-0015: each kind's cross-document check
  waited on a solution-wide loader that did not exist yet. When checked against the source, the loader and four of
  seven parked validators were already built.
- Two parked validators (`validate_topology_references()`, `TopologyService._validate_dynamic()`) were deleted, not
  built, when Topology became an inline Workspace field (ADR-0028); same-document model validators do their job.
- The Workspace subnet check was added last. It checks the union of the document's networks because the reference
  cannot say which internal network it means, and it runs for every workspace, not only one with a `spec.topology`.
- The last parked validator, a Namespace's `modules[].file` against the filesystem and a repository map
  (ADR-0010), became unnecessary when module references became names (`modules[].module`): the generic reference
  walk checks them like any other reference. The doc then had no open work and moved to design.
