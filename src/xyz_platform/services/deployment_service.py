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
