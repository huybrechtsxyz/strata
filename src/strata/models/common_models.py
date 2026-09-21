#!/usr/bin/env python3
"""Common models, enums, and reusable types for Strata v2."""

import ipaddress
import re
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


# Enumeration of supported platform kinds
class PlatformKind(str, Enum):
    """Enumeration of supported platform kinds."""

    CONFIGURATION = "configuration"
    PROVIDER = "provider"
    RESOURCE = "resource"
    DNS = "dns"
    NETWORK = "network"
    FIREWALL = "firewall"


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
