#!/usr/bin/env python3
"""Common models, enums, and reusable types for Strata v2."""

from enum import Enum
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, RootModel, StringConstraints, field_validator

# Allowed script file extensions for lifecycle phase scripts.
SCRIPT_EXTENSIONS = {".sh", ".bash", ".py", ".ps1", ".js", ".mjs", ".go"}


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


class ScriptPathModel(PlatformBaseModel):
    """Individual script with scope and execution metadata."""

    file: str = Field(description="Path to script file")
    scope: PlatformKind = Field(
        description="Execution scope - determines how many times the script runs "
        "(e.g. deployment, environment, workspace, provider, resource, module, namespace)"
    )
    priority: int = Field(
        default=100,
        ge=0,
        le=9999,
        description="Execution order within scope (lower runs first)",
    )
    target: str | None = Field(
        None,
        description="Optional target filter (e.g., 'vm-*', 'azure-*', 'production')",
    )
    description: str | None = Field(None, description="Optional description for documentation purposes")

    @field_validator("file")
    @classmethod
    def validate_script_path(cls, v: str) -> str:
        """Validate script file has a valid extension.

        Filesystem existence checks are deferred to a later service-layer phase
        because the file may live in a remote repo not yet synced to disk.
        """
        path = Path(v)
        if path.suffix not in SCRIPT_EXTENSIONS:
            raise ValueError(
                f"Script must have a valid extension (.sh, .bash, .py, .ps1, .js, .mjs, .go), got: {path.suffix}"
            )
        return v


class ScriptsModel(PlatformBaseModel):
    """Model for validating script paths with scope-aware execution."""

    description: str | None = Field(None, description="Optional description for documentation purposes")
    scripts: list[str | ScriptPathModel] | None = None

    @field_validator("scripts")
    @classmethod
    def validate_and_normalize_scripts(
        cls, v: list[str | ScriptPathModel] | None
    ) -> list[str | ScriptPathModel] | None:
        """Validate scripts have valid extensions.

        Filesystem existence checks are deferred to a later service-layer phase
        because files may live in remote repos not yet synced to disk.
        """
        if v is None:
            return v
        for item in v:
            if isinstance(item, str):
                path = Path(item)
                if path.suffix not in SCRIPT_EXTENSIONS:
                    raise ValueError(
                        f"Script must have a valid extension (.sh, .bash, .py, .ps1, .js, .mjs, .go), got: {path.suffix}"
                    )
            # ScriptPathModel entries already validated their own `file` field.
        return v


class CommonLifecyclePhaseModel(ScriptsModel):
    """Lifecycle phase configuration: a description plus its scripts."""


class CommonLifecycleModel(RootModel[dict[str, CommonLifecyclePhaseModel]]):
    """
    Lifecycle phases for common models.

    Maps phase names to phase configurations. Phase names follow the pattern
    ``{command}_{action}_{suffix}``, e.g. ``deploy_plan_before``,
    ``deploy_provision``, ``deploy_destroy_after``, ``config_clear``. This is an
    open map (any phase name is allowed) rather than a fixed set of fields, since
    each kind and provisioner type defines its own set of supported phases.
    """

    root: dict[str, CommonLifecyclePhaseModel] = Field(default_factory=dict)
