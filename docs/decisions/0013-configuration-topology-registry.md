# Configuration Topology Registry (as far as Workspace needs it)

- Status: superseded (structure) — the registry model (`ConfigurationTopologyModel`,
  embedded on `ConfigurationSpecModel`) described below was promoted to a
  standalone kind, `TopologyConfigModel`, by [ADR-0014](0014-provider-topology-config-standalone-kinds.md).
  Content/reasoning below is unchanged and still accurate; only the shape
  (embedded list vs. standalone kind + pointer) changed.
- Date: 2026-09-21
- Related: [ADR-0011](0011-topology-and-provisioning-decoupling.md) (flagged
  this exact registry as Remaining Work), [ADR-0012](0012-workspace-model-design-decisions.md)
  (`WorkspaceModel`, whose Phase 2 validation this registry will eventually
  feed), [ADR-0014](0014-provider-topology-config-standalone-kinds.md)
  (promoted this registry to a standalone kind)

## Context and Problem Statement

`ConfigurationModel` only modeled a provider registry so far (`spec.providers`,
ADR-0003's deliberately-minimal-slice policy). Investigated what Configuration
needs to add specifically to support `Workspace`/`Topology` — found a real,
proven v1 precedent (`ConfigurationTopologyModel`/`ConfigurationComponentModel`
in v1's `configuration_model.py`, consumed by v1's real
`WorkspaceService._validate_component_constraints()`/`_get_component_role()`)
rather than designing this from scratch.

## v1 precedent, verified against real consumer code

v1's `ConfigurationTopologyModel` registers, per topology `type`: an
`additional_components` gate and a list of expected component roles
(`ConfigurationComponentModel`: `role`, `description`, `uses_module`,
`is_control`, `required`, `min_count`, `max_count`). `ConfigurationSpecModel`
adds a matching `additional_topologies` gate and unique-`type` validation.

Checked the actual consumer (`WorkspaceService`, v1) rather than trusting the
schema alone:

- `_get_component_role(component)` resolves a topology component's "role" by
  looking up `component.resource` in `spec.resources` and reading **that
  resource's own `.role` field** — not a field on the component itself. v2
  already has this exact field (`WorkspaceResourceModel.role`), so no new
  field is needed on `TopologyComponentModel` to support this.
- `_validate_component_constraints()` enforces, per registered component role:
  `required` (at least one resource with this role must exist),
  `min_count`/`max_count` (0 = unlimited), and `uses_module` (a resource with
  this role must have at least one attached module).
- `is_control` was checked and found to be **dead schema** — defined in
  `configuration_model.py` but never read anywhere else in v1 (builders,
  services, deployers). Not ported.

## Decision

1. New `config_topology_model.py` (mirrors `config_provider_model.py`'s
   pattern of splitting a registry's models into their own file):
   `ConfigurationTopologyComponentModel` (`role`, `description`, `uses_module`,
   `required`, `min_count`/`max_count` with a `max_count >= min_count unless
   max_count == 0` validator) and `ConfigurationTopologyModel` (`type`,
   `description`, `additional_components`, `components`, unique-role
   validator). `is_control` deliberately dropped — see above.
2. `ConfigurationSpecModel` gains `additional_topologies: bool` (default
   `False`) and `topologies: list[ConfigurationTopologyModel] | None`, plus a
   unique-`type` validator — mirrors `providers`'s existing shape exactly.
3. **Cross-validation against a real Workspace is Phase 2, not built here.**
   v1's actual enforcement (`_validate_component_constraints`) lives in
   `WorkspaceService`, which needs a loaded `ConfigurationModel` plus each
   referenced `Topology` document's resolved components — the same
   file-loading precondition every other Workspace Phase 2 item already
   waits on (ADR-0011/ADR-0012). Only the registry *schema* is built now;
   `WorkspaceService`'s docstring was updated to list this as a fourth
   deferred Phase 2 item alongside Topology/Namespace refs and subnet refs.

## Consequences

- Good: registry mirrors the existing `providers` pattern exactly — no new
  conventions introduced.
- Good: verified against v1's real consumer code (not just its schema) before
  porting, same discipline as every other kind this session — caught and
  dropped one genuinely dead field (`is_control`).
- Good: no new field needed on `TopologyComponentModel` — v2's existing
  `WorkspaceResourceModel.role` already covers what v1 resolves role from.
- Neutral: the registry has no effect yet — it's inert until
  `WorkspaceService`'s Phase 2 validation is built (tracked, not scheduled).

## Remaining Work

- ~~`WorkspaceService`: resolve each `Topology` reference's components' roles
  (via `WorkspaceResourceModel.role`) and validate against
  `ConfigurationModel.spec.topologies`~~ — done as
  `validate_topology_components()` (plus `validate_topology_references()` for
  the plain existence checks), direct ports of v1's
  `_validate_component_constraints()`/`_get_component_role()`. Both are
  self-contained public methods, not wired into `_validate_dynamic()` itself
  — that hook's signature only threads a `configuration_model`, not the
  actual loaded `TopologyModel` instances these checks also need. A future
  solution-loading layer (which resolves `spec.topology[].file` into real
  `TopologyModel`s) is what will actually call them.
