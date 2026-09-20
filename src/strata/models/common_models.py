#!/usr/bin/env python3
"""Common models, enums, and reusable types for Strata v2."""

from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


# Base model configuration for all Strata models
class PlatformBaseModel(BaseModel):
    """Base model for all Strata platform models with standard configuration."""

    model_config = ConfigDict(
        extra="forbid",  # Reject unknown fields
        validate_assignment=True,
        str_strip_whitespace=True,
    )


# Reusable resource name type with validation.
# Must start with lowercase letter, contain only lowercase letters, numbers, dashes, and underscores.
# Compatible with Terraform, Ansible, shell scripts, and other IaC tools.
PlatformName = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z][a-z0-9_-]*$",
        min_length=1,
        max_length=64,
        strip_whitespace=True,
    ),
]

# Variable key type for dictionary keys (environment variables, configurations)
VariableKey = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]

# VariableRefs, SecretRefs, and FeatureRefs are lists of variable keys for referencing in modules and resources
VariableRefs = list[VariableKey] | None
SecretRefs = list[VariableKey] | None
FeatureRefs = list[VariableKey] | None


# Enumeration of supported platform kinds
class PlatformKind(str, Enum):
    """Enumeration of supported platform kinds."""

    PROVIDER = "provider"


# Enumeration of supported workspace versions
class PlatformVersion(str, Enum):
    """Enumeration of supported platform versions."""

    v2 = "strata.huybrechts.xyz/v2"
    v2_omp = "strata.omp.com/v2"


# Canonical API version for all new YAML documents
CANONICAL_API_VERSION = PlatformVersion.v2


class CommonLifecycleModel(PlatformBaseModel):
    """
    IaC workflow lifecycle phases configuration.

    Defines setup, validation, plan, apply, output, and destroy workflow phases.
    """

    setup: dict[str, Any] | None = Field(None, description="Setup phase configuration")
    validation: dict[str, Any] | None = Field(None, description="Validation phase configuration")
    plan: dict[str, Any] | None = Field(None, description="Plan phase configuration")
    apply: dict[str, Any] | None = Field(None, description="Apply phase configuration")
    output: dict[str, Any] | None = Field(None, description="Output phase configuration")
    destroy: dict[str, Any] | None = Field(None, description="Destroy phase configuration")
