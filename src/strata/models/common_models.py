#!/usr/bin/env python3
"""Common models, enums, and reusable types for Strata v2."""

import warnings
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, RootModel, StringConstraints, field_validator, model_validator

from strata.utils.path_safety import validate_file_ref_no_traversal, validate_relative_path

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


# Enumeration of supported platform kinds
class PlatformKind(str, Enum):
    """Enumeration of supported platform kinds."""

    CONFIGURATION = "configuration"
    PROVIDERCONFIG = "providerconfig"
    TOPOLOGYCONFIG = "topologyconfig"
    PROVIDER = "provider"
    RESOURCE = "resource"
    DNS = "dns"
    NETWORK = "network"
    FIREWALL = "firewall"
    MODULE = "module"
    NAMESPACE = "namespace"
    TOPOLOGY = "topology"
    WORKSPACE = "workspace"
    INTEGRATION = "integration"


# Enumeration of supported workspace versions
class PlatformVersion(str, Enum):
    """Enumeration of supported platform versions."""

    v2 = "strata.huybrechts.xyz/v2"
    v2_omp = "strata.omp.com/v2"


# Canonical API version for all new YAML documents
CANONICAL_API_VERSION = PlatformVersion.v2


# Value binding token syntax (ADR-0002), CIDR/path-safety validation helpers,
# and `check_unique_names` all moved to `strata.utils` (`value_tokens.py`,
# `path_safety.py`, `names.py`) — pure functions with no Pydantic dependency,
# reused across many model files. See those modules for the full history/
# reasoning previously carried in this file's comments.


class SourceModel(PlatformBaseModel):
    """Reusable model for source configuration.

    Two modes (mutually exclusive, validated):
      1. Git-based: repository + source_path — used for Terraform modules, local charts, etc.
      2. Chart-based: chart_repository + chart_name — used for Helm/ArgoCD chart registry pulls.

    Example — git-based::

        source:
          repository: my-infra-repo
          source_path: terraform/modules/vpc
          target_path: build/vpc

    Example — Helm chart registry::

        source:
          chart_name: authentik
          chart_version: "2024.12.0"
          chart_repository: https://charts.goauthentik.io
    """

    repository: PlatformName | None = Field(
        None, description="Name of the repository from solution registered repositories (via strata repo add)"
    )
    source_path: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] | None = Field(
        None,
        description="Path to the source artifacts within the repository (relative path)",
    )
    target_path: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] | None = Field(
        None,
        description="Target path where artifacts should be built/deployed (relative to build/deploy directory)",
    )
    description: str | None = Field(None, description="Optional description for documentation purposes")

    # Git ref pinning (overrides the workspace-level remote default)
    reference: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] | None = Field(
        None,
        description=(
            "Git ref override (branch, tag, or commit SHA) for this specific source. "
            "Takes precedence over the remote's default reference and any environment "
            "remote override. Only valid for git-based sources (repository + source_path)."
        ),
    )

    # Helm / ArgoCD chart registry fields
    chart_name: str | None = Field(
        None,
        description="Helm chart name (e.g. 'authentik'). Required when using chart_repository.",
    )
    chart_version: str | None = Field(
        None,
        description="Helm chart version (e.g. '2024.12.0'). Omit to use latest.",
    )
    chart_repository: str | None = Field(
        None,
        description="Helm chart repository URL or OCI reference (e.g. 'https://charts.goauthentik.io' "
        "or 'oci://ghcr.io/org/charts').",
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
                "SourceModel.reference is only valid for git-based sources, not chart-based sources "
                "(use chart_version instead)."
            )
        return self

    @field_validator("source_path", "target_path")
    @classmethod
    def validate_source_target_path(cls, v: str | None) -> str | None:
        """Validate that paths are relative and secure (see `validate_relative_path`)."""
        if v is None:
            return v
        return validate_relative_path(v)


# Standard slot types for module deployments. Any PlatformName-compliant value
# is accepted; non-standard values just get a warning, not a rejection.
STANDARD_SLOT_TYPES = {"main", "staging", "canary", "sidecar", "init"}


def validate_slot_type(value: str | None) -> str | None:
    """Validate a `slot_type` field, warning (not rejecting) on non-standard values.

    Accepts any `PlatformName`-compliant value. Standard slot types:
    main (primary/production), staging, canary (gradual rollout), sidecar
    (Kubernetes sidecar container), init (Kubernetes init container). Custom
    values are allowed (may not be supported by every provisioner).
    """
    if value is not None and value not in STANDARD_SLOT_TYPES:
        warnings.warn(
            f"slot_type '{value}' is not a standard value. "
            f"Standard values are: {', '.join(sorted(STANDARD_SLOT_TYPES))}. "
            "Custom slot types are allowed but may not be supported by all provisioners.",
            UserWarning,
            stacklevel=3,
        )
    return value


class ModuleReferenceModel(PlatformBaseModel):
    """A pointer to a `Module` document, plus placement/override metadata.

    Shared by `namespace_model.py` (`NamespaceSpecModel.modules` — modules
    grouped under a container-orchestration namespace) and
    `topology_model.py` (`TopologyComponentModel.modules` — a module attached
    directly to one resource, e.g. Function App code onto its Function App,
    no orchestration namespace involved). Both are ultimately "a pointer to a
    Module document plus placement metadata" — one shared shape rather than
    two independently-drifting near-duplicates.
    """

    name: PlatformName = Field(description="Unique module reference name within its parent")
    file: str = Field(description="File reference to the module configuration (module YAML file)")
    description: str | None = Field(None, description="Optional description of what this module provides")
    slot_type: str | None = Field(
        "main",
        description="Deployment slot type: 'main' (primary/production), 'staging', 'canary', 'sidecar', 'init'. "
        "Defaults to 'main'. Custom values allowed but may generate warnings.",
    )
    enabled: bool = Field(default=True, description="Whether this module is enabled/deployed")
    configuration: dict[str, Any] | None = Field(
        None, description="Module-specific configuration overrides in this context"
    )

    @field_validator("file")
    @classmethod
    def validate_file_no_traversal(cls, v: str) -> str:
        """Reject absolute paths / '..' in `file` (see `validate_file_ref_no_traversal`)."""
        validate_file_ref_no_traversal(v)
        return v

    @field_validator("slot_type")
    @classmethod
    def validate_slot_type_value(cls, v: str | None) -> str | None:
        """Validate slot_type using the common validator."""
        return validate_slot_type(v)


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
