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
