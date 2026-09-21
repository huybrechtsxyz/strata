#!/usr/bin/env python3
"""Service for loading and validating workspace configuration."""

from strata.models.configuration_model import ConfigurationModel
from strata.models.topology_config_model import TopologyConfigModel
from strata.models.topology_model import TopologyModel
from strata.models.workspace_model import WorkspaceModel
from strata.services.base_service import BaseService


class WorkspaceService(BaseService[WorkspaceModel]):
    """Service for handling workspace configuration.

    No Phase 2 dynamic validation wired into `validate()`/`_validate_dynamic()`
    yet: `BaseService`'s hook only threads through a single
    `configuration_model` parameter, but topology cross-checks also need each
    referenced `Topology` document's actual content — this service doesn't
    load files itself (ADR-0003; no solution-loading layer exists in v2 yet).

    `validate_topology_references()` and `validate_topology_components()`
    below are self-contained public methods a future solution-loading layer
    can call once it has resolved `spec.topology[].file` into real
    `TopologyModel` instances — same "assume the caller already loaded it"
    shape as `_validate_dynamic(configuration_model)`'s own contract.

    Cross-checking each `WorkspaceResourceModel.subnet.subnet` against the
    real subnet names inside the referenced Network document's
    `NetworkDefinitionModel.subnets[]` is still fully deferred (not ported
    yet) for the same file-loading reason.
    """

    def _get_model_class(self) -> type[WorkspaceModel]:
        """Return the WorkspaceModel class for validation."""
        return WorkspaceModel

    def validate_topology_references(self, topology_models: dict[str, TopologyModel]) -> tuple[bool, list[str]]:
        """Cross-check each referenced Topology's internal references
        (`components[].resource`, `namespaces[].namespace`) against this
        workspace's own `resources`/`namespaces` (ADR-0011/ADR-0012).

        Args:
            topology_models: Already-loaded `TopologyModel` instances keyed
                by `WorkspaceTopologyModel.name`. A missing entry for a
                declared `spec.topology[]` reference is itself an error.
        """
        if self.model is None or not self.model.spec.topology:
            return True, []

        errors: list[str] = []
        resource_names = {r.name for r in (self.model.spec.resources or [])}
        namespace_names = {n.name for n in (self.model.spec.namespaces or [])}

        for topo_ref in self.model.spec.topology:
            topology_model = topology_models.get(topo_ref.name)
            if topology_model is None:
                errors.append(f"Topology '{topo_ref.name}': no loaded TopologyModel provided for validation")
                continue

            for component in topology_model.spec.components:
                if component.resource not in resource_names:
                    errors.append(
                        f"Topology '{topo_ref.name}': component references undefined resource '{component.resource}'"
                    )
            for ns_ref in topology_model.spec.namespaces or []:
                if ns_ref.namespace not in namespace_names:
                    errors.append(f"Topology '{topo_ref.name}': references undefined namespace '{ns_ref.namespace}'")

        return (len(errors) == 0), errors

    def validate_topology_components(
        self,
        configuration_model: ConfigurationModel,
        topology_config_models: dict[str, TopologyConfigModel],
        topology_models: dict[str, TopologyModel],
    ) -> tuple[bool, list[str]]:
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
                `additional_topologies`; `spec.topologies` itself is now just
                `{name, file}` pointers — ADR-0014).
            topology_config_models: Already-loaded `TopologyConfigModel`
                instances keyed by topology type (`meta.name`) — the caller
                resolves each `configuration_model.spec.topologies[].file`
                pointer itself.
            topology_models: Already-loaded `TopologyModel` instances keyed
                by `WorkspaceTopologyModel.name`.
        """
        if self.model is None or not self.model.spec.topology:
            return True, []

        errors: list[str] = []
        resource_roles = {r.name: r.role for r in (self.model.spec.resources or [])}

        for topo_ref in self.model.spec.topology:
            topology_model = topology_models.get(topo_ref.name)
            if topology_model is None:
                errors.append(f"Topology '{topo_ref.name}': no loaded TopologyModel provided for validation")
                continue

            topo_type = topology_model.spec.type
            matching_config = topology_config_models.get(topo_type)

            if matching_config is None:
                if not configuration_model.spec.additional_topologies:
                    errors.append(
                        f"Topology '{topo_ref.name}': type '{topo_type}' is not registered in "
                        "configuration.spec.topologies and additional_topologies is False"
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
                        errors.append(
                            f"Topology '{topo_ref.name}': component role '{role}' is not registered "
                            f"for topology type '{topo_type}' and additional_components is False"
                        )

            for comp_config in matching_config.spec.components or []:
                actual_count = role_counts.get(comp_config.role, 0)

                if comp_config.required and actual_count == 0:
                    errors.append(
                        f"Topology '{topo_ref.name}': required component role '{comp_config.role}' is missing"
                    )
                    continue
                if actual_count == 0:
                    continue

                if comp_config.min_count and actual_count < comp_config.min_count:
                    errors.append(
                        f"Topology '{topo_ref.name}': component role '{comp_config.role}' has "
                        f"{actual_count} instance(s), needs at least {comp_config.min_count}"
                    )
                if comp_config.max_count and actual_count > comp_config.max_count:
                    errors.append(
                        f"Topology '{topo_ref.name}': component role '{comp_config.role}' has "
                        f"{actual_count} instance(s), exceeds max {comp_config.max_count}"
                    )
                if comp_config.uses_module and not role_has_module.get(comp_config.role, False):
                    errors.append(
                        f"Topology '{topo_ref.name}': component role '{comp_config.role}' requires a "
                        "module attached but none of its resources have one"
                    )

        return (len(errors) == 0), errors

