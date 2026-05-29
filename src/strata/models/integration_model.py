#!/usr/bin/env python3
"""Pydantic models for external integration configuration.

Integrations define external tools and services the platform uses to
extend its capabilities (e.g. git, terraform, bitwarden, vault).
"""

from typing import Optional, Set

from pydantic import BaseModel, Field, field_validator, model_validator

from strata.models.auth_models import AuthenticationModel
from strata.models.common_models import CommonLifecycleModel


class IntegrationValidationSpecModel(BaseModel):
    """Validation specification for integration availability checking."""

    command: str = Field(
        ...,
        description="Command to check integration availability (e.g., 'git --version')",
    )
    min_version: Optional[str] = Field(None, description="Minimum required version (e.g., '2.30.0')")
    max_version: Optional[str] = Field(None, description="Maximum supported version (e.g., '2.40.0')")


class IntegrationEndpointsSpecModel(BaseModel):
    """Endpoint specification for remote integrations."""

    address: str = Field(
        ...,
        description="Service endpoint URL (supports env var substitution: ${VAR_NAME:default})",
    )


class IntegrationModel(BaseModel):
    """
    Model for external integration configuration.

    Integrations extend platform capabilities by connecting to external
    tools (CLI), services (APIs), or cloud resources (SDKs).

    Examples:
        - name: git
          type: git
          capabilities: [repository]
          required: true

        - name: bitwarden
          type: bitwarden
          capabilities: [secrets]
          required: false
          endpoints:
            address: https://bitwarden.com/api
    """

    name: str = Field(..., description="Unique integration name (used for registry lookup)")
    type: str = Field(
        ...,
        description="Integration type (maps to integration class: git, terraform, bitwarden, vault, etc.)",
    )
    capabilities: Set[str] = Field(
        default_factory=set,
        description=(
            "Set of capabilities this integration provides. "
            "Valid values: api, container, features, infrastructure, keyvalue, repository, secrets, variables. "
            "Use 'infrastructure' for IaC and configuration-management tools (Terraform, Ansible, OpenTofu). "
            "Use 'container' for container runtimes and container-native deployment tools (Docker, Helm, Podman)."
        ),
    )
    description: Optional[str] = Field(None, description="Human-readable description of the integration")
    required: bool = Field(
        default=False,
        description="Whether this integration is required for platform operation",
    )
    enabled: bool = Field(
        default=True,
        description="Whether this integration is enabled (for optional integrations)",
    )
    validation: Optional[IntegrationValidationSpecModel] = Field(
        None,
        description="Validation specification for checking integration availability",
    )
    authentication: Optional[AuthenticationModel] = Field(
        None, description="Authentication configuration for accessing the integration"
    )
    endpoints: Optional[IntegrationEndpointsSpecModel] = Field(
        None, description="Endpoint specification for remote integrations"
    )
    lifecycle: Optional[CommonLifecycleModel] = Field(
        None, description="Lifecycle hook specification for API integrations"
    )

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, v: Set[str]) -> Set[str]:
        """
        Validate that all capability names are valid.

        Imports inside validator to avoid circular dependency.
        """
        # Lazy import to avoid circular dependency
        from strata.integrations.capabilities import VALID_CAPABILITY_NAMES

        invalid = v - VALID_CAPABILITY_NAMES
        if invalid:
            raise ValueError(
                f"Invalid capability names: {invalid}. Valid capabilities are: {sorted(VALID_CAPABILITY_NAMES)}"
            )
        return v

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        """
        Validate that integration type is either a custom type or a built-in type.

        Custom types (customsecret, customvariable, etc.) are validated immediately.
        Built-in types are assumed valid - actual registration checked at runtime.

        Imports inside validator to avoid circular dependency.
        """
        # Lazy import to avoid circular dependency
        from strata.integrations.capabilities import (
            CUSTOM_INTEGRATION_TYPES,
            is_custom_integration_type,
        )

        # Check if it's a custom type
        if is_custom_integration_type(v):
            return v

        # For built-in types, we just check the format (lowercase, alphanumeric + underscore)
        # Actual registration is checked when IntegrationService tries to create the instance
        if not v.islower() or not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError(
                f"Invalid integration type format: '{v}'. "
                f"Type must be lowercase alphanumeric (with underscores). "
                f"Valid custom types: {sorted(CUSTOM_INTEGRATION_TYPES)}"
            )

        return v

    @model_validator(mode="after")
    def validate_custom_type_capabilities(self):
        """
        Validate that custom integration types have appropriate capabilities.

        For example, customsecret should have 'secrets' in capabilities.
        """
        # Lazy import to avoid circular dependency
        from strata.integrations.capabilities import (
            CUSTOM_TYPE_CAPABILITY_MAP,
            is_custom_integration_type,
        )

        # Only validate custom types
        if not is_custom_integration_type(self.type):
            return self

        # Get expected capability for this custom type
        expected_capability = CUSTOM_TYPE_CAPABILITY_MAP.get(self.type)

        # customapi can have any capability, skip validation
        if expected_capability is None:
            return self

        # Check if expected capability is present
        if expected_capability not in self.capabilities:
            raise ValueError(
                f"Custom integration type '{self.type}' must include "
                f"capability '{expected_capability}' in capabilities. "
                f"Current capabilities: {self.capabilities}"
            )

        return self
