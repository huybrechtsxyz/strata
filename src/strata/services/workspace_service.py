#!/usr/bin/env python3
"""Service for loading and validating workspace configuration."""

from strata.models.configuration_model import ConfigurationModel
from strata.models.network_model import NetworkModel
from strata.models.topology_config_model import TopologyConfigModel
from strata.models.workspace_model import WorkspaceModel
from strata.services.base_service import BaseService
from strata.utils.diagnostics import Diagnostics


class WorkspaceService(BaseService[WorkspaceModel]):
    """Service for handling workspace configuration.

    `validate_topology_components()` below is a self-contained public method
    a future solution-loading layer can call once it has resolved the
    `TopologyConfigModel` instances `configuration.spec.topologies` names —
    same "assume the caller already loaded it" shape as
    `_validate_dynamic(configuration_model)`'s own contract.
    `Topology` itself is inline on `spec.topology` now (ADR-0028), so no
    Topology-document loading is needed; its own internal reference checks
    (`components[].resource`/`namespaces[].namespace` against this
    workspace's `resources`/`namespaces`) are Phase 1 model validators on
    `WorkspaceSpecModel` itself.

    `validate_resource_subnets()` below is the same shape, for
    `WorkspaceResourceModel.subnet.subnet` against the real subnet names
    inside the referenced Network document(s) — `.network` (which Network
    *document*) is already checked in Phase 1
    (`validate_resource_subnet_references`); this is the Phase 2 half, once
    that document is actually loaded.
    """

    def _get_model_class(self) -> type[WorkspaceModel]:
        """Return the WorkspaceModel class for validation."""
        return WorkspaceModel

    def claimed_document_names(self) -> set[str]:
        """Every dns_zones/networks/firewalls/namespaces name claimed by
        some execution step's own `targets` — the ownership half of
        `${output:...}` resolution (docs/design/gap_fit_v1.md gap #12, generalized to
        namespaces so a Module reached through a targeted namespace is
        covered too, not just a directly-targeted DNS/network/firewall
        document — see docs/design/deploy-command.md's "Cross-invocation
        output access" section, "Expanded finding" subsection, for why
        Modules needed the same treatment).

        Pure function of this workspace's own declarations — no other
        loaded document needed. Reuses the same target vocabulary
        `workspace_model.py`'s own `validate_execution()` already validates
        `targets` against (`resources ∪ namespaces ∪ dns_zones ∪ networks ∪
        firewalls`), rather than a second, narrower one — a resource is
        deliberately not surfaced as "claimable" here even though it's a
        valid target too: a Resource document has no Value-token-bearing
        field of its own that `${output:...}` could ever appear in (unlike
        a Module reached through a namespace).
        """
        if self.model is None:
            return set()
        spec = self.model.spec
        claimable = (
            set(spec.dns_zones or [])
            | set(spec.networks or [])
            | set(spec.firewalls or [])
            | set(spec.namespaces or [])
        )
        claimed: set[str] = set()
        for step in spec.execution or []:
            claimed |= set(step.targets) & claimable
        return claimed

    def validate_topology_components(
        self,
        configuration_model: ConfigurationModel,
        topology_config_models: dict[str, TopologyConfigModel],
    ) -> Diagnostics:
        """Cross-check each topology's component roles against the
        configuration's topology type registry (ADR-0013/ADR-0014).

        Direct port of v1's real `WorkspaceService._validate_component_constraints()`/
        `_get_component_role()`: a component's "role" comes from the
        `WorkspaceResourceModel.role` of the resource it references (not a
        field on the component itself), matched against a loaded
        `TopologyConfigModel.spec.components[].role` entries for the
        topology's own `type`.

        Args:
            configuration_model: Loaded configuration registry (used for
                `additional_topologies`; `spec.topologies` itself is just a
                list of TopologyConfig document names — ADR-0014).
            topology_config_models: Already-loaded `TopologyConfigModel`
                instances keyed by topology type (`meta.name`) — the caller
                resolves each name in `configuration_model.spec.topologies`
                itself.
        """
        if self.model is None or not self.model.spec.topology:
            return Diagnostics()

        diagnostics = Diagnostics()
        resource_roles = {r.name: r.role for r in (self.model.spec.resources or [])}

        for topo in self.model.spec.topology:
            topo_type = topo.type
            matching_config = topology_config_models.get(topo_type)

            if matching_config is None:
                if not configuration_model.spec.additional_topologies:
                    diagnostics.error(
                        f"Topology '{topo.name}': type '{topo_type}' is not registered in "
                        "configuration.spec.topologies and additional_topologies is False",
                        location="spec.topology",
                        code="unregistered_topology_type",
                    )
                continue

            role_counts: dict[str, int] = {}
            role_has_module: dict[str, bool] = {}
            for component in topo.components:
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
                            f"Topology '{topo.name}': component role '{role}' is not registered "
                            f"for topology type '{topo_type}' and additional_components is False",
                            location="spec.resources",
                            code="unregistered_component_role",
                        )

            for comp_config in matching_config.spec.components or []:
                actual_count = role_counts.get(comp_config.role, 0)

                if comp_config.required and actual_count == 0:
                    diagnostics.error(
                        f"Topology '{topo.name}': required component role '{comp_config.role}' is missing",
                        location="spec.resources",
                        code="missing_required_role",
                    )
                    continue
                if actual_count == 0:
                    continue

                if comp_config.min_count and actual_count < comp_config.min_count:
                    diagnostics.error(
                        f"Topology '{topo.name}': component role '{comp_config.role}' has "
                        f"{actual_count} instance(s), needs at least {comp_config.min_count}",
                        location="spec.resources",
                        code="role_count_below_minimum",
                    )
                if comp_config.max_count and actual_count > comp_config.max_count:
                    diagnostics.error(
                        f"Topology '{topo.name}': component role '{comp_config.role}' has "
                        f"{actual_count} instance(s), exceeds max {comp_config.max_count}",
                        location="spec.resources",
                        code="role_count_above_maximum",
                    )
                if comp_config.uses_module and not role_has_module.get(comp_config.role, False):
                    diagnostics.error(
                        f"Topology '{topo.name}': component role '{comp_config.role}' requires a "
                        "module attached but none of its resources have one",
                        location="spec.resources",
                        code="role_missing_module",
                    )

        return diagnostics

    def validate_resource_subnets(self, network_models: dict[str, NetworkModel]) -> Diagnostics:
        """Cross-check each resource's `subnet.subnet` against the real
        subnet names inside its referenced Network document.

        `subnet.network` already named a real, existing workspace network
        in Phase 1 (`validate_resource_subnet_references`, `workspace_model.py`)
        — this is the Phase 2 half, once that Network document is actually
        loaded. A Network document's own `spec.networks[]` can declare more
        than one independently-named network (`docs/config/network.md`),
        and `WorkspaceResourceSubnetModel` has no field naming which
        internal network a resource means — only the document as a whole —
        so `subnet.subnet` is checked against the union of every internal
        network's subnet names in that one document, not a single one of
        them.

        Args:
            network_models: Already-loaded `NetworkModel` documents, keyed
                by `meta.name`, for every network name this workspace's
                resources reference via `subnet.network`. A name missing
                from this dict (an unresolved reference) is silently
                skipped — `validate_references` already reported it; this
                method would otherwise duplicate that finding.
        """
        diagnostics = Diagnostics()
        if self.model is None:
            return diagnostics

        for resource in self.model.spec.resources or []:
            if resource.subnet is None:
                continue

            network_model = network_models.get(resource.subnet.network)
            if network_model is None:
                continue  # unresolved reference — validate_references already reported it

            subnet_names = {subnet.name for definition in network_model.spec.networks for subnet in definition.subnets}
            if resource.subnet.subnet not in subnet_names:
                diagnostics.error(
                    f"Resource '{resource.name}' subnet '{resource.subnet.subnet}' not found in network "
                    f"'{resource.subnet.network}'. Available subnets: {sorted(subnet_names)}",
                    location="spec.resources",
                    code="unregistered_subnet",
                )

        return diagnostics
