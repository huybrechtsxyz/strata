#!/usr/bin/env python3
"""Common models, enums, and reusable types for Strata v2."""

import warnings
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, RootModel, StringConstraints, field_validator, model_validator

from strata.utils.path_safety import validate_relative_path

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

    SOLUTION = "solution"
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
    TENANT = "tenant"
    ENVIRONMENT = "environment"
    DEPLOYMENT = "deployment"
    VERSION = "version"


def validate_kind_matches(value: "PlatformKind", expected: "PlatformKind") -> "PlatformKind":
    """Reject a document whose `kind:` doesn't match the model it's being validated as.

    `kind` fields are declared `frozen=True` with a fixed `default=`, but that
    only blocks *reassignment after construction* — Pydantic still accepts any
    valid `PlatformKind` value supplied at parse time (e.g. a YAML file with
    `kind: firewall` validates fine against `NetworkModel`, silently taking on
    the wrong kind). Every root model's `kind` field must call this from a
    `field_validator` with its own expected kind.
    """
    if value != expected:
        raise ValueError(f"Expected kind '{expected.value}', got '{value.value}'")
    return value


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

    One field names *which remote* (`remote`); the selection field that
    accompanies it decides the mode:

      1. Git-based: ``remote`` + ``source_path`` — Terraform modules, local charts, etc.
         ``remote`` may be omitted to mean "this solution's own repository".
      2. Chart-based: ``remote`` + ``chart_name`` — Helm/ArgoCD chart registry pulls.
         ``remote`` is required; a chart always comes from a registry.

    v1 (and v2's own earlier pass) had two fields that both answered "which
    remote" — `repository` for git and `chart_repository` for charts —
    differing only in what you selected afterwards. Collapsed for the same
    reason `ModuleReferenceModel` was: one shape, not two near-duplicates.
    `chart_repository` additionally held a raw URL, so chart sources were the
    last place in the schema that could not be redirected to an internal
    mirror by editing one declaration. Mirrors Flux's single `sourceRef`,
    which spans Git/OCI/Helm repositories alike.

    Example — git-based::

        source:
          remote: my-infra-repo
          source_path: terraform/modules/vpc
          target_path: build/vpc

    Example — Helm chart registry::

        source:
          remote: goauthentik
          chart_name: authentik
          chart_version: "2024.12.0"
    """

    remote: PlatformName | None = Field(
        None,
        description="Name of a remote declared in the solution manifest's spec.remotes (strata.yaml). The "
        "remote owns the URL, the git/OCI ref and the credentials; this only selects which one to take "
        "from. Required for chart-based sources; optional for git-based ones, where omitting it means "
        "this solution's own repository.",
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

    # No `reference` field: a git/OCI ref is declared once, on the remote
    # (`SolutionRemoteModel.reference`), and never overridden per use
    # site. Allowing an override here would let two modules silently pull
    # different trees of the same repository, and makes "what version is
    # deployed?" answerable only by scanning every source in the solution.
    # Same call Flux/Bazel/Nix make (ref lives on the source declaration);
    # Helm's per-chart `chart_version` below is NOT an exception — a chart
    # repo index legitimately serves many versions, so picking one is
    # selection, not remote identity.

    # Helm / ArgoCD chart registry fields
    chart_name: str | None = Field(
        None,
        description="Helm chart name (e.g. 'authentik'). Selects chart-based mode; requires `remote`.",
    )
    chart_version: str | None = Field(
        None,
        description="Helm chart version (e.g. '2024.12.0'). Omit to use latest. Only valid in chart-based "
        "mode. Unlike a git ref, this is NOT remote identity — a chart index legitimately serves many "
        "versions, so picking one is selection and belongs here rather than on the remote.",
    )

    @model_validator(mode="after")
    def validate_source_mode(self) -> "SourceModel":
        """Ensure exactly one source mode is selected, with the fields that mode needs.

        The mode comes from the *selection* field (`source_path` vs
        `chart_name`), not from `remote` — `remote` is mode-agnostic, so one
        OCI registry can serve charts in one source and plain artifacts in
        another. Whether the named remote's `type` actually matches the mode
        needs the loaded solution manifest, so it is a Phase 2 check (ADR-0003).
        """
        has_git = self.source_path is not None
        has_chart = self.chart_name is not None

        if not has_git and not has_chart:
            raise ValueError(
                "SourceModel requires either a git-based source (source_path) "
                "or a chart-based source (chart_name)."
            )
        if has_git and has_chart:
            raise ValueError(
                "SourceModel cannot mix git-based (source_path) and chart-based (chart_name) "
                "selection. Use one mode only."
            )
        if has_chart and self.remote is None:
            raise ValueError("remote is required for chart-based sources (a chart comes from a registry).")
        if has_git and self.chart_version is not None:
            raise ValueError("chart_version is only valid for chart-based sources.")
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

    `name` is the reference's own name within its parent; `module` names the
    Module document it points at (resolved by discovery). Same instance/class
    split as `WorkspaceResourceModel.name`/`.resource` — one Module can be
    attached several times under different reference names.
    """

    name: PlatformName = Field(description="Unique module reference name within its parent")
    module: PlatformName = Field(
        description="Name of the Module document this reference points at (its meta.name, resolved by discovery)"
    )
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
