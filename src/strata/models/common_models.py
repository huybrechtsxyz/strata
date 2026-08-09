#!/usr/bin/env python3
"""Common models, enums, and reusable types for Strata."""

import re
import warnings
from enum import Enum
from pathlib import Path
from typing import Annotated, Dict, List, Optional, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StringConstraints,
    field_validator,
    model_validator,
)

from strata.utils.config import SCRIPT_EXTENSIONS

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
    TENANT = "tenant"
    DEPLOYMENT = "deployment"
    DNS = "dns"
    ENVIRONMENT = "environment"
    FIREWALL = "firewall"
    MODULE = "module"
    NETWORK = "network"
    NAMESPACE = "namespace"
    PLATFORM_MODEL = "platform_model"
    DEPLOYMENT_MANIFEST = "deployment-manifest"
    PROVIDER = "provider"
    RESOURCE = "resource"
    WORKSPACE = "workspace"
    VERSION_LOCK = "version-lock"
    VERSION_MANIFEST = "version"
    PROMOTION_RECORD = "promotion-record"


# Enumeration of supported workspace versions.
class PlatformVersion(str, Enum):
    """Enumeration of supported platform versions."""

    v1 = "strata.huybrechts.xyz/v1"
    v1_omp = "strata.omp.com/v1"  # Hidden alias — accepted in validation, not advertised


# The canonical apiVersion for all new YAML documents.
CANONICAL_API_VERSION = PlatformVersion.v1

# Kinds that are internal build/deploy artifacts, not user-authored YAML documents.
INTERNAL_KINDS: frozenset = frozenset(
    {
        PlatformKind.PLATFORM_MODEL,
        PlatformKind.DEPLOYMENT_MANIFEST,
        PlatformKind.VERSION_LOCK,
        PlatformKind.VERSION_MANIFEST,
        PlatformKind.PROMOTION_RECORD,
    }
)

# Kinds users actually author as YAML documents — everything in PlatformKind except
# the internal, machine-generated-only kinds above. Single source of truth for any
# doc/script that needs to print a "valid kinds" list — derive from this, don't
# hand-copy the values (see scripts/Check.ps1's kind docs coverage check).
USER_AUTHORABLE_KINDS: frozenset = frozenset(PlatformKind) - INTERNAL_KINDS


# Enumeration of supported provisioner names.
class ProvisionerType(str, Enum):
    """Enumeration of supported provisioner names."""

    TERRAFORM = "terraform"
    ANSIBLE = "ansible"
    SCRIPT = "script"
    COMPOSE = "compose"
    HELM = "helm"
    ARGOCD = "argocd"
    FLUX = "flux"
    BICEP = "bicep"


# Provisioner types that do not require an IaC source directory — they render
# from the platform artifact at build time and commit to a git remote at deploy time.
_SYNC_PROVISIONER_TYPES = frozenset({ProvisionerType.ARGOCD, ProvisionerType.FLUX})


# Enumeration of supported service deployer types (for module-level service deployment).
class ServiceDeployerType(str, Enum):
    """Enumeration of supported service deployer types for module deployment."""

    HELM = "helm"
    COMPOSE = "compose"
    ARGOCD = "argocd"
    SCRIPT = "script"


class PlatformBaseModel(BaseModel):
    """Base for all user-authored YAML document models.

    Forbids extra fields at parse time — typos and wrong-model fields are
    caught immediately instead of silently dropped.
    """

    model_config = ConfigDict(extra="forbid")


# Model for individual script with scope and execution metadata
class ScriptPathModel(PlatformBaseModel):
    """Individual script with scope and execution metadata."""

    file: str = Field(description="Path to script file")
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
    description: Optional[str] = Field(None, description="Optional description for documentation purposes")

    @field_validator("file")
    @classmethod
    def validate_script_path(cls, v):
        """Validate script file has a valid extension.

        Filesystem existence checks are deferred to Phase 2 (service layer)
        because the file may live in a remote repo not yet synced to disk.
        """
        path = Path(v)
        if path.suffix not in SCRIPT_EXTENSIONS:
            raise ValueError(
                f"Script must have a valid extension (.sh, .bash, .py, .ps1, .js, .mjs, .go), got: {path.suffix}"
            )
        return v


