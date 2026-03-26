#!/usr/bin/env python3
"""
===============================================================================
Script Name   : workspace_service.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Workspace service class
===============================================================================
"""

from typing import Any, Dict, List, Optional, Tuple, Union, cast
from xyz_platform.models.configuration_model import ConfigurationModel
from xyz_platform.models.workspace_model import WorkspaceModel
from xyz_platform.services.base_service import BaseService
from xyz_platform.exceptions import InvalidReferenceError

from xyz_platform.services.configuration_service import ConfigurationService
from xyz_platform.services.provider_service import ProviderService
from xyz_platform.services.resource_service import ResourceService
from xyz_platform.services.namespace_service import NamespaceService
from xyz_platform.services.firewall_service import FirewallService
from xyz_platform.services.module_service import ModuleService


class WorkspaceService(BaseService):
    """Service for handling workspace configurations."""

    # Initialization

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None):
        """
        Initialize the WorkspaceService.

        _related_services = {
            "providers": {"provider_name": ProviderService, ...},
        }
        """
        super().__init__(path=path, data=data)
        self.model: Optional[WorkspaceModel] = None
        self._related_services: Optional[Dict[str, Dict[str, BaseService]]] = None
        self._validation_errors: List[str] = []
        self._structured_errors: List[InvalidReferenceError] = []

    # Abstract methods

    def _get_model_class(self):
        """Return the WorkspaceModel class for validation."""
        return WorkspaceModel

    def _validate_dynamic(
        self,
        configuration_model: Optional[ConfigurationModel] = None,
        work_path: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Phase 2: Dynamic validation against configuration.

        Validates:
        1. Provisioner repository references (workspace.spec.provisioners -> configuration.spec.repositories)
        2. Provisioner validity (validated via ProvisionerType enum)
        3. Module repository references (loaded modules -> configuration.spec.repositories)
        4. Topology provider references (workspace.spec.providers)
        5. Topology provisioner references (workspace.spec.provisioners)
        6. Component roles against configuration topology definitions

        Args:
            configuration_model: Optional ConfigurationModel for cross-validation
            work_path: Optional working path for file resolution

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)
        """
        if not configuration_model or not self.model:
            return True, []

        errors = []
        # Note: Do not call _ensure_validated() here as this is called during validate()
        # before the model is marked as validated

        # Get workspace provider names and create provider map
        workspace_provider_names = {p.name for p in self.model.spec.providers}

        # Get workspace provisioner types
        workspace_provisioner_types = {
            p.provisioner for p in self.model.spec.provisioners
        }

        # Build repository map from configuration
        config_repository_names = set()
        if configuration_model.spec.repositories:
            config_repository_names = {
                repo.name for repo in configuration_model.spec.repositories
            }

        # STEP 1: Validate provisioner repository references
        # Check that each provisioner's source.repository exists in configuration
        for provisioner in self.model.spec.provisioners:
            if provisioner.source and provisioner.source.repository:
                if provisioner.source.repository not in config_repository_names:
                    error = InvalidReferenceError(
                        source_type="Provisioner",
                        source_name=provisioner.name,
                        reference_type="repository",
                        reference_value=provisioner.source.repository,
                    )
                    self._structured_errors.append(error)
                    errors.append(str(error))

        # STEP 2: Provisioner validity is already validated by ProvisionerType enum

        # STEP 3: Validate module repository references (if modules are defined in resources)
        if self.model and self.model.spec.resources:
            for resource in self.model.spec.resources:
                if resource.modules:
                    # Note: Modules in workspace are references with file paths.
                    # The actual module source.repository is in the module file itself.
                    # For thorough validation, we would need to load each module file,
                    # but that's expensive. This check is documented for when module
                    # services are loaded via related_services.
                    pass

        # Validate module repository references from loaded module services (if available)
        if self._related_services and "modules" in self._related_services:
            for module_name, module_service in self._related_services[
                "modules"
            ].items():
                if (
                    hasattr(module_service, "model")
                    and module_service.model is not None
                    and hasattr(module_service.model, "spec")
                    and hasattr(module_service.model.spec, "source")
                    and module_service.model.spec.source
                ):
                    module_repo = module_service.model.spec.source.repository
                    if module_repo not in config_repository_names:
                        error = InvalidReferenceError(
                            source_type="Module",
                            source_name=module_name,
                            reference_type="repository",
                            reference_value=module_repo,
                        )
                        self._structured_errors.append(error)
                        errors.append(str(error))

        # Build topology configuration map and roles map from configuration
        topology_config_map = {}
        topology_roles_map = {}
        if configuration_model.spec.topologies:
            for topo_config in configuration_model.spec.topologies:
                # Store the full config object
                topology_config_map[topo_config.type] = topo_config
                # Build roles map
                if topo_config.components:
                    valid_roles = {comp.role for comp in topo_config.components}
                    topology_roles_map[topo_config.type] = valid_roles

        # Validate topology provider, provisioner, and component role references
        for topology in self.model.spec.topology:
            # Check provider reference
            if topology.provider not in workspace_provider_names:
                error = InvalidReferenceError(
                    source_type="Topology",
                    source_name=topology.name,
                    reference_type="provider",
                    reference_value=topology.provider,
                )
                self._structured_errors.append(error)
                errors.append(str(error))

            # Check provisioner reference
            if topology.provisioner not in workspace_provisioner_types:
                error = InvalidReferenceError(
                    source_type="Topology",
                    source_name=topology.name,
                    reference_type="provisioner",
                    reference_value=topology.provisioner.value,
                )
                self._structured_errors.append(error)
                errors.append(str(error))

            # Validate component roles against configuration topology
            topology_type_normalized = topology.type.lower().replace("-", "_")

            # Find matching configuration topology (with normalization)
            valid_roles = None
            matching_config = None
            for config_type, topo_config in topology_config_map.items():
                config_type_normalized = config_type.lower().replace("-", "_")
                if config_type_normalized == topology_type_normalized:
                    valid_roles = topology_roles_map.get(config_type)
                    matching_config = topo_config
                    break

            # Check if topology type is allowed (additional_topologies validation)
            if (
                matching_config is None
                and not configuration_model.spec.additional_topologies
            ):
                # available_topologies = sorted(topology_config_map.keys())
                error = InvalidReferenceError(
                    source_type="Topology",
                    source_name=topology.name,
                    reference_type="topology type",
                    reference_value=topology.type,
                )
                self._structured_errors.append(error)
                errors.append(str(error))
                continue  # Skip component validation for this topology

            # If topology type is defined in configuration, validate component roles
            if valid_roles:
                additional_components_allowed = (
                    matching_config.additional_components if matching_config else True
                )

                for component in topology.components:
                    # Resolve role from component or resource reference
                    component_role = self._get_component_role(component)
                    if not component_role:
                        continue  # Skip components without role
                    component_role_normalized = component_role.lower().replace("-", "_")
                    valid_roles_normalized = {
                        r.lower().replace("-", "_") for r in valid_roles
                    }

                    if component_role_normalized not in valid_roles_normalized:
                        # Check if additional components are allowed
                        if not additional_components_allowed:
                            # Get component name for error message
                            component_name = component.resource
                            error = InvalidReferenceError(
                                source_type="Component",
                                source_name=component_name,
                                reference_type="role",
                                reference_value=component_role,
                            )
                            self._structured_errors.append(error)
                            errors.append(str(error))

            # Validate component constraints from configuration (min/max count, required, uses_module)
            if matching_config and matching_config.components:
                config_validation_errors = self._validate_component_constraints(
                    topology, matching_config
                )
                errors.extend(config_validation_errors)

        # STEP 5: Validate that all file: references resolve to existing files on disk
        if work_path:
            repo_map = configuration_model.get_repo_map() if configuration_model else {}
            file_refs = []
            for p in self.model.spec.providers:
                file_refs.append((f"Provider '{p.name}'", p.file))
            if self.model.spec.resources:
                for r in self.model.spec.resources:
                    file_refs.append((f"Resource '{r.name}'", r.file))
                    if r.modules:
                        for m in r.modules:
                            file_refs.append(
                                (f"Resource '{r.name}' module '{m.name}'", m.file)
                            )
            if self.model.spec.namespaces:
                for ns in self.model.spec.namespaces:
                    file_refs.append((f"Namespace '{ns.name}'", ns.file))
            if self.model.spec.firewalls:
                for fw in self.model.spec.firewalls:
                    file_refs.append((f"Firewall '{fw.name}'", fw.file))
            errors.extend(self._validate_file_refs(work_path, repo_map, file_refs))

        return len(errors) == 0, errors

    def _validate_component_constraints(self, topology, matching_config) -> List[str]:
        """
        Validate component constraints from configuration.

        Validates:
        - uses_module: Component must have a module if required
        - required: Topology must include required components
        - min_count: Topology must have at least min_count instances
        - max_count: Topology must not exceed max_count instances (0 = unlimited)

        Args:
            topology: WorkspaceTopologyModel instance
            matching_config: ConfigurationTopologyModel instance

        Returns:
            List[str]: List of validation error messages
        """
        errors = []

        # Build a map of component roles to their instances in the workspace topology
        # Count both full definitions and simple resource references by resolving roles
        component_role_counts = {}
        component_role_modules = {}
        for component in topology.components:
            # Resolve role from component or resource reference
            component_role = self._get_component_role(component)
            if not component_role:
                continue  # Skip components without role
            role_normalized = component_role.lower().replace("-", "_")
            component_role_counts[role_normalized] = (
                component_role_counts.get(role_normalized, 0) + 1
            )
            # Check if resource has modules (component is just a reference)
            resource = self._get_resource_by_name(component.resource)
            if resource and resource.modules:
                component_role_modules.setdefault(role_normalized, []).append(
                    component.resource
                )

        # Validate each component configuration constraint
        for config_comp in matching_config.components:
            if not config_comp.role:
                continue  # Skip components without role
            role_normalized = config_comp.role.lower().replace("-", "_")
            actual_count = component_role_counts.get(role_normalized, 0)

            # Validate required components
            if config_comp.required and actual_count == 0:
                error = InvalidReferenceError(
                    source_type="Topology",
                    source_name=topology.name,
                    reference_type="required component role",
                    reference_value=config_comp.role,
                )
                self._structured_errors.append(error)
                errors.append(str(error))
                continue

            # Skip further validation if component is not present
            if actual_count == 0:
                continue

            # Validate min_count constraint
            if config_comp.min_count and actual_count < config_comp.min_count:
                error = InvalidReferenceError(
                    source_type="Topology",
                    source_name=topology.name,
                    reference_type="component role (min_count)",
                    reference_value=f"{config_comp.role} (has {actual_count}, needs {config_comp.min_count})",
                )
                self._structured_errors.append(error)
                errors.append(str(error))

            # Validate max_count constraint (0 means unlimited)
            if (
                config_comp.max_count
                and config_comp.max_count > 0
                and actual_count > config_comp.max_count
            ):
                error = InvalidReferenceError(
                    source_type="Topology",
                    source_name=topology.name,
                    reference_type="component role (max_count)",
                    reference_value=f"{config_comp.role} (has {actual_count}, max {config_comp.max_count})",
                )
                self._structured_errors.append(error)
                errors.append(str(error))

            # Validate uses_module constraint
            if config_comp.uses_module:
                components_without_module = []
                for component in topology.components:
                    # Resolve role from component or resource reference
                    comp_role = self._get_component_role(component)
                    if not comp_role:
                        continue
                    comp_role_normalized = comp_role.lower().replace("-", "_")
                    # Check if resource has modules (component is just a reference)
                    resource = self._get_resource_by_name(component.resource)
                    if comp_role_normalized == role_normalized and (
                        not resource or not resource.modules
                    ):
                        components_without_module.append(component.resource)

                if components_without_module:
                    error = InvalidReferenceError(
                        source_type="Component",
                        source_name=", ".join(components_without_module),
                        reference_type="module (required by role)",
                        reference_value=config_comp.role,
                    )
                    self._structured_errors.append(error)
                    errors.append(str(error))

        return errors

    def _get_resource_by_name(self, resource_name: str) -> Optional[Any]:
        """
        Get a resource definition by its name.

        Args:
            resource_name: Name of the resource to find

        Returns:
            WorkspaceResourceModel instance, or None if not found
        """
        if self.model and self.model.spec.resources:
            for resource_ref in self.model.spec.resources:
                if resource_ref.name == resource_name:
                    return resource_ref
        return None

    def _get_component_role(self, component) -> Optional[str]:
        """
        Get the role for a component by resolving its resource reference.

        Args:
            component: WorkspaceComponentModel instance (has only 'resource' field)

        Returns:
            The role from the referenced resource, or None if not found
        """
        # Component only has a resource name reference - look up role from resources
        if isinstance(component.resource, str):
            # Find the resource definition
            if self.model and self.model.spec.resources:
                for resource_ref in self.model.spec.resources:
                    if resource_ref.name == component.resource:
                        return resource_ref.role

        return None

    # Service Methods

    def get_validation_errors(self) -> List[str]:
        """Return the list of validation errors after loading related services."""
        return self._validation_errors

    def load_workspace_services(
        self, objects_path: Optional[str] = None
    ) -> Tuple[Dict[str, Dict[str, BaseService]], bool]:
        """
        Load all related services (providers, resources, namespaces, firewalls) into a dictionary.

        Uses service cache to avoid re-parsing the same files.

        Args:
            objects_path: Optional base directory for resolving relative file paths.
                          If None, uses the directory of the workspace file.

        Returns:
            Tuple containing:
            - Dict with structure:
              {
                  "providers": {"provider_name": ProviderService, ...},
                  "resources": {"resource_name": ResourceService, ...},
                  "namespaces": {"namespace_name": NamespaceService, ...},
                  "firewalls": {"firewall_name": FirewallService, ...}
              }
            - bool: Success status (True if all services loaded successfully)
        """
        # Return cached services if already loaded
        if self._related_services is not None:
            self.logger.debug("Returning cached related services")
            return self._related_services, True

        self._ensure_validated()
        self.logger.info(
            "Loading related services for workspace",
            extra={"workspace_name": self.get_name()},
        )
        success = True

        # Validate objects_path parameter
        if objects_path is None:
            error_msg = (
                "objects_path parameter is required for loading related services. "
                "Paths in workspace YAML are relative to project root."
            )
            self.logger.error(
                error_msg,
                extra={"workspace_name": self.get_name()},
            )
            raise ValueError(error_msg)

        services = {
            "providers": {},
            "resources": {},
            "namespaces": {},
            "firewalls": {},
            "modules": {},
        }
        if self.model is None:
            error_msg = "Workspace model is not loaded. Cannot load related services."
            self.logger.error(error_msg)
            raise ValueError(error_msg)
        workspace: WorkspaceModel = self.model

        # Build repo_map once for all @repo_name/... path resolutions in this call
        repo_map: Dict[str, str] = ConfigurationService.get_instance().get_repo_map()

        # Load firewall services from workspace spec firewalls
        if self.model and self.model.spec and self.model.spec.firewalls:
            self.logger.debug(f"Loading {len(self.model.spec.firewalls)} firewall(s)")
            for firewall_ref in self.model.spec.firewalls:
                firewall_path = self._resolve_file_path(
                    firewall_ref.file, objects_path, repo_map
                )
                firewall_key = firewall_ref.name
                try:
                    # Use service cache to avoid re-parsing same files
                    fw_service: FirewallService = FirewallService.load(
                        firewall_path, validate=True
                    )
                    if fw_service.is_validated():
                        services["firewalls"][firewall_key] = fw_service
                        self.logger.debug(
                            f"Loaded firewall '{firewall_key}'",
                            extra={"path": firewall_path},
                        )
                    else:
                        success = False
                        errors = fw_service.get_validation_errors()
                        self._validation_errors.append(
                            f"Firewall '{firewall_ref.name}' validation failed"
                        )
                        self._validation_errors.extend(errors)
                        self.logger.warning(
                            f"Firewall '{firewall_ref.name}' validation failed",
                            extra={"path": firewall_path, "error_count": len(errors)},
                        )
                except Exception as e:
                    success = False
                    error_msg = f"Failed to load firewall '{firewall_ref.name}' from {firewall_path}: {str(e)}"
                    self._validation_errors.append(error_msg)
                    self.logger.error(
                        error_msg,
                        exc_info=True,
                        extra={
                            "firewall_name": firewall_ref.name,
                            "path": firewall_path,
                        },
                    )

        # Load provider services
        if workspace.spec.providers:
            self.logger.debug(f"Loading {len(workspace.spec.providers)} provider(s)")
            for provider_ref in workspace.spec.providers:
                provider_path = self._resolve_file_path(
                    provider_ref.file, objects_path, repo_map
                )
                try:
                    # Use service cache to avoid re-parsing same files
                    pv_service: ProviderService = ProviderService.load(
                        provider_path, validate=True
                    )
                    if pv_service.is_validated():
                        services["providers"][provider_ref.name] = pv_service
                        self.logger.debug(
                            f"Loaded provider '{provider_ref.name}'",
                            extra={"path": provider_path},
                        )
                    else:
                        success = False
                        errors = pv_service.get_validation_errors()
                        self._validation_errors.append(
                            f"Provider '{provider_ref.name}' validation failed"
                        )
                        self._validation_errors.extend(errors)
                        self.logger.warning(
                            f"Provider '{provider_ref.name}' validation failed",
                            extra={"path": provider_path, "error_count": len(errors)},
                        )
                except Exception as e:
                    success = False
                    error_msg = f"Failed to load provider '{provider_ref.name}' from {provider_path}: {str(e)}"
                    self._validation_errors.append(error_msg)
                    self.logger.error(
                        error_msg,
                        exc_info=True,
                        extra={
                            "provider_name": provider_ref.name,
                            "path": provider_path,
                        },
                    )

        # Load resource services from workspace resources section
        # Resources are defined in workspace.spec.resources with file references
        # Components in topology reference these resources by name
        if workspace.spec.resources:
            self.logger.debug(f"Loading {len(workspace.spec.resources)} resource(s)")
            for resource_ref in workspace.spec.resources:
                resource_path = self._resolve_file_path(
                    resource_ref.file, objects_path, repo_map
                )
                resource_key = resource_ref.name
                if resource_key not in services["resources"]:
                    try:
                        # Use service cache to avoid re-parsing same files
                        rx_service: ResourceService = ResourceService.load(
                            resource_path, validate=True
                        )
                        if rx_service.is_validated():
                            services["resources"][resource_key] = rx_service
                            self.logger.debug(
                                f"Loaded resource '{resource_key}'",
                                extra={"path": resource_path},
                            )

                            # Merge multiple firewalls if resource references more than one
                            if (
                                resource_ref.firewalls
                                and len(resource_ref.firewalls) > 0
                            ):
                                self.logger.debug(
                                    f"Merging {len(resource_ref.firewalls)} firewall(s) for resource '{resource_key}'"
                                )
                                firewall_services = []
                                for fw_name in resource_ref.firewalls:
                                    fw_service = services["firewalls"].get(fw_name)
                                    if fw_service:
                                        firewall_services.append(fw_service)
                                    else:
                                        self.logger.warning(
                                            f"Firewall '{fw_name}' referenced by resource '{resource_key}' not found"
                                        )

                                if firewall_services:
                                    try:
                                        merged_firewall = (
                                            FirewallService.merge_firewalls(
                                                firewall_services
                                            )
                                        )
                                        rx_service.set_merged_firewall(merged_firewall)
                                        self.logger.debug(
                                            f"Successfully merged {len(firewall_services)} firewall(s) for resource '{resource_key}'"
                                        )
                                    except Exception as e:
                                        success = False
                                        error_msg = f"Failed to merge firewalls for resource '{resource_key}': {str(e)}"
                                        self._validation_errors.append(error_msg)
                                        self.logger.error(
                                            error_msg,
                                            exc_info=True,
                                            extra={
                                                "resource_name": resource_key,
                                                "firewall_count": len(
                                                    firewall_services
                                                ),
                                            },
                                        )

                        else:
                            success = False
                            errors = rx_service.get_validation_errors()
                            self._validation_errors.append(
                                f"Resource '{resource_ref.name}' validation failed"
                            )
                            self._validation_errors.extend(errors)
                            self.logger.warning(
                                f"Resource '{resource_ref.name}' validation failed",
                                extra={
                                    "path": resource_path,
                                    "error_count": len(errors),
                                },
                            )

                    except Exception as e:
                        success = False
                        error_msg = f"Failed to load resource '{resource_ref.name}' from {resource_path}: {str(e)}"
                        self._validation_errors.append(error_msg)
                        self.logger.error(
                            error_msg,
                            exc_info=True,
                            extra={
                                "resource_name": resource_ref.name,
                                "path": resource_path,
                            },
                        )

        # Load module services referenced by resources
        if workspace and workspace.spec and workspace.spec.resources:
            for resource_ref in workspace.spec.resources:
                if resource_ref.modules:
                    self.logger.debug(
                        f"Loading {len(resource_ref.modules)} module(s) for resource '{resource_ref.name}'"
                    )
                    for module_ref in resource_ref.modules:
                        module_path = self._resolve_file_path(
                            module_ref.file, objects_path, repo_map
                        )
                        # Use resource_name:module_name as key to avoid conflicts
                        module_key = f"{resource_ref.name}:{module_ref.name}"

                        try:
                            mod_service: ModuleService = ModuleService.load(
                                module_path, validate=True
                            )
                            if mod_service.is_validated():
                                services["modules"][module_key] = mod_service
                                self.logger.debug(
                                    f"Loaded module '{module_ref.name}' for resource '{resource_ref.name}'",
                                    extra={"path": module_path},
                                )
                            else:
                                success = False
                                errors = mod_service.get_validation_errors()
                                self._validation_errors.append(
                                    f"Module '{module_ref.name}' for resource '{resource_ref.name}' validation failed"
                                )
                                self._validation_errors.extend(errors)
                                self.logger.warning(
                                    f"Module '{module_ref.name}' validation failed",
                                    extra={
                                        "resource_name": resource_ref.name,
                                        "path": module_path,
                                        "error_count": len(errors),
                                    },
                                )
                        except Exception as e:
                            success = False
                            error_msg = f"Failed to load module '{module_ref.name}' for resource '{resource_ref.name}' from {module_path}: {str(e)}"
                            self._validation_errors.append(error_msg)
                            self.logger.error(
                                error_msg,
                                exc_info=True,
                                extra={
                                    "resource_name": resource_ref.name,
                                    "module_name": module_ref.name,
                                    "path": module_path,
                                },
                            )

        # Load namespace services
        if workspace.spec.namespaces:
            self.logger.debug(f"Loading {len(workspace.spec.namespaces)} namespace(s)")
            for namespace_ref in workspace.spec.namespaces:
                namespace_path = self._resolve_file_path(
                    namespace_ref.file, objects_path, repo_map
                )
                try:
                    # Use service cache to avoid re-parsing same files
                    ns_service: NamespaceService = NamespaceService.load(
                        namespace_path, validate=True
                    )
                    if ns_service.is_validated():
                        services["namespaces"][namespace_ref.name] = ns_service
                        self.logger.debug(
                            f"Loaded namespace '{namespace_ref.name}'",
                            extra={"path": namespace_path},
                        )
                    else:
                        success = False
                        errors = ns_service.get_validation_errors()
                        self._validation_errors.append(
                            f"Namespace '{namespace_ref.name}' validation failed"
                        )
                        self._validation_errors.extend(errors)
                        self.logger.warning(
                            f"Namespace '{namespace_ref.name}' validation failed",
                            extra={"path": namespace_path, "error_count": len(errors)},
                        )
                except Exception as e:
                    success = False
                    error_msg = f"Failed to load namespace '{namespace_ref.name}' from {namespace_path}: {str(e)}"
                    self._validation_errors.append(error_msg)
                    self.logger.error(
                        error_msg,
                        exc_info=True,
                        extra={
                            "namespace_name": namespace_ref.name,
                            "path": namespace_path,
                        },
                    )

        # Load module services referenced by namespaces
        if workspace.spec.namespaces:
            for namespace_ref in workspace.spec.namespaces:
                # First ensure the namespace service is loaded
                namespace_service = services["namespaces"].get(namespace_ref.name)
                if (
                    namespace_service
                    and hasattr(namespace_service.model.spec, "modules")
                    and namespace_service.model.spec.modules
                ):
                    self.logger.debug(
                        f"Loading {len(namespace_service.model.spec.modules)} module(s) for namespace '{namespace_ref.name}'"
                    )
                    for module_ref in namespace_service.model.spec.modules:
                        module_path = self._resolve_file_path(
                            module_ref.file, objects_path, repo_map
                        )
                        # Use namespace_name:module_name as key to avoid conflicts
                        module_key = f"{namespace_ref.name}:{module_ref.name}"

                        try:
                            mod_service = ModuleService.load(module_path, validate=True)
                            if mod_service.is_validated():
                                services["modules"][module_key] = mod_service
                                self.logger.debug(
                                    f"Loaded module '{module_ref.name}' for namespace '{namespace_ref.name}'",
                                    extra={"path": module_path},
                                )
                            else:
                                success = False
                                errors = mod_service.get_validation_errors()
                                self._validation_errors.append(
                                    f"Module '{module_ref.name}' for namespace '{namespace_ref.name}' validation failed"
                                )
                                self._validation_errors.extend(errors)
                                self.logger.warning(
                                    f"Module '{module_ref.name}' validation failed",
                                    extra={
                                        "namespace_name": namespace_ref.name,
                                        "path": module_path,
                                        "error_count": len(errors),
                                    },
                                )
                        except Exception as e:
                            success = False
                            error_msg = f"Failed to load module '{module_ref.name}' for namespace '{namespace_ref.name}' from {module_path}: {str(e)}"
                            self._validation_errors.append(error_msg)
                            self.logger.error(
                                error_msg,
                                exc_info=True,
                                extra={
                                    "namespace_name": namespace_ref.name,
                                    "module_name": module_ref.name,
                                    "path": module_path,
                                },
                            )

        # Cache services if successful
        if success:
            self._related_services = services
            self.logger.info(
                "All related services loaded successfully",
                extra={
                    "workspace_name": self.get_name(),
                    "providers": len(services["providers"]),
                    "resources": len(services["resources"]),
                    "modules": len(services["modules"]),
                    "namespaces": len(services["namespaces"]),
                    "firewalls": len(services["firewalls"]),
                },
            )
        else:
            self.logger.warning(
                "Some related services failed to load",
                extra={
                    "workspace_name": self.get_name(),
                    "error_count": len(self._validation_errors),
                },
            )

        return services, success

    def get_firewall_services(self) -> Optional[Dict[str, FirewallService]]:
        """Get a specific firewall service by name."""
        value = self._get_workspace_related_services("firewalls", None)
        if value is not None and isinstance(value, dict):
            casted = {
                k: cast(FirewallService, v)
                for k, v in value.items()
                if isinstance(v, FirewallService)
            }
            return casted
        return None

    def get_firewall_service(self, firewall_name: str) -> Optional[FirewallService]:
        """Get a specific firewall service by name."""
        value = self._get_workspace_related_services("firewalls", firewall_name)
        if value is not None and isinstance(value, FirewallService):
            return cast(FirewallService, value)
        return None

    def get_module_services(self) -> Optional[Dict[str, ModuleService]]:
        """Get a specific module service by resource and module name."""
        value = self._get_workspace_related_services("modules", None)
        if value is not None and isinstance(value, dict):
            casted = {
                k: cast(ModuleService, v)
                for k, v in value.items()
                if isinstance(v, ModuleService)
            }
            return casted
        return None

    def get_module_service(
        self, resource_name: str, module_name: str
    ) -> Optional[ModuleService]:
        """Get a specific module service by resource and module name."""
        module_key = f"{resource_name}:{module_name}"
        value = self._get_workspace_related_services("modules", module_key)
        if value is not None and isinstance(value, ModuleService):
            return cast(ModuleService, value)
        return None

    def get_namespace_services(
        self,
    ) -> Optional[Dict[str, NamespaceService]]:
        """Get a specific namespace service by name."""
        value = self._get_workspace_related_services("namespaces", None)
        if value is not None and isinstance(value, dict):
            casted = {
                k: cast(NamespaceService, v)
                for k, v in value.items()
                if isinstance(v, NamespaceService)
            }
            return casted
        return None

    def get_namespace_service(self, namespace_name: str) -> Optional[NamespaceService]:
        """Get a specific namespace service by name."""
        value = self._get_workspace_related_services("namespaces", namespace_name)
        if value is not None and isinstance(value, NamespaceService):
            return cast(NamespaceService, value)
        return None

    def get_provider_services(self) -> Optional[Dict[str, ProviderService]]:
        """Get a specific provider service by name."""
        value = self._get_workspace_related_services("providers", None)
        if value is not None and isinstance(value, dict):
            casted = {
                k: cast(ProviderService, v)
                for k, v in value.items()
                if isinstance(v, ProviderService)
            }
            return casted
        return None

    def get_provider_service(self, provider_name: str) -> Optional[ProviderService]:
        """Get a specific provider service by name."""
        value = self._get_workspace_related_services("providers", provider_name)
        if value is not None and isinstance(value, ProviderService):
            return cast(ProviderService, value)
        return None

    def get_resource_services(
        self,
    ) -> Optional[Dict[str, ResourceService]]:
        """Get a specific resource service by name."""
        value = self._get_workspace_related_services("resources", None)
        if value is not None and isinstance(value, dict):
            casted = {
                k: cast(ResourceService, v)
                for k, v in value.items()
                if isinstance(v, ResourceService)
            }
            return casted
        return None

    def get_resource_service(self, resource_name: str) -> Optional[ResourceService]:
        """Get a specific resource service by name."""
        value = self._get_workspace_related_services("resources", resource_name)
        if value is not None and isinstance(value, ResourceService):
            return cast(ResourceService, value)
        return None

    # Helper method to get related services

    def _get_workspace_related_services(
        self, service_type: str, service_name: Optional[str] = None
    ) -> Optional[Union[BaseService, Dict[str, BaseService]]]:
        """
        Get a specific related service by type and optionally by name.

        This is a helper method for subclasses that manage related services.

        Args:
            service_type: Type of service (e.g., 'providers', 'resources', etc.)
            service_name: Optional name for dict-based services

        Returns:
            The requested service or None if not found
        """
        # Check if subclass has _related_services attribute
        if not hasattr(self, "_related_services") or self._related_services is None:
            return None

        # Check if requested service type is available
        if service_type not in self._related_services:
            return None

        # Get the service object for the requested type
        service_obj = self._related_services.get(service_type)

        # If service is a dict and name provided, get specific item
        if isinstance(service_obj, dict):
            if service_name:
                return service_obj.get(service_name)
            # No service_name requested → return the full dict
            return service_obj

        return None
