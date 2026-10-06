# Solution-Wide Document Loading & Deferred Phase 2 Validation — Design

- Status: implemented for the loader and four of five remaining
  cross-document checks; one validator remains unbuilt (see below) — two
  others (`validate_topology_references()`, `TopologyService._validate_dynamic()`)
  were deleted outright, not just moved, when Topology was reverted to an
  inline `Workspace` field (ADR-0028); their job is now a same-document
  Phase 1 model validator, not a Phase 2 concern
- Last updated: 2026-10-06 (Workspace subnet cross-check implemented)

## Overview

Every kind that references another kind by name (Workspace→Network,
Provider/Resource→ProviderConfig, Namespace→Module) needs a Phase 2 check
once the referenced document is actually loaded. (Workspace's own inline
topology groupings → Resource/Namespace used to be in this list too, back
when Topology was a standalone kind — ADR-0028 reverted that, so those
checks are now same-document Phase 1 `WorkspaceSpecModel` model validators,
not a Phase 2 concern at all.) This is now built:
[ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md)'s
discovery loader is `strata/controllers/solution_controller.py`
(`SolutionController`/`find_solution_root`) plus
`strata/controllers/solution_context.py` (`open_solution()`,
`SolutionContext.resolve()`), and four of the seven originally-parked
validators are wired through `strata/controllers/semantic_checks.py`,
called by `strata validate` (see [validate-command.md](validate-command.md)).
This doc's job now is just to track the one that still isn't built.

## Current Design

- Discovery model: recursive scan from `strata.yaml`'s root, indexed by
  `(kind, meta.name)`, kind taken from each document's own `kind:` field
  ([ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md)).
- Convention shared by every deferred validator below: a small, self-
  contained public method on the referencing kind's Service, taking the
  already-loaded target model(s) as a parameter — never wired into the fixed
  `_validate_dynamic()` hook signature, since that hook only threads a
  `ConfigurationModel`.

## Validators built, tested, and wired

| Validator                                            | Referencing kind                     | Checks against                        | Called from                                              |
| ---------------------------------------------------- | ------------------------------------ | ------------------------------------- | -------------------------------------------------------- |
| `WorkspaceService.validate_topology_components()`    | Workspace (inline `spec.topology[]`) | loaded `TopologyConfigModel` registry | `semantic_checks._check_workspace_topology_components()` |
| `ProviderService.validate_against_provider_config()` | Provider                             | loaded `ProviderConfigModel`          | `semantic_checks._check_providers()`                     |
| `ResourceService.validate_against_provider_config()` | Resource                             | loaded `ProviderConfigModel`          | `semantic_checks._check_resources()`                     |
| `WorkspaceService.validate_resource_subnets()`       | Workspace (`resource.subnet.subnet`) | loaded `Network` doc's real subnets   | `semantic_checks._check_workspace_subnets()`             |

The subnet cross-check resolves every network name a workspace's
resources reference via `subnet.network`, then checks `subnet.subnet`
against the **union** of every internal `NetworkDefinitionModel`'s subnet
names in that one document — a Network document can declare more than one
independently-named network ([docs/config/network.md](../config/network.md)),
and `WorkspaceResourceSubnetModel` has no field naming which internal one a
resource means, only the document as a whole. An unresolved `.network`
name is silently skipped (no duplicate finding — `validate_references`
already reports it).

## Validators not yet built at all

| Validator                              | Referencing kind             | Checks against                  | ADR                                                               |
| -------------------------------------- | ---------------------------- | ------------------------------- | ----------------------------------------------------------------- |
| `NamespaceService._validate_dynamic()` | Namespace (`modules[].file`) | real filesystem path + repo map | [ADR-0010](../decisions/0010-namespace-model-design-decisions.md) |

## The loader itself

Built. `SolutionController`/`find_solution_root` (`solution_controller.py`)
discover and schema-validate every document; `open_solution()`/
`SolutionContext.resolve()` (`solution_context.py`) run the four Phase 2
passes (reference existence, `extends`/tenant resolution, semantic checks,
version pin checks) — see [validate-command.md](validate-command.md) for
the full flow.

## Related Decisions

- [ADR-0010](../decisions/0010-namespace-model-design-decisions.md), [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md), [ADR-0012](../decisions/0012-workspace-model-design-decisions.md), [ADR-0013](../decisions/0013-configuration-topology-registry.md), [ADR-0014](../decisions/0014-provider-topology-config-standalone-kinds.md), [ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md), [ADR-0028](../decisions/0028-topology-inline-reversion.md)
- [firewall-network-cidr-references.md](firewall-network-cidr-references.md) — a
  related but distinct concern: that doc's `${value:}` path addressing into
  `NetworkDefinitionModel.subnets[]` is positional (by list index) only; this
  validator resolves by `.name` instead, so it doesn't share that doc's
  "sharp edge," but both touch the same `Network`/subnet surface.

## Remaining Work / Open Questions

- `NamespaceService._validate_dynamic()` (`modules[].file` against a real
  filesystem path + repo map) — still not built.

## Changelog

- 2026-09-24: Created, consolidating the recurring "no solution-wide
  loading layer" note duplicated across ADR-0010 through ADR-0015.
- 2026-09-24: Updated after checking the real (uncommitted) source —
  the loader and four of seven validators are actually built and wired;
  only the three above remain open.
- 2026-10-06: Implemented the Workspace subnet cross-check —
  `WorkspaceService.validate_resource_subnets()` + `semantic_checks.
  _check_workspace_subnets()`, wired into `_check_workspaces()` unconditionally
  (not gated on `spec.topology` like the sibling topology check). Checks the
  union of every internal `NetworkDefinitionModel`'s subnets in the
  referenced document, since a Network document's own `spec.networks[]` can
  declare more than one independently-named network and
  `WorkspaceResourceSubnetModel` only names the document, not which internal
  network. 5 new unit tests (`test_services_workspace.py`) + 3 new
  integration tests through `open_solution(...).resolve()`
  (`test_semantic_checks.py`). Full check suite green: mypy (146 files),
  ruff, import-linter (1 kept, 0 broken), pytest (2169 passed). Only
  `NamespaceService._validate_dynamic()` remains unbuilt now.
