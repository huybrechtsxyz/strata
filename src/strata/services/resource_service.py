#!/usr/bin/env python3
"""Service for loading and validating resource configurations."""

import re
from typing import List, Optional, Tuple

from strata.models.configuration_model import ConfigurationModel
from strata.models.firewall_model import FirewallModel
from strata.models.resource_model import ResourceModel
from strata.services.base_service import BaseService


class ResourceService(BaseService["ResourceModel"]):
    """Service for handling resource configurations."""

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None):
        """Initialize the ResourceService."""
        super().__init__(path=path, data=data)
        self.model = None

        # Store merged firewall for this resource after validation
        self._merged_firewall: Optional[FirewallModel] = None

    def on_init(self) -> None:
        """Lifecycle hook: called after __init__ completes."""
        pass

    def on_ready(self) -> None:
        """Called after validation succeeds - populate category/subcategory from configuration."""
        # Now model is loaded and validated, populate category/subcategory
        if self.model and self.model.spec and self.model.spec.properties:
            self._populate_category_from_configuration()

    def on_shutdown(self) -> None:
        """Lifecycle hook: called before cleanup/destruction."""
        pass

    def _get_model_class(self):
        """Return the ResourceModel class for validation."""
        return ResourceModel

    def _validate_dynamic(
        self,
        configuration_model: Optional["ConfigurationModel"] = None,
        work_path: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Phase 2: Dynamic validation against configuration.

        Validates cross-references when configuration is provided:
        - Provider type exists in configuration.spec.providers
        - Resource type exists for provider (when additional_resources=False)
        - Configuration fields match schema patterns defined in provider resource

        Additional validation handled elsewhere:
        - Variables/secrets: Validated by WorkspaceService._validate_service_references
        - Lifecycle scripts: Validated by ScriptsModel (FilePath + extensions)
        - Firewall references: FilePath validation ensures firewall file exists

        All structural validation handled by MODEL validators:
        - Unique disk names, labels, mount points
        - Unique volume names
        - Volumes mounted under disk mount points
        - Valid mount paths (absolute, not system directories)
        - Label format with ${.name} substitution support

        Args:
            configuration_model: Optional ConfigurationModel for cross-validation

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)
        """
        if configuration_model is None or self.model is None:
            # No configuration provided or model not initialized, skip dynamic validation
            return True, []

        errors = []

        # Get provider type and resource type from this resource
        provider_type = self.model.spec.properties.provider_type
        resource_type = self.model.spec.properties.resource_type

        # Find matching provider configuration
        config_provider = None
        if configuration_model.spec.providers:
            for provider in configuration_model.spec.providers:
                if provider.name == provider_type:
                    config_provider = provider
                    break

        if config_provider is None:
            errors.append(
                f"Provider type '{provider_type}' not found in configuration. "
                f"Available providers: {[p.name for p in configuration_model.spec.providers] if configuration_model.spec.providers else []}"
            )
            return False, errors

        # Validate resource type if provider doesn't allow additional resources
        if not config_provider.additional_resources:
            if config_provider.resources is None or len(config_provider.resources) == 0:
                errors.append(
                    f"Provider '{provider_type}' has no resources defined in configuration, "
                    f"but additional_resources is False"
                )
                return False, errors

            # Extract resource type names
            valid_resource_types = [res.name for res in config_provider.resources]

            if resource_type not in valid_resource_types:
                errors.append(
                    f"Resource type '{resource_type}' is not valid for provider '{provider_type}'. "
                    f"Valid resource types: {valid_resource_types}"
                )
                return False, errors

        # Validate configuration schema if present
        if self.model.spec.configuration:
            schema_errors = self._validate_configuration_schema(
                config_provider, resource_type, self.model.spec.configuration
            )
            errors.extend(schema_errors)

        return len(errors) == 0, errors

    def _validate_configuration_schema(self, config_provider, resource_type: str, configuration: dict) -> List[str]:
        """
        Validate configuration fields against schema patterns defined in provider resource.

        The schema is defined in configuration.spec.providers[provider_type].resources[resource_type].configuration
        where each field maps to a regex pattern that values must match.

        When additional_configurations=False, only fields in the schema are allowed.
        When additional_configurations=True, extra fields are permitted.

        Args:
            config_provider: The provider configuration from ConfigurationModel
            resource_type: The resource type being validated
            configuration: The configuration dict from this resource

        Returns:
            List[str]: List of validation error messages
        """
        errors: List[str] = []
        config_resource = None
        if config_provider.resources:
            for res in config_provider.resources:
                if res.name == resource_type:
                    config_resource = res
                    break

        if config_resource is None or config_resource.configuration is None:
            # No schema defined, skip validation
            return errors

        schema = config_resource.configuration
        additional_allowed = config_resource.additional_configurations

        # Validate each field in the resource configuration
        for field_name, field_value in configuration.items():
            if field_name not in schema:
                # Field not in schema
                if not additional_allowed:
                    errors.append(
                        f"Configuration field '{field_name}' is not allowed for resource type '{resource_type}'. "
                        f"additional_configurations is False. Valid fields: {list(schema.keys())}"
                    )
                continue

            schema_def = schema[field_name]

            # Handle both string pattern (legacy) and structured field object
            if isinstance(schema_def, str):
                # Legacy: string pattern (always required)
                pattern = schema_def
            elif isinstance(schema_def, dict):
                # Structured field with pattern and required flag
                raw_pattern = schema_def.get("pattern")
                if not raw_pattern:
                    errors.append(f"Configuration field '{field_name}' schema is missing 'pattern' property")
                    continue
                pattern = str(raw_pattern)
            else:
                # ConfigurationSchemaField model instance
                pattern = schema_def.pattern

            # Convert value to string for pattern matching
            value_str = str(field_value)

            # Validate against regex pattern
            try:
                if not re.match(pattern, value_str):
                    errors.append(
                        f"Configuration field '{field_name}' value '{value_str}' does not match "
                        f"required pattern '{pattern}' for resource type '{resource_type}'"
                    )
            except re.error as e:
                errors.append(
                    f"Invalid regex pattern '{pattern}' for field '{field_name}' in configuration schema: {str(e)}"
                )

        # Check for required fields (fields in schema but not in configuration)
        for schema_field, schema_def in schema.items():
            if schema_field in configuration:
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
                    f"Required configuration field '{schema_field}' is missing "
                    f"for resource type '{resource_type}'. Pattern: {pattern}"
                )

        return errors

    def get_provider_type(self) -> str:
        """Get the provider type."""
        self._ensure_validated()
        assert self.model is not None
        return self.model.spec.properties.provider_type

    def get_resource_type(self) -> str:
        """Get the resource type."""
        self._ensure_validated()
        assert self.model is not None
        return self.model.spec.properties.resource_type

    def get_unit_cost(self) -> Optional[float]:
        """Get the resource unit cost."""
        self._ensure_validated()
        assert self.model is not None
        return self.model.spec.properties.unit_cost

    def get_category_and_subcategory(self) -> Tuple[Optional[str], Optional[str]]:
        """Get category and subcategory.

        Returns category and subcategory, already populated from configuration
        if they were empty at load time.
        """
        self._ensure_validated()
        assert self.model is not None
        return (
            self.model.spec.properties.category,
            self.model.spec.properties.subcategory,
        )

    def get_merged_firewall(self) -> Optional[FirewallModel]:
        """Get the merged firewall configuration for this resource.

        This is populated during validation by merging all referenced firewall
        files together. Returns None if no firewall is defined or if validation
        has not been completed yet.
        """
        self._ensure_validated()
        return self._merged_firewall

    def set_merged_firewall(self, firewall: FirewallModel) -> None:
        """Set the merged firewall configuration for this resource.

        This is called during validation to store the merged firewall dict after
        merging all referenced firewall files together.

        Args:
            firewall (dict): The merged firewall configuration to store for this resource
        """
        self._merged_firewall = firewall

    def _populate_category_from_configuration(self):
        """Populate category/subcategory from configuration if empty.

        This is called during on_ready() (after validation) to fill in
        category/subcategory from configuration defaults when they're not
        specified in the resource file.
        """
        if not self.model or not self.model.spec or not self.model.spec.properties:
            return

        properties = self.model.spec.properties

        # Skip if both are already defined
        if properties.category and properties.subcategory:
            return

        try:
            from strata.services.configuration_service import ConfigurationService

            # Try to get configuration from singleton
            config_service = ConfigurationService.get_instance()
            if not config_service or not config_service.model:
                return

            provider_type = properties.provider_type
            resource_type = properties.resource_type

            # Find provider in configuration
            if config_service.model.spec.providers:
                for provider in config_service.model.spec.providers:
                    if provider.name != provider_type:
                        continue

                    # Find resource in provider's resources
                    if provider.resources:
                        for resource in provider.resources:
                            if resource.name == resource_type:
                                # Update properties directly if not set
                                if not properties.category and resource.category:
                                    properties.category = resource.category
                                if not properties.subcategory and resource.subcategory:
                                    properties.subcategory = resource.subcategory
                                return
        except Exception:
            # If configuration service is not available, skip
            pass