# Model for validating script paths with scope support
class ScriptsModel(PlatformBaseModel):
    """Model for validating script paths with scope-aware execution."""

    description: Optional[str] = Field(None, description="Optional description for documentation purposes")
    scripts: Optional[List[str | ScriptPathModel]] = None

    @field_validator("scripts")
    @classmethod
    def validate_and_normalize_scripts(cls, v):
        """Validate scripts have valid extensions and normalize simple paths.

        Filesystem existence checks are deferred to Phase 2 (service layer)
        because files may live in remote repos not yet synced to disk.
        """
        if v is None:
            return v
        normalized = []
        for item in v:
            if isinstance(item, (str, Path)):
                # Simple path - validate extension only (existence checked in Phase 2)
                path = Path(item)
                if path.suffix not in SCRIPT_EXTENSIONS:
                    raise ValueError(
                        f"Script must have a valid extension (.sh, .bash, .py, .ps1, .js, .mjs, .go), got: {path.suffix}"
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


# Model for source configuration referencing a repository or Helm chart registry
class SourceModel(PlatformBaseModel):
    """
    Reusable model for source configuration.

    Two modes (mutually exclusive, validated):
      1. Git-based: repository + source_path  — used for Terraform modules, local charts, etc.
      2. Chart-based: chart_repository + chart_name  — used for Helm/ArgoCD chart registry pulls.

    Example — git-based:
        source:
          repository: my-infra-repo
          source_path: terraform/modules/vpc
          target_path: build/vpc

    Example — Helm chart registry:
        source:
          chart_name: authentik
          chart_version: "2024.12.0"
          chart_repository: https://charts.goauthentik.io
    """

    repository: Optional[PlatformName] = Field(
        None, description="Name of the repository from solution registered repositories (via strata repo add)"
    )
    source_path: Optional[Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]] = Field(
        None,
        description="Path to the source artifacts within the repository (relative path)",
    )
    target_path: Optional[Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]] = Field(
        None,
        description="Target path where artifacts should be built/deployed (relative to build/deploy directory)",
    )
    description: Optional[str] = Field(None, description="Optional description for documentation purposes")

    # Git ref pinning (overrides the workspace-level remote default)
    reference: Optional[Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]] = Field(
        None,
        description=(
            "Git ref override (branch, tag, or commit SHA) for this specific source. "
            "Takes precedence over the remote's default reference and any environment "
            "remote override. Only valid for git-based sources (repository + source_path)."
        ),
    )

    # Helm / ArgoCD chart registry fields
    chart_name: Optional[str] = Field(
        None,
        description="Helm chart name (e.g. 'authentik'). Required when using chart_repository.",
    )
    chart_version: Optional[str] = Field(
        None,
        description="Helm chart version (e.g. '2024.12.0'). Omit to use latest.",
    )
    chart_repository: Optional[str] = Field(
        None,
        description="Helm chart repository URL or OCI reference (e.g. 'https://charts.goauthentik.io' or 'oci://ghcr.io/org/charts').",
    )

    @model_validator(mode="after")
    def validate_source_mode(self) -> "SourceModel":
        """Ensure exactly one source mode is specified: git-based or chart-based."""
        has_git = self.repository is not None or self.source_path is not None
        has_chart = self.chart_repository is not None or self.chart_name is not None

        if not has_git and not has_chart:
            raise ValueError(
                "SourceModel requires either a git-based source (repository + source_path) "
                "or a chart-based source (chart_repository + chart_name)."
            )
        if has_git and has_chart:
            raise ValueError(
                "SourceModel cannot mix git-based (repository/source_path) and "
                "chart-based (chart_repository/chart_name) fields. Use one mode only."
            )
        if has_git and self.source_path is None:
            raise ValueError("source_path is required when repository is specified.")
        if has_chart and self.chart_name is None:
            raise ValueError("chart_name is required when chart_repository is specified.")
        if self.reference is not None and has_chart:
            raise ValueError(
                "SourceModel.reference is only valid for git-based sources, "
                "not chart-based sources (use chart_version instead)."
            )
        return self

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
            raise ValueError(f"Path cannot contain parent directory references (..). Got: {path_str}")

        # Normalize to forward slashes
        normalized = path_str.replace("\\", "/")

        # Remove leading/trailing slashes
        normalized = normalized.strip("/")

        return normalized


# Standard slot types for module deployments
STANDARD_SLOT_TYPES = {"main", "staging", "canary", "sidecar", "init"}


def check_unique_names(items: Sequence[str], label: str) -> None:
    """Raise ``ValueError`` if *items* contains duplicate values.

    Uses O(n) set-based detection instead of the O(n²) ``.count()`` pattern.
    The error message lists duplicates in sorted order for deterministic output.
    """
    seen: set[str] = set()
    dupes: set[str] = set()
    for item in items:
        if item in seen:
            dupes.add(item)
        seen.add(item)
    if dupes:
        raise ValueError(f"Duplicate {label}: {', '.join(sorted(dupes))}")


# Resource name validator function
def validate_platform_name(value: str) -> str:
    """
    Validate resource names to be lowercase alphanumeric with underscores and hyphens.
    Must start with a letter. Matches the PlatformName regex: ``^[a-z][a-z0-9_-]*$``.
    """
    if not re.match(r"^[a-z][a-z0-9_-]*$", value):
        raise ValueError(
            f"Name '{value}' must contain only lowercase letters, numbers, "
            f"underscores, and hyphens, and must start with a letter"
        )
    return value


# Validator for slot_type field with warning for non-standard values
def validate_slot_type(value: Optional[str]) -> Optional[str]:
    """
    Validate slot_type field.

    Accepts any PlatformName-compliant value (lowercase alphanumeric + underscores, starts with letter).
    Issues a warning if the value is not one of the standard slot types.

    Standard slot types:
    - main: Primary/production deployment
    - staging: Staging/pre-production deployment
    - canary: Canary deployment for gradual rollout
    - sidecar: Sidecar container in Kubernetes
    - init: Init container in Kubernetes

    Args:
        value: The slot_type value to validate

    Returns:
        The validated slot_type value

    Raises:
        ValueError: If value doesn't match PlatformName pattern
    """
    if value is None:
        return value

    # Validate against PlatformName pattern
    try:
        validate_platform_name(value)
    except ValueError as e:
        raise ValueError(f"slot_type must follow PlatformName rules: {e}") from e

    # Warn if not a standard slot type
    if value not in STANDARD_SLOT_TYPES:
        warnings.warn(
            f"slot_type '{value}' is not a standard value. "
            f"Standard values are: {', '.join(sorted(STANDARD_SLOT_TYPES))}. "
            f"Custom slot types are allowed but may not be supported by all provisioners.",
            UserWarning,
            stacklevel=3,
        )

    return value
