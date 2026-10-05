# Solution-Wide Document Loading & Deferred Phase 2 Validation — Design

- Status: implemented for the loader and three of five remaining
  cross-document checks; two validators remain unbuilt (see below) — two
  others (`validate_topology_references()`, `TopologyService._validate_dynamic()`)
  were deleted outright, not just moved, when Topology was reverted to an
  inline `Workspace` field (ADR-0028); their job is now a same-document
  Phase 1 model validator, not a Phase 2 concern
- Last updated: 2026-10-01 (ADR-0028's topology-inline reversion)

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
This doc's job now is just to track the three that still aren't built.

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

## Validators not yet built at all

| Validator                              | Referencing kind                     | Checks against                      | ADR                                                               |
| -------------------------------------- | ------------------------------------ | ----------------------------------- | ----------------------------------------------------------------- |
| Workspace subnet cross-check           | Workspace (`resource.subnet.subnet`) | loaded `Network` doc's real subnets | [ADR-0012](../decisions/0012-workspace-model-design-decisions.md) |
| `NamespaceService._validate_dynamic()` | Namespace (`modules[].file`)         | real filesystem path + repo map     | [ADR-0010](../decisions/0010-namespace-model-design-decisions.md) |

## The loader itself

Built. `SolutionController`/`find_solution_root` (`solution_controller.py`)
discover and schema-validate every document; `open_solution()`/
`SolutionContext.resolve()` (`solution_context.py`) run the four Phase 2
passes (reference existence, `extends`/tenant resolution, semantic checks,
version pin checks) — see [validate-command.md](validate-command.md) for
the full flow.

## Related Decisions

- [ADR-0010](../decisions/0010-namespace-model-design-decisions.md), [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md), [ADR-0012](../decisions/0012-workspace-model-design-decisions.md), [ADR-0013](../decisions/0013-configuration-topology-registry.md), [ADR-0014](../decisions/0014-provider-topology-config-standalone-kinds.md), [ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md), [ADR-0028](../decisions/0028-topology-inline-reversion.md)

## Remaining Work / Open Questions

- Workspace subnet cross-check (`resource.subnet.subnet` against a real
  `Network` document's subnets) — still not built.
- `NamespaceService._validate_dynamic()` (`modules[].file` against a real
  filesystem path + repo map) — still not built.

## Changelog

- 2026-09-24: Created, consolidating the recurring "no solution-wide
  loading layer" note duplicated across ADR-0010 through ADR-0015.
- 2026-09-24: Updated after checking the real (uncommitted) source —
  the loader and four of seven validators are actually built and wired;
  only the three above remain open.
