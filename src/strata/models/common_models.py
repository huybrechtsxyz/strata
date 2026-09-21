#!/usr/bin/env python3
"""Common models, enums, and reusable types for Strata v2."""

import ipaddress
import re
import warnings
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, RootModel, StringConstraints, field_validator, model_validator

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
    PROVIDER = "provider"
    RESOURCE = "resource"
    DNS = "dns"
    NETWORK = "network"
    FIREWALL = "firewall"
    MODULE = "module"
    NAMESPACE = "namespace"
    TOPOLOGY = "topology"
    WORKSPACE = "workspace"


# Enumeration of the *known* built-in provisioner/deployer tools — a reference
# set for internal classification only, NOT used directly as any schema
# field's type. v1's real `DeployerFactory` supports user-registered
# provisioner plugins beyond these built-ins (`.strata/provisioners/*.py`),
# so fields like `Module.spec.type` are open `PlatformName` strings, checked
# against this enum only to classify *recognized* values (see
# `validate_type_is_workload_deployer()` in module_model.py) — an
# unrecognized value is a possible custom plugin, not a hard error. Subsets
# of this vocabulary that mean something different in different contexts are
# expressed as named, documented constants below — never as a second,
# overlapping enum (ADR-0011).
class ProvisionerType(str, Enum):
    """Reference enumeration of known built-in provisioner/deployer tools.

    Not used as a schema field's type — see module docstring above.
    """

    TERRAFORM = "terraform"
    OPENTOFU = "opentofu"
    ANSIBLE = "ansible"
    BICEP = "bicep"
    SCRIPT = "script"
    HELM = "helm"
    COMPOSE = "compose"
    ARGOCD = "argocd"
    FLUX = "flux"


# Sync/GitOps tools: render from the platform artifact and commit to a git
# remote at deploy time — no IaC source directory needed, unlike the other
# ProvisionerType members.
SYNC_PROVISIONER_TYPES = frozenset({ProvisionerType.ARGOCD, ProvisionerType.FLUX})

# OpenTofu is a drop-in, backend-compatible fork of Terraform (same IaC
# semantics, same state-backend config shape) — treated identically to
# TERRAFORM wherever terraform-specific validation applies (e.g.
# `validate_backend_only_for_terraform` in provisioning_model.py).
TERRAFORM_COMPATIBLE_TYPES = frozenset({ProvisionerType.TERRAFORM, ProvisionerType.OPENTOFU})

# Tools that can deploy a Module's services (containers/sub-charts). Used to
# classify a *recognized* `ModuleSpecModel.type` value — terraform/ansible/bicep
# manage infrastructure state, not container workloads, so a known value in
# this category is rejected there; an unrecognized value (custom plugin)
# isn't checked against this at all.
WORKLOAD_DEPLOYER_TYPES = frozenset(
    {ProvisionerType.HELM, ProvisionerType.COMPOSE, ProvisionerType.ARGOCD, ProvisionerType.SCRIPT}
)


# Enumeration of supported workspace versions
class PlatformVersion(str, Enum):
    """Enumeration of supported platform versions."""

    v2 = "strata.huybrechts.xyz/v2"
    v2_omp = "strata.omp.com/v2"


# Canonical API version for all new YAML documents
CANONICAL_API_VERSION = PlatformVersion.v2


# Value binding token syntax (ADR-0002): a field may be a plain literal, or
# contain one or more embedded `${var:KEY}`/`${secret:KEY}`/`${feature:KEY}`
# tokens (e.g. a composite/concatenated string like a connection string).
# Reused verbatim from v1 (ADR-0075) rather than `{{ }}`-style templating,
# since `{{` collides with strata's existing Jinja/Helm templating elsewhere.
VALUE_TOKEN_KINDS = ("var", "secret", "feature")

VALUE_TOKEN_PATTERN = re.compile(r"\$\{(?P<kind>var|secret|feature):(?P<key>[A-Za-z0-9_.-]+)\}")

_VALUE_TOKEN_CANDIDATE_PATTERN = re.compile(r"\$\{[^}]*\}")


