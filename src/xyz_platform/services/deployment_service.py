#!/usr/bin/env python3
"""
===============================================================================
Script Name   : deployment_service.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Service for handling deployment configurations and validation.
===============================================================================
"""

import re
from pathlib import Path
from typing import Dict, Optional, List, Tuple, TYPE_CHECKING

from xyz_platform.models.configuration_model import ConfigurationModel
from xyz_platform.models.deployment_model import DeploymentModel
from xyz_platform.models.store_models import validate_store_security_policy
from xyz_platform.services.base_service import BaseService
from xyz_platform.exceptions import ServiceLoadError, ServiceNotValidatedError

if TYPE_CHECKING:
    from xyz_platform.services.workspace_service import WorkspaceService
    from xyz_platform.services.environment_service import EnvironmentService


class DeploymentService(BaseService):
    """Service for handling deployment configurations."""

    def __init__(self, path=None, data=None):
        super().__init__(path, data)
        self.model: Optional[DeploymentModel] = None
        self._related_services: Optional[Dict[str, Dict[str, BaseService]]] = None
        self._validation_errors: List[str] = []

    def _get_model_class(self):
        """Return the DeploymentModel class for validation."""
        return DeploymentModel

    def _validate_dynamic(
        self,
        configuration_model: Optional["ConfigurationModel"] = None,
        work_path: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Phase 2: Validate deployment against dynamic configuration.

        Validates cross-references when related services are loaded:
        - Deployment layer values against configuration.spec.layering
        - Deployment properties against configuration.spec.deployment.properties schema
        - Environment variable/secret references against workspace
        - Deployment variable/secret references against workspace
        - Security policies for variable/secret/feature stores

        Note: Uniqueness validations (environments, configurations, variables, secrets)
        are already enforced by MODEL validators in DeploymentSpecModel.

        Args:
            configuration_model: Optional ConfigurationModel for cross-validation
            work_path: Optional working directory for validating bundled source paths

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)
        """
        errors = []

        # Validate deployment layers against configuration layering
        if configuration_model and configuration_model.spec.layering:
            layer_errors = self._validate_deployment_layers(configuration_model)
            errors.extend(layer_errors)

        # Validate deployment properties against configuration schema
        if configuration_model and configuration_model.spec.deployment:
            properties_errors = self._validate_deployment_properties(
                configuration_model
            )
            errors.extend(properties_errors)

        # Validate against security policies if configuration is provided
        if configuration_model and configuration_model.spec.security:
            security = configuration_model.spec.security
            security_errors = validate_store_security_policy(
                variables=self.model.spec.variables,
                secrets=self.model.spec.secrets,
                features=self.model.spec.features,
                allowed_variable_stores=security.allowed_variable_stores,
                allowed_secret_stores=security.allowed_secret_stores,
                allowed_feature_stores=security.allowed_feature_stores,
            )
            errors.extend(security_errors)

        # Note: File path validation is now handled by Pydantic FilePath at model load time
        # No need to validate sources here since files are validated when model is loaded

        return len(errors) == 0, errors

    def _validate_deployment_layers(
        self, configuration_model: "ConfigurationModel"
    ) -> List[str]:
        """
        Validate deployment layer values against configuration layering definition.

        Args:
            configuration_model: Configuration model with layering definition

        Returns:
            List[str]: List of validation error messages
        """
        errors = []

        # No validation if no layering configured
        if not configuration_model.spec.layering:
            return errors

        # CRITICAL: Validate last layer is named "environment" (should already be validated in model)
        if configuration_model.spec.layering[-1].name != "environment":
            errors.append(
                f"Configuration error: Last layer must be named 'environment', "
                f"got '{configuration_model.spec.layering[-1].name}'"
            )
            return errors  # Fatal error - can't proceed with other validations

        deployment_values = self.model.spec.layers or {}

        # Validate all required layers are provided
        for layer in configuration_model.spec.layering:
            if layer.required and layer.name not in deployment_values:
                errors.append(
                    f"Required layer '{layer.name}' not provided in deployment"
                )

            # Validate pattern if value exists and pattern is defined
            if layer.name in deployment_values and layer.pattern:
                value = deployment_values[layer.name]
                try:
                    if not re.match(layer.pattern, value):
                        errors.append(
                            f"Layer '{layer.name}' value '{value}' does not match "
                            f"pattern '{layer.pattern}'"
                        )
                except re.error as e:
                    errors.append(
                        f"Invalid regex pattern for layer '{layer.name}': {layer.pattern} - {e}"
                    )

        # CRITICAL: Validate environment is provided (last layer must always have a value)
        if "environment" not in deployment_values:
            errors.append(
                "Required layer 'environment' not provided in deployment. "
                "The last layer must always be 'environment'."
            )

        # Warn on unknown layers (not in configuration)
        configured_names = {layer.name for layer in configuration_model.spec.layering}
        unknown_layers = set(deployment_values.keys()) - configured_names
        if unknown_layers:
            self.logger.warning(
                f"Deployment contains layers not defined in configuration: {unknown_layers}"
            )

        return errors

    def _validate_deployment_properties(
        self, configuration_model: "ConfigurationModel"
    ) -> List[str]:
        """
        Validate deployment properties against configuration schema.

        The schema is defined in configuration.spec.deployment.properties
        where each field maps to a regex pattern that values must match.

        When additional_properties=False, only fields in the schema are allowed.
        When additional_properties=True, extra fields are permitted.

        Args:
            configuration_model: Configuration model with deployment schema

        Returns:
            List[str]: List of validation error messages
        """
        errors = []

        # No validation if no deployment schema configured
        if not configuration_model.spec.deployment:
            return errors

        deployment_config = configuration_model.spec.deployment
        schema = deployment_config.properties or {}
        additional_allowed = deployment_config.additional_properties
        deployment_properties = self.model.spec.properties or {}

        # Validate each field in deployment.spec.properties
        for field_name, field_value in deployment_properties.items():
            if field_name not in schema:
                # Field not in schema
                if not additional_allowed:
                    errors.append(
                        f"Deployment property '{field_name}' is not allowed. "
                        f"additional_properties is False. Valid fields: {list(schema.keys())}"
                    )
                continue

            schema_def = schema[field_name]

            # Handle both string pattern (legacy) and structured field object
            if isinstance(schema_def, str):
                # Legacy: string pattern (always required)
                pattern = schema_def
            elif isinstance(schema_def, dict):
                # Structured field with pattern and required flag
                pattern = schema_def.get("pattern")
                if not pattern:
                    errors.append(
                        f"Deployment property '{field_name}' schema is missing 'pattern' property"
                    )
                    continue
            else:
                # ConfigurationSchemaField model instance
                pattern = schema_def.pattern

            # Convert value to string for pattern matching
            value_str = str(field_value)

            # Validate against regex pattern
            try:
                if not re.match(pattern, value_str):
                    errors.append(
                        f"Deployment property '{field_name}' value '{value_str}' does not match "
                        f"required pattern '{pattern}'"
                    )
            except re.error as e:
                errors.append(
                    f"Invalid regex pattern '{pattern}' for property '{field_name}' in "
                    f"deployment schema: {str(e)}"
                )

        # Check for required fields (fields in schema but not in properties)
        for schema_field, schema_def in schema.items():
            if schema_field in deployment_properties:
                continue

            # Determine if field is required
            is_required = True  # Default to required
            if isinstance(schema_def, dict):
                is_required = schema_def.get("required", True)
            elif hasattr(schema_def, "required"):
                is_required = schema_def.required

            if is_required:
                # Get pattern for error message
                if isinstance(schema_def, str):
                    pattern = schema_def
                elif isinstance(schema_def, dict):
                    pattern = schema_def.get("pattern", "N/A")
                else:
                    pattern = schema_def.pattern

                errors.append(
                    f"Required deployment property '{schema_field}' is missing. "
                    f"Pattern: {pattern}"
                )

        return errors

    def load_related_services(
        self, objects_path: str
    ) -> Tuple[Dict[str, BaseService], bool]:
        """
        Load workspace and environment services for deployment.

        Architecture:
        - Infrastructure (providers, resources, namespaces, firewalls): Owned by workspace
        - Configuration layering: Handled by controller layer (future)
        - Environments: Deployment-level (merged if multiple files)

        Args:
            objects_path: Base directory for resolving relative file paths

        Returns:
            Tuple containing:
            - Dict with structure:
              {
                  "workspace": WorkspaceService,
                  "environment": EnvironmentService (merged if multiple files)
              }
            - bool: Success status

        Note:
            Infrastructure services (providers, resources, etc.) are accessed via
            workspace service delegation, not stored here.
        """
        # Return cached services if already loaded
        if self._related_services is not None:
            self.logger.debug("Returning cached related services")
            return self._related_services, True

        # Check objects_path validity
        if objects_path is None or not Path(objects_path).is_dir():
            error_msg = f"Invalid objects_path: {objects_path} is not a directory"
            self.logger.error(error_msg, extra={"objects_path": objects_path})
            return {}, False

        self._ensure_validated()
        self.logger.info(
            "Loading related services for deployment",
            extra={"deployment_name": self.get_name()},
        )
        success = True

        # Lazy import to avoid circular dependencies
        from xyz_platform.services.workspace_service import WorkspaceService
        from xyz_platform.services.environment_service import EnvironmentService

        # Store workspace and environment services
        # Infrastructure services accessed via workspace delegation
        services = {
            "workspace": None,
            "environment": None,  # Single environment per deployment
        }

        try:
            # Step 1: Load workspace service
            if not self.model.spec.workspace:
                self.logger.error("Workspace not found in deployment")
                return services, False

            workspace_ref = self.model.spec.workspace
            workspace_name = workspace_ref.name
            workspace_path = workspace_ref.file
            self.logger.debug(
                "Loading workspace",
                extra={
                    "workspace_name": str(workspace_name),
                    "workspace_path": str(workspace_path),
                },
            )

            # Use BaseService.load() which has caching built-in
            workspace_service: WorkspaceService = WorkspaceService.load(
                str(workspace_path), validate=True
            )

            if not workspace_service.is_validated():
                self.logger.error(
                    "Workspace validation failed",
                    extra={"workspace_name": workspace_name},
                )
                return services, False

            # Step 2: Load workspace infrastructure services
            workspace_services, ws_success = workspace_service.load_related_services(
                objects_path=objects_path
            )

            if not ws_success:
                success = False
                errors = workspace_service.get_validation_errors()
                self._validation_errors.extend(errors)
                self.logger.warning(
                    "Some workspace services failed to load",
                    extra={
                        "deployment_name": self.get_name(),
                        "error_count": len(errors),
                    },
                )

            # Store workspace service (infrastructure accessed via delegation)
            services["workspace"] = workspace_service
            self.logger.debug(
                "Workspace loaded with infrastructure services",
                extra={
                    "providers": len(workspace_services.get("providers", {})),
                    "resources": len(workspace_services.get("resources", {})),
                    "namespaces": len(workspace_services.get("namespaces", {})),
                    "firewalls": len(workspace_services.get("firewalls", {})),
                },
            )

            # Step 3: Load and merge environment files
            env_paths = [str(env_path) for env_path in self.model.spec.environments]

            self.logger.debug(
                f"Loading deployment environments: {len(env_paths)} file(s)",
                extra={"paths": env_paths},
            )

            try:
                # If multiple environment files, merge them
                if len(env_paths) > 1:
                    self.logger.debug(
                        f"Merging {len(env_paths)} environment files for deployment"
                    )
                    work_path = Path(objects_path)
                    merged_env = EnvironmentService.merge_envfiles(env_paths, work_path)
                    # Create a service from the merged model
                    env_service = EnvironmentService(data=merged_env.model_dump())
                    # Validate the merged environment
                    is_valid, errors = env_service.validate()
                    if not is_valid:
                        self.logger.warning(
                            f"Merged deployment environment validation failed",
                            extra={"errors": errors},
                        )
                        success = False
                else:
                    # Single environment file - load directly
                    env_service = EnvironmentService.load(env_paths[0], validate=True)

                if not env_service.is_validated():
                    self.logger.warning(
                        f"Deployment environment validation failed",
                        extra={"paths": env_paths},
                    )
                    success = False
                else:
                    # Store the single environment service for this deployment
                    # Stages are pipeline metadata only, not linked to environments
                    services["environment"] = env_service
                    self.logger.debug("Environment loaded for deployment")

            except Exception as e:
                success = False
                self.logger.error(
                    f"Failed to load deployment environments",
                    exc_info=True,
                    extra={"paths": env_paths, "error": str(e)},
                )

        except Exception as e:
            success = False
            deployment_name = self.model.meta.name if self.model else "unknown"
            self.logger.error(
                f"Failed to load related services for deployment '{deployment_name}'",
                exc_info=True,
                extra={"error_type": type(e).__name__},
            )
            error = ServiceLoadError(
                service_name=deployment_name,
                reason=f"Failed to load related services: {str(e)}",
                cause=e,
            )
            self._structured_errors.append(error)
            self._validation_errors.append(str(error))
            return services, False

        if success:
            self._related_services = services
            self.logger.info(
                "All related services loaded successfully for deployment",
                extra={
                    "deployment_name": self.get_name(),
                    "workspace": (
                        services["workspace"].get_name()
                        if services["workspace"]
                        else None
                    ),
                    "environment": (
                        services["environment"].get_name()
                        if services["environment"]
                        else None
                    ),
                },
            )
        else:
            self.logger.warning(
                "Some services failed to load",
                extra={
                    "deployment_name": self.get_name(),
                    "error_count": len(self._validation_errors),
                },
            )

        return services, success

    def validate_related_services(self) -> Tuple[bool, List[str]]:
        """
        Validate cross-service references after all services are loaded.

        This performs validations that require access to loaded workspace and environment
        services. Should be called after load_related_services() completes successfully.

        Validates:
        - Environment overrides reference existing workspace resources/providers/modules
        - Stage provisioner/topology references are valid
        - Resource dependencies (depends_on) reference existing resources
        - Resource firewall references are valid
        - Resource namespace references are valid
        - Resource provider references are valid

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)

        Raises:
            ServiceNotValidatedError: If load_related_services() hasn't been called
        """
        if self._related_services is None:
            raise ServiceNotValidatedError(
                "DeploymentService: load_related_services() must be called before validate_related_services()"
            )

        errors = []
        workspace = self._related_services.get("workspace")
        environment = self._related_services.get("environment")

        # Can't validate without workspace
        if not workspace:
            errors.append(
                "Workspace service not loaded, cannot validate cross-references"
            )
            return False, errors

        # Get workspace infrastructure for validation
        workspace_services, _ = workspace.load_related_services(str(Path.cwd()))
        providers = workspace_services.get("providers", {})
        resources = workspace_services.get("resources", {})
        namespaces = workspace_services.get("namespaces", {})
        firewalls = workspace_services.get("firewalls", {})

        # Validation 1: Environment overrides reference valid workspace entities
        if environment and environment.has_overrides():
            # Check resource overrides
            for resource_name in environment.get_overridden_resource_names():
                if resource_name not in resources:
                    errors.append(
                        f"Environment overrides non-existent resource '{resource_name}'"
                    )

            # Check provider overrides
            for provider_name in environment.get_overridden_provider_names():
                if provider_name not in providers:
                    errors.append(
                        f"Environment overrides non-existent provider '{provider_name}'"
                    )

            # Check module overrides (modules are within resources)
            for module_key in environment.get_overridden_module_keys():
                # module_key format: "resource_name.module_name"
                if "." in module_key:
                    resource_name, module_name = module_key.split(".", 1)
                    if resource_name not in resources:
                        errors.append(
                            f"Environment overrides module in non-existent resource '{resource_name}'"
                        )
                    # Note: Can't validate module exists without loading resource details

        # Validation 2: Stage provisioner/topology references
        if self.model.spec.stages:
            for stage in self.model.spec.stages:
                if stage.provisioner:
                    # Check provisioner exists (would need workspace.get_provisioner_service())
                    # TODO: Implement when provisioner service exists
                    pass

                if stage.topology:
                    # Check topology exists (would need workspace.get_topology_service())
                    # TODO: Implement when topology service exists
                    pass

        # Validation 3: Resource cross-references (workspace-level dependencies)
        # Check that workspace resource dependencies reference existing resources
        if workspace.model and workspace.model.spec.resources:
            for workspace_resource in workspace.model.spec.resources:
                resource_name = workspace_resource.name

                # Validate depends_on references
                if workspace_resource.depends_on:
                    for dep_resource in workspace_resource.depends_on:
                        if dep_resource not in resources:
                            errors.append(
                                f"Resource '{resource_name}' depends on non-existent resource '{dep_resource}'"
                            )

                # Validate firewall references
                if workspace_resource.firewalls:
                    for firewall_ref in workspace_resource.firewalls:
                        if firewall_ref not in firewalls:
                            errors.append(
                                f"Resource '{resource_name}' references non-existent firewall '{firewall_ref}'"
                            )

        success = len(errors) == 0

        if success:
            self.logger.info(
                "Related services validation passed",
                extra={"deployment_name": self.get_name()},
            )
        else:
            self.logger.warning(
                "Related services validation failed",
                extra={
                    "deployment_name": self.get_name(),
                    "error_count": len(errors),
                },
            )

        return success, errors

    def apply_environment_overrides(self) -> Tuple[bool, List[str]]:
        """
        Apply environment overrides to workspace resources, providers, and modules.

        This modifies the loaded workspace services in-place to reflect environment-specific
        configurations. Should be called after load_related_services() and validate_related_services().

        Overrides are applied in precedence order:
        1. Workspace base values (lowest priority)
        2. Environment properties (if any)
        3. Environment-specific overrides (highest priority)

        Note: Stages are pipeline metadata only and have no relationship to environment overrides.
              1 deployment = 1 workspace instance with 1 environment's overrides applied.

        Returns:
            Tuple[bool, List[str]]: (success, list of error/warning messages)

        Raises:
            ServiceNotValidatedError: If load_related_services() hasn't been called
        """
        if self._related_services is None:
            raise ServiceNotValidatedError(
                "DeploymentService: load_related_services() must be called before apply_environment_overrides()"
            )

        errors = []
        workspace = self._related_services.get("workspace")
        environment = self._related_services.get("environment")

        # Can't apply without workspace
        if not workspace:
            errors.append("Workspace service not loaded, cannot apply overrides")
            return False, errors

        # Can't apply without environment or if no overrides defined
        if not environment:
            self.logger.debug("No environment loaded, skipping override application")
            return True, []

        if not environment.has_overrides():
            self.logger.debug("No overrides defined in environment")
            return True, []

        # Get workspace services
        workspace_services, _ = workspace.load_related_services(str(Path.cwd()))
        resources = workspace_services.get("resources", {})
        providers = workspace_services.get("providers", {})

        self.logger.info(
            "Applying environment overrides to workspace",
            extra={
                "deployment_name": self.get_name(),
                "environment_name": environment.get_name(),
            },
        )

        # Apply resource overrides
        for resource_name in environment.get_overridden_resource_names():
            resource_override = environment.get_resource_override(resource_name)
            if not resource_override:
                continue

            # Get the workspace resource model
            workspace_resource = next(
                (r for r in workspace.model.spec.resources if r.name == resource_name),
                None,
            )
            if not workspace_resource:
                errors.append(
                    f"Resource override for non-existent resource '{resource_name}' (skipped)"
                )
                self.logger.warning(
                    f"Skipping override for non-existent resource '{resource_name}'"
                )
                continue

            # Apply overrides (only override non-None values)
            if resource_override.description is not None:
                workspace_resource.description = resource_override.description
            if resource_override.enabled is not None:
                workspace_resource.enabled = resource_override.enabled
            if resource_override.condition is not None:
                workspace_resource.condition = resource_override.condition
            if resource_override.role is not None:
                workspace_resource.role = resource_override.role
            if resource_override.count is not None:
                workspace_resource.count = resource_override.count
            if resource_override.depends_on is not None:
                workspace_resource.depends_on = resource_override.depends_on
            if resource_override.references is not None:
                # Merge references (override wins)
                if workspace_resource.references:
                    workspace_resource.references.update(resource_override.references)
                else:
                    workspace_resource.references = resource_override.references
            if resource_override.firewalls is not None:
                workspace_resource.firewalls = resource_override.firewalls
            if resource_override.configuration is not None:
                # Deep merge configuration (override wins)
                if workspace_resource.configuration:
                    workspace_resource.configuration.update(
                        resource_override.configuration
                    )
                else:
                    workspace_resource.configuration = resource_override.configuration
            if resource_override.custom is not None:
                # Deep merge custom (override wins)
                if workspace_resource.custom:
                    workspace_resource.custom.update(resource_override.custom)
                else:
                    workspace_resource.custom = resource_override.custom
            if resource_override.labels is not None:
                # Merge labels (override wins)
                if workspace_resource.labels:
                    workspace_resource.labels.update(resource_override.labels)
                else:
                    workspace_resource.labels = resource_override.labels
            if resource_override.tags is not None:
                workspace_resource.tags = resource_override.tags

            self.logger.debug(f"Applied resource override for '{resource_name}'")

        # Apply module overrides
        for module_key in environment.get_overridden_module_keys():
            if isinstance(module_key, tuple) and len(module_key) == 3:
                resource_name, module_name, slot_type = module_key
            else:
                continue

            module_override = environment.get_module_override(
                resource_name, module_name, slot_type or "main"
            )
            if not module_override:
                continue

            # Get the workspace resource
            workspace_resource = next(
                (r for r in workspace.model.spec.resources if r.name == resource_name),
                None,
            )
            if not workspace_resource or not workspace_resource.modules:
                errors.append(
                    f"Module override for '{resource_name}.{module_name}' (resource has no modules - skipped)"
                )
                self.logger.warning(
                    f"Skipping module override for '{resource_name}.{module_name}' - resource has no modules"
                )
                continue

            # Find the module to override
            target_module = next(
                (
                    m
                    for m in workspace_resource.modules
                    if m.name == module_name
                    and (m.slot_type or "main") == (slot_type or "main")
                ),
                None,
            )
            if not target_module:
                errors.append(
                    f"Module override for '{resource_name}.{module_name}' (module not found - skipped)"
                )
                self.logger.warning(
                    f"Skipping module override for '{resource_name}.{module_name}' - module not found in workspace"
                )
                continue

            # Apply module overrides
            if module_override.slot_type is not None:
                target_module.slot_type = module_override.slot_type
            if module_override.enabled is not None:
                target_module.enabled = module_override.enabled
            if module_override.configuration is not None:
                # Deep merge configuration (override wins)
                if target_module.configuration:
                    target_module.configuration.update(module_override.configuration)
                else:
                    target_module.configuration = module_override.configuration

            self.logger.debug(
                f"Applied module override for '{resource_name}.{module_name}'"
            )

        # Apply provider overrides (minimal - providers mostly configured in provider files)
        for provider_name in environment.get_overridden_provider_names():
            provider_override = environment.get_provider_override(provider_name)
            if not provider_override:
                continue

            # Get the workspace provider model
            workspace_provider = next(
                (p for p in workspace.model.spec.providers if p.name == provider_name),
                None,
            )
            if not workspace_provider:
                errors.append(
                    f"Provider override for non-existent provider '{provider_name}' (skipped)"
                )
                self.logger.warning(
                    f"Skipping override for non-existent provider '{provider_name}'"
                )
                continue

            # Apply provider overrides
            if provider_override.description is not None:
                workspace_provider.description = provider_override.description

            self.logger.debug(f"Applied provider override for '{provider_name}'")

        # Success if no critical errors (skipped overrides are warnings, not failures)
        critical_errors = [e for e in errors if "skipped" not in e.lower()]
        success = len(critical_errors) == 0

        if success:
            self.logger.info(
                "Environment overrides applied successfully",
                extra={
                    "deployment_name": self.get_name(),
                    "environment_name": environment.get_name(),
                    "warnings": len(errors),  # These are non-critical warnings
                },
            )
        else:
            self.logger.error(
                "Failed to apply environment overrides",
                extra={
                    "deployment_name": self.get_name(),
                    "critical_error_count": len(critical_errors),
                },
            )

        return success, errors

    def get_workspace_service(self) -> "WorkspaceService":
        """
        Get the workspace service.

        Returns:
            WorkspaceService instance

        Raises:
            ServiceNotValidatedError: If load_related_services() hasn't been called
        """
        if self._related_services is None:
            raise ServiceNotValidatedError("DeploymentService")
        return self._related_services.get("workspace")

    def get_environment_service(self) -> Optional["EnvironmentService"]:
        """
        Get the environment service for this deployment.

        Note: There is only one environment per deployment. Stages are pipeline
              metadata and have no relationship to environments.

        Returns:
            EnvironmentService instance or None if not loaded

        Raises:
            ServiceNotValidatedError: If load_related_services() hasn't been called
        """
        if self._related_services is None:
            raise ServiceNotValidatedError("DeploymentService")
        return self._related_services.get("environment")

    def get_provider_service(self, provider_name: str):
        """
        Get a specific provider service by name (delegates to workspace).

        Args:
            provider_name: Name of the provider

        Returns:
            ProviderService instance or None if not found

        Raises:
            ServiceNotValidatedError: If load_related_services() hasn't been called
        """
        return self._get_related_service("providers", provider_name)

    def get_resource_service(self, resource_name: str):
        """
        Get a specific resource service by name (delegates to workspace).

        Args:
            resource_name: Name of the resource

        Returns:
            ResourceService instance or None if not found

        Raises:
            ServiceNotValidatedError: If load_related_services() hasn't been called
        """
        return self._get_related_service("resources", resource_name)

    def get_namespace_service(self, namespace_name: str):
        """
        Get a specific namespace service by name (delegates to workspace).

        Args:
            namespace_name: Name of the namespace

        Returns:
            NamespaceService instance or None if not found

        Raises:
            ServiceNotValidatedError: If load_related_services() hasn't been called
        """
        return self._get_related_service("namespaces", namespace_name)

    def get_firewall_service(self, firewall_name: str):
        """
        Get a specific firewall service by name (delegates to workspace).

        Args:
            firewall_name: Name of the firewall

        Returns:
            FirewallService instance or None if not found

        Raises:
            ServiceNotValidatedError: If load_related_services() hasn't been called
        """
        return self._get_related_service("firewalls", firewall_name)

    def _get_related_service(
        self, service_type: str, service_name: Optional[str] = None
    ):
        """
        Get a specific related service by type and optionally by name.

        For infrastructure services (providers, resources, namespaces, firewalls),
        delegates to workspace service. For environments, returns from deployment's
        environment dict.

        Args:
            service_type: Type of service (e.g., 'workspace', 'environments',
                         'providers', 'resources', 'namespaces', 'firewalls')
            service_name: Optional name for dict-based services (environments,
                         providers, resources, etc.)

        Returns:
            The requested service or None if not found

        Raises:
            ServiceNotValidatedError: If load_related_services() hasn't been called
        """
        # Fail-fast if not loaded
        if self._related_services is None:
            raise ServiceNotValidatedError("DeploymentService")

        # Workspace service
        if service_type == "workspace":
            return self._related_services.get("workspace")

        # Environment service (single instance per deployment)
        if service_type == "environment":
            return self._related_services.get("environment")

        # Infrastructure services: delegate to workspace
        if service_type in ("providers", "resources", "namespaces", "firewalls"):
            workspace = self._related_services.get("workspace")
            if workspace is None:
                return None

            # Delegate to workspace's accessor method
            if service_type == "providers" and service_name:
                return workspace.get_provider_service(service_name)
            elif service_type == "resources" and service_name:
                return workspace.get_resource_service(service_name)
            elif service_type == "namespaces" and service_name:
                return workspace.get_namespace_service(service_name)
            elif service_type == "firewalls" and service_name:
                return workspace.get_firewall_service(service_name)

        return None

    def get_validation_errors(self) -> List[str]:
        """Return the list of validation errors after loading related services."""
        return self._validation_errors

    def get_build_path(self, build_path: Path) -> Path:
        """
        Get the build path for the deployment.

        Constructs a build directory path using deployment name and version.

        Args:
            build_path: Base build directory path

        Returns:
            Path: Build path for this deployment (build_path / "{name}-{version}")

        Example:
            build_path = Path("/tmp/builds")
            deployment name = "test_deployment"
            deployment version = "1.0.0"
            returns: Path("/tmp/builds/test_deployment-1.0.0")
        """
        deployment_name = self.get_name()
        deployment_version = self.get_version()
        return build_path / f"{deployment_name}-{deployment_version}"
