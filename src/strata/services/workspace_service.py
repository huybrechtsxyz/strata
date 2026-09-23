#!/usr/bin/env python3
"""Service for loading and validating workspace configuration."""

from strata.models.configuration_model import ConfigurationModel
from strata.models.topology_config_model import TopologyConfigModel
from strata.models.topology_model import TopologyModel
from strata.models.workspace_model import WorkspaceModel
from strata.services.base_service import BaseService
from strata.utils.diagnostics import Diagnostics


class WorkspaceService(BaseService[WorkspaceModel]):
    """Service for handling workspace configuration.

    No Phase 2 dynamic validation wired into `validate()`/`_validate_dynamic()`
    yet: `BaseService`'s hook only threads through a single
    `configuration_model` parameter, but topology cross-checks also need each
    referenced `Topology` document's actual content — this service doesn't
    load files itself (ADR-0003; no solution-loading layer exists in v2 yet).

    `validate_topology_references()` and `validate_topology_components()`
    below are self-contained public methods a future solution-loading layer
    can call once it has resolved the Topology names in `spec.topology[]`
    into real `TopologyModel` instances — same "assume the caller already
    loaded it" shape as `_validate_dynamic(configuration_model)`'s own
    contract.

    Cross-checking each `WorkspaceResourceModel.subnet.subnet` against the
    real subnet names inside the referenced Network document's
    `NetworkDefinitionModel.subnets[]` is still fully deferred (not ported
    yet) for the same file-loading reason.
    """

    def _get_model_class(self) -> type[WorkspaceModel]:
        """Return the WorkspaceModel class for validation."""
        return WorkspaceModel

    def validate_topology_references(self, topology_models: dict[str, TopologyModel]) -> Diagnostics:
        """Cross-check each referenced Topology's internal references
        (`components[].resource`, `namespaces[].namespace`) against this
        workspace's own `resources`/`namespaces` (ADR-0011/ADR-0012).

        Args:
            topology_models: Already-loaded `TopologyModel` instances keyed
                by `WorkspaceTopologyModel.name`. A missing entry for a
                declared `spec.topology[]` reference is itself an error.
        """
        diagnostics = Diagnostics()
        if self.model is None or not self.model.spec.topology:
            return diagnostics

        resource_names = {r.name for r in (self.model.spec.resources or [])}
        namespace_names = set(self.model.spec.namespaces or [])

        for topo_name in self.model.spec.topology:
            topology_model = topology_models.get(topo_name)
            if topology_model is None:
                diagnostics.error(
                    f"Topology '{topo_name}': no loaded TopologyModel provided for validation",
                    location="spec.topology",
                    code="topology_not_loaded",
                )
                continue

            for component in topology_model.spec.components:
                if component.resource not in resource_names:
                    diagnostics.error(
                        f"Topology '{topo_name}': component references undefined resource "
                        f"'{component.resource}'",
                        location="spec.resources",
                        code="undefined_resource",
                    )
            for ns_ref in topology_model.spec.namespaces or []:
                if ns_ref.namespace not in namespace_names:
                    diagnostics.error(
                        f"Topology '{topo_name}': references undefined namespace '{ns_ref.namespace}'",
                        location="spec.namespaces",
                        code="undefined_namespace",
                    )

        return diagnostics

    def validate_topology_components(
        self,
        configuration_model: ConfigurationModel,
        topology_config_models: dict[str, TopologyConfigModel],
        topology_models: dict[str, TopologyModel],
    ) -> Diagnostics:
        """Cross-check each referenced Topology's component roles against the
        configuration's topology type registry (ADR-0013/ADR-0014).

        Direct port of v1's real `WorkspaceService._validate_component_constraints()`/
        `_get_component_role()`: a component's "role" comes from the
        `WorkspaceResourceModel.role` of the resource it references (not a
        field on the component itself), matched against a loaded
        `TopologyConfigModel.spec.components[].role` entries for the
        topology's own `spec.type`.

        Args:
            configuration_model: Loaded configuration registry (used for
                `additional_topologies`; `spec.topologies` itself is just a
                list of TopologyConfig document names — ADR-0014).
            topology_config_models: Already-loaded `TopologyConfigModel`
                instances keyed by topology type (`meta.name`) — the caller
                resolves each name in `configuration_model.spec.topologies`
                itself.
            topology_models: Already-loaded `TopologyModel` instances keyed
                by the Topology document name listed in `spec.topology[]`.
        """
        if self.model is None or not self.model.spec.topology:
            return Diagnostics()

        diagnostics = Diagnostics()
        resource_roles = {r.name: r.role for r in (self.model.spec.resources or [])}

        for topo_name in self.model.spec.topology:
            topology_model = topology_models.get(topo_name)
            if topology_model is None:
                diagnostics.error(
                    f"Topology '{topo_name}': no loaded TopologyModel provided for validation",
                    location="spec.topology",
                    code="topology_not_loaded",
                )
                continue

            topo_type = topology_model.spec.type
            matching_config = topology_config_models.get(topo_type)

            if matching_config is None:
                if not configuration_model.spec.additional_topologies:
                    diagnostics.error(
                        f"Topology '{topo_name}': type '{topo_type}' is not registered in "
                        "configuration.spec.topologies and additional_topologies is False",
                        location="spec.topology",
                        code="unregistered_topology_type",
                    )
                continue

            role_counts: dict[str, int] = {}
            role_has_module: dict[str, bool] = {}
            for component in topology_model.spec.components:
                role = resource_roles.get(component.resource)
                if role is None:
                    continue
                role_counts[role] = role_counts.get(role, 0) + 1
                if component.modules:
                    role_has_module[role] = True

            valid_roles = {c.role for c in (matching_config.spec.components or [])}
            if not matching_config.spec.additional_components:
                for role in role_counts:
                    if role not in valid_roles:
                        diagnostics.error(
                            f"Topology '{topo_name}': component role '{role}' is not registered "
                            f"for topology type '{topo_type}' and additional_components is False",
                            location="spec.resources",
                            code="unregistered_component_role",
                        )

            for comp_config in matching_config.spec.components or []:
                actual_count = role_counts.get(comp_config.role, 0)

                if comp_config.required and actual_count == 0:
                    diagnostics.error(
                        f"Topology '{topo_name}': required component role '{comp_config.role}' is missing",
                        location="spec.resources",
                        code="missing_required_role",
                    )
                    continue
                if actual_count == 0:
                    continue

                if comp_config.min_count and actual_count < comp_config.min_count:
                    diagnostics.error(
                        f"Topology '{topo_name}': component role '{comp_config.role}' has "
                        f"{actual_count} instance(s), needs at least {comp_config.min_count}",
                        location="spec.resources",
                        code="role_count_below_minimum",
                    )
                if comp_config.max_count and actual_count > comp_config.max_count:
                    diagnostics.error(
                        f"Topology '{topo_name}': component role '{comp_config.role}' has "
                        f"{actual_count} instance(s), exceeds max {comp_config.max_count}",
                        location="spec.resources",
                        code="role_count_above_maximum",
                    )
                if comp_config.uses_module and not role_has_module.get(comp_config.role, False):
                    diagnostics.error(
                        f"Topology '{topo_name}': component role '{comp_config.role}' requires a "
                        "module attached but none of its resources have one",
                        location="spec.resources",
                        code="role_missing_module",
                    )

        return diagnostics