def validate_value_tokens(value: str) -> None:
    """Raise ``ValueError`` if `value` contains a malformed ``${...}`` token.

    Catches typos at schema time (Phase 1) — e.g. an unknown kind
    (``${vars:x}``), a missing key (``${var:}``), or a missing colon
    (``${var}``) — without needing an Environment to check keys against.
    Well-formed tokens (``${var:region}``, ``${secret:db_password}``,
    ``${feature:enable_x}``) and plain literals with no tokens are accepted.
    Whether the *key itself* is real (e.g. `region` is actually declared) is a
    Phase 2 check against a real Environment, not this function's job.
    """
    for candidate in _VALUE_TOKEN_CANDIDATE_PATTERN.findall(value):
        if not VALUE_TOKEN_PATTERN.fullmatch(candidate):
            kinds = "|".join(VALUE_TOKEN_KINDS)
            raise ValueError(f"Malformed Value token {candidate!r}. Expected '${{{kinds}:KEY}}', e.g. '${{var:region}}'.")


def has_value_tokens(value: str) -> bool:
    """Return True if `value` contains at least one ``${...}`` Value token.

    Used to decide whether a field's literal-only validation (e.g. CIDR format
    checking) should run at all — a token-bearing string can't be format-checked
    until it's resolved (build/deploy time), so callers should skip that
    validation when this returns True.
    """
    return bool(_VALUE_TOKEN_CANDIDATE_PATTERN.search(value))


def validate_cidr_or_token(value: str) -> None:
    """Validate a CIDR/IP-or-Value-binding string.

    Always checks Value-token syntax via `validate_value_tokens()`. If `value`
    has no tokens (a plain literal), also validates it's a well-formed IP
    network/address via `ipaddress.ip_network()` — a token-bearing value can't
    be format-checked until it's resolved (build/deploy time). Shared by
    `network_model.py` (subnet/address-space CIDRs) and `firewall_model.py`
    (rule source/destination IP or CIDR).
    """
    validate_value_tokens(value)
    if not has_value_tokens(value):
        try:
            ipaddress.ip_network(value, strict=False)
        except ValueError as e:
            raise ValueError(f"Invalid CIDR/IP: {value}") from e


def validate_no_path_traversal(value: str) -> None:
    """Raise ``ValueError`` if `value` is an absolute path or contains '..'.

    Guards against path traversal escaping a build/deploy output directory.
    Validation only — does not normalize/mutate `value` (callers that also
    want normalization, e.g. stripping slashes, should use
    `validate_relative_path` instead; callers where a trailing '/' is
    semantically meaningful, e.g. `ModuleFileModel.target`, should use this
    directly so that marker isn't stripped).
    """
    path_str = str(value)

    if path_str.startswith("/") or path_str.startswith("\\"):
        raise ValueError(f"Path must be relative, not absolute. Got: {path_str}")

    if len(path_str) >= 2 and path_str[1] == ":":
        raise ValueError(f"Path must be relative, not absolute. Got: {path_str}")

    if ".." in path_str:
        raise ValueError(f"Path cannot contain parent directory references (..). Got: {path_str}")


def validate_relative_path(value: str) -> str:
    """Validate that a path is relative and secure, and normalize it.

    Runs `validate_no_path_traversal()`, then normalizes backslashes to
    forward slashes and strips leading/trailing slashes. Used by `SourceModel`
    (`source_path`/`target_path`), where no trailing-slash convention applies.
    """
    validate_no_path_traversal(value)
    return str(value).replace("\\", "/").strip("/")


def validate_file_ref_no_traversal(value: str) -> None:
    """Raise ``ValueError`` if a file-reference string escapes its base directory.

    Handles the ``@reponame/...`` cross-repo reference convention: the
    ``@reponame`` segment itself isn't a filesystem path, so only the part
    after it is checked (e.g. ``@infra/../../etc/passwd`` is still rejected).
    A bare relative reference with no ``@`` prefix is checked as-is. Shared by
    `module_model.py`'s `ModuleFileModel` (`source`/`target`) and
    `namespace_model.py`'s `NamespaceModuleModel` (`file`) — any field naming
    a file/module reference that will be resolved relative to a repo or
    build/work directory should use this.
    """
    path_to_check = value
    if path_to_check.startswith("@"):
        _, _, path_to_check = path_to_check.partition("/")
    if path_to_check:
        validate_no_path_traversal(path_to_check)


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


def check_unique_names(items: list[str], label: str) -> None:
    """Raise ``ValueError`` if `items` contains duplicate values.

    Uses O(n) set-based detection instead of the O(n^2) `.count()` pattern.
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
