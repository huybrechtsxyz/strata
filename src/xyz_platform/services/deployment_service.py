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
from typing import Dict, Optional, List, Tuple

from xyz_platform.models.configuration_model import ConfigurationModel
from xyz_platform.models.deployment_model import DeploymentModel
from xyz_platform.models.store_models import validate_store_security_policy
from xyz_platform.services.base_service import BaseService
from xyz_platform.exceptions import ServiceLoadError, ServiceNotValidatedError


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
        - Environment variable/secret references against workspace
        - Deployment variable/secret references against workspace
        - Bundled source paths exist (if work_path provided)

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

        deployment_values = self.model.spec.deployment or {}

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

    # ==================== Service Methods Section ====================

    def get_related_services(self) -> Optional[Dict[str, Dict[str, BaseService]]]:
        """
        Get cached related services if already loaded.
        """
        return self._related_services

    def load_related_services(
        self, objects_path: str, stage_name: Optional[str] = None
    ) -> Tuple[Dict[str, BaseService], bool]:
        """
        Load workspace and environment services for deployment.

        Architecture:
        - Infrastructure (providers, resources, namespaces, firewalls): Owned by workspace
        - Configuration layering: Handled by WorkspaceController
          (see workspace_controller.py for implementation details)

        Args:
            objects_path: Base directory for resolving relative file paths.
            stage_name: Optional stage name. If provided, loads only that stage's environments.
                       If None, loads all environments from all stages.

        Returns:
            Tuple containing:
            - Dict with structure:
              {
                  "workspace": WorkspaceService,
                  "environments": {stage_name: EnvironmentService (merged), ...},
                  "current_stage": str (if stage_name provided)
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

        # Check objects_path validity before proceeding
        if objects_path is None or not Path(objects_path).is_dir():
            error_msg = f"Invalid objects_path: {objects_path} is not a directory"
            self.logger.error(error_msg, extra={"objects_path": objects_path})
            return {}, False

        self._ensure_validated()
        self.logger.info(
            "Loading related services for deployment",
            extra={"deployment_name": self.get_name(), "stage_name": stage_name},
        )
        success = True

        # Lazy import to avoid circular dependencies
        from xyz_platform.services.workspace_service import WorkspaceService
        from xyz_platform.services.environment_service import EnvironmentService

        # Only store workspace and environment services
        # Infrastructure services accessed via workspace delegation
        # Configuration layering will be handled by WorkspaceController
        services = {
            "workspace": None,
            "environments": {},
        }

        if stage_name:
            services["current_stage"] = stage_name

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
                workspace_path, validate=True
            )

            if not workspace_service.is_validated():
                self.logger.error(
                    "Workspace validation failed",
                    extra={"workspace_name": workspace_name},
                )
                return services, False

            # Step 2: Load workspace infrastructure services (providers, resources, namespaces, firewalls)
            # These are owned by workspace, not deployment
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

            # Step 3: Load environment services from stages
            # Note: Configuration layering (variables/secrets/features) will be handled
            # by WorkspaceController, not here
            stages_to_load = (
                [self.get_stage_by_name(stage_name)]
                if stage_name
                else self.model.spec.stages
            )

            if not stages_to_load or (stage_name and stages_to_load[0] is None):
                self.logger.error(
                    f"Stage '{stage_name}' not found in deployment"
                    if stage_name
                    else "No stages found in deployment"
                )
                return services, False

            self.logger.debug(f"Loading {len(stages_to_load)} stage(s)")

            # Load deployment-level environment files (shared across all stages)
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
                    # Store environment for all stages (deployment-level, not stage-specific)
                    for stage in stages_to_load:
                        services["environments"][stage.name] = env_service
                        self.logger.debug(
                            f"Environment loaded for stage '{stage.name}'"
                        )
            except Exception as e:
                success = False
                self.logger.error(
                    f"Failed to load deployment environments",
                    exc_info=True,
                    extra={"paths": env_paths, "error": str(e)},
                )

            # Continue loading other services for each stage

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
                    "environment_count": len(services["environments"]),
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

    def get_workspace_service(self):
        """Get the workspace service."""
        return self._get_related_service("workspace")

    def get_environment_service(self, stage_name: str):
        """Get a specific environment service by stage name."""
        return self._get_related_service("environments", stage_name)

    def get_environment_services(self) -> Dict[str, BaseService]:
        """Get all environment services.

        Raises:
            ServiceNotValidatedError: If load_related_services() hasn't been called
        """
        if self._related_services is None:
            raise ServiceNotValidatedError("DeploymentService")
        return self._related_services.get("environments", {})

    def get_provider_service(self, provider_name: str):
        """Get a specific provider service by name."""
        return self._get_related_service("providers", provider_name)

    def get_resource_service(self, resource_name: str):
        """Get a specific resource service by name."""
        return self._get_related_service("resources", resource_name)

    def get_namespace_service(self, namespace_name: str):
        """Get a specific namespace service by name."""
        return self._get_related_service("namespaces", namespace_name)

    def get_firewall_service(self, firewall_name: str):
        """Get a specific firewall service by name."""
        return self._get_related_service("firewalls", firewall_name)

    def get_variable_service(self):
        """Get the populated variable service instance."""
        if self._related_services and "variable_service" in self._related_services:
            return self._related_services["variable_service"]
        return None

    def get_secret_service(self):
        """Get the populated secret service instance."""
        if self._related_services and "secret_service" in self._related_services:
            return self._related_services["secret_service"]
        return None

    def get_feature_service(self):
        """Get the populated feature service instance."""
        if self._related_services and "feature_service" in self._related_services:
            return self._related_services["feature_service"]
        return None

    def get_validation_errors(self) -> List[str]:
        """Return the list of validation errors after loading related services."""
        return self._validation_errors

    def get_structured_errors(self) -> List:
        """Return the list of structured PlatformException objects."""
        return self._structured_errors

    def _get_related_service(self, service_type: str, service_name: str = None):
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
        """
        # Fail-fast if not loaded
        if self._related_services is None:
            raise ServiceNotValidatedError("DeploymentService")

        # Workspace service
        if service_type == "workspace":
            return self._related_services.get("workspace")

        # Environment services (by name)
        if service_type == "environments" and service_name:
            return self._related_services.get("environments", {}).get(service_name)

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
