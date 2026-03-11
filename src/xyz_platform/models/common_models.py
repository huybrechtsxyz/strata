#!/usr/bin/env python3
"""
===============================================================================
Script Name   : common_models.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Common models and enums for XYZ Platform using Pydantic.
===============================================================================
"""

from enum import Enum
from pathlib import Path
from typing import Dict, List, Annotated, Optional
from pydantic import (
    BaseModel,
    Field,
    FilePath,
    RootModel,
    StringConstraints,
    field_validator,
)

# List of valid script file extensions
VALID_SCRIPT_EXTENSIONS = {".sh", ".bash", ".py", ".ps1"}

# Reusable resource name type with validation.
# Must start with lowercase letter, contain only lowercase letters, numbers, and underscores.
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
VariableRefs = Optional[List[VariableKey]]
SecretRefs = Optional[List[VariableKey]]
FeatureRefs = Optional[List[VariableKey]]


# Enumeration of supported platform kinds.
class PlatformKind(str, Enum):
    """Enumeration of supported platform kinds."""

    CONFIGURATION = "configuration"
    DEPLOYMENT = "deployment"
    ENVIRONMENT = "environment"
    FIREWALL = "firewall"
    MODULE = "module"
    NAMESPACE = "namespace"
    PROVIDER = "provider"
    RESOURCE = "resource"
    WORKSPACE = "workspace"


# Enumeration of supported workspace versions.
class PlatformVersion(str, Enum):
    """Enumeration of supported platform versions."""

    v1 = "platform.huybrechts.xyz/v1"


# Model for individual script with scope and execution metadata
class ScriptPathModel(BaseModel):
    """Individual script with scope and execution metadata."""

    file: FilePath = Field(description="Path to script file")
    scope: PlatformKind = Field(
        description="Execution scope - determines how many times script runs (deployment, environment, workspace, provider, resource, module, namespace)"
    )
    priority: int = Field(
        default=100,
        ge=0,
        le=9999,
        description="Execution order within scope (lower runs first)",
    )
    target: Optional[str] = Field(
        None,
        description="Optional target filter (e.g., 'vm-*', 'azure-*', 'production')",
    )
    description: Optional[str] = Field(
        None, description="Optional description for documentation purposes"
    )

    @field_validator("path")
    @classmethod
    def validate_script_path(cls, v):
        """Validate script file exists and has valid extension."""
        path = Path(v)
        if not path.exists():
            raise ValueError(f"Script does not exist: {v}")
        if not path.is_file():
            raise ValueError(f"Script path is not a file: {v}")
        if path.suffix not in VALID_SCRIPT_EXTENSIONS:
            raise ValueError(
                f"Script must have a valid extension (.sh, .bash, .py, .ps1), got: {path.suffix}"
            )
        return v


# Model for validating script paths with scope support
class ScriptsModel(BaseModel):
    """Model for validating script paths with scope-aware execution."""

    description: Optional[str] = Field(
        None, description="Optional description for documentation purposes"
    )
    scripts: Optional[List[FilePath | ScriptPathModel]] = None

    @field_validator("scripts")
    @classmethod
    def validate_and_normalize_scripts(cls, v):
        """Validate scripts and normalize simple paths to ScriptModel."""
        if v is None:
            return v
        normalized = []
        for item in v:
            if isinstance(item, (str, Path)):
                # Simple path - validate it exists and has valid extension
                path = Path(item)
                if not path.exists():
                    raise ValueError(f"Script does not exist: {item}")
                if not path.is_file():
                    raise ValueError(f"Script path is not a file: {item}")
                if path.suffix not in VALID_SCRIPT_EXTENSIONS:
                    raise ValueError(
                        f"Script must have a valid extension (.sh, .bash, .py, .ps1), got: {path.suffix}"
                    )
                # Will be converted to ScriptModel with scope inferred from context during build
                normalized.append(item)
            else:
                # Already a ScriptModel, path validation done by ScriptModel validator
                normalized.append(item)
        return normalized


# Model for lifecycle phase with optional scripts and description
class CommonLifecyclePhaseModel(ScriptsModel):
    """Model for common lifecycle phase with script validation."""


# Model for lifecycle phases mapping phase names to configurations
class CommonLifecycleModel(RootModel):
    """
    Lifecycle phases for common models.
    Maps phase names to phase configurations.
    Phase names follow pattern: {command}_{action}_{suffix}
    Examples: config_clear, config_fetch, deploy_plan_before, deploy_apply_after
    """

    root: Dict[str, CommonLifecyclePhaseModel] = {}


# Model for source configuration referencing a repository
class SourceModel(BaseModel):
    """
    Reusable model for source configuration that references a repository.

    Used by resources, modules, and other components to specify where their
    source artifacts come from and where they should be deployed/built.

    Example usage in a resource:
        source:
          repository: my-infra-repo
          source_path: terraform/modules/vpc
          target_path: build/vpc
    """

    repository: PlatformName = Field(
        description="Name of the repository from configuration's repositories list"
    )
    source_path: Annotated[
        str, StringConstraints(min_length=1, strip_whitespace=True)
    ] = Field(
        description="Path to the source artifacts within the repository (relative path)"
    )
    target_path: Optional[
        Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
    ] = Field(
        None,
        description="Target path where artifacts should be built/deployed (relative to build/deploy directory)",
    )
    description: Optional[str] = Field(
        None, description="Optional description for documentation purposes"
    )

    @field_validator("source_path", "target_path")
    @classmethod
    def validate_relative_path(cls, v):
        """
        Validate that paths are relative and secure.

        Ensures paths:
        - Do not contain absolute path markers (/, \\, drive letters)
        - Do not contain parent directory references (..)
        - Do not start with / or \\
        - Use forward slashes only
        """
        if v is None:
            return v

        # Convert to string if Path
        path_str = str(v)

        # Check for absolute paths
        if path_str.startswith("/") or path_str.startswith("\\"):
            raise ValueError(f"Path must be relative, not absolute. Got: {path_str}")

        # Check for drive letters (Windows)
        if len(path_str) >= 2 and path_str[1] == ":":
            raise ValueError(f"Path must be relative, not absolute. Got: {path_str}")

        # Check for parent directory references
        if ".." in path_str:
            raise ValueError(
                f"Path cannot contain parent directory references (..). Got: {path_str}"
            )

        # Normalize to forward slashes
        normalized = path_str.replace("\\", "/")

        # Remove leading/trailing slashes
        normalized = normalized.strip("/")

        return normalized
