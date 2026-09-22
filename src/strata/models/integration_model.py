#!/usr/bin/env python3
"""Pydantic models for external integration configuration.

Unlike v1, `Integration` is a **standalone kind** here — v1 embedded
`IntegrationModel` as a bare sub-model list directly on
`ConfigurationSpecModel.integrations`, with no `PlatformKind`/apiVersion of
its own (confirmed: v1's `PlatformKind` enum has no `"integration"` entry).
Promoted to a full kind here for the same reason Topology was promoted in
ADR-0011 — independently authorable/reusable, consistent with every other
kind Configuration/Workspace can reference by name+file, rather than a list
only reachable by editing Configuration directly.

Deliberately minimal slice of v1's real `IntegrationModel`/`capabilities.py`:
built now are `type`, `capabilities`, `description`, `required`, `enabled`,
`authentication` (direct reuse of `AuthenticationModel`). Deferred — each a
real v1 field, but with no v2 consumer yet: `validation` (CLI
availability/version check — nothing runs CLI checks in v2), `endpoints`
(remote-integration address — no remote consumer yet), `lifecycle` (no
hook-execution machinery), `properties` (freeform passthrough — cheap but
inert until something reads it), and the `customsecret`/`customvariable`/...
custom-type allowlist + capability-consistency validator (real v1
mechanism, but modeling it now would mean inventing an arbitrary "supported
custom wrapper types" set with nothing concrete driving it).

v1's runtime `Protocol` capability classes (`IVariableStore`, `ISecretStore`,
etc.) and `IntegrationFactory` registry are pure execution-layer code, not
schema — out of scope for this models-only rewrite entirely, not merely
deferred.
"""

from typing import Any

from pydantic import Field, field_validator

from strata.models.auth_models import AuthenticationModel
from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
)

#: Capability vocabulary v2 currently has real consumers for. v1's real set
#: has ~16 entries (azure/aws/gcloud CLI, identity, siem audit, cve scanner,
#: cost estimator, diagram render, etc.) — deliberately not ported wholesale;
#: extend only when a concrete v2 feature needs the capability (ADR-0003's
#: "minimal slice" policy). Closed/curated on purpose, unlike `type` below —
#: a capability is an abstract contract the codebase itself dispatches on,
#: not an arbitrary tool name (same reasoning as `STANDARD_SLOT_TYPES` being
#: closed while `Module.spec.type` stays open).
VALID_INTEGRATION_CAPABILITIES = frozenset(
    {
        "variables",  # VariableStoreModel-backed stores
        "secrets",  # SecretStoreModel-backed stores
        "features",  # FeatureStoreModel-backed stores
        "infrastructure",  # Provisioner.tool-backed IaC/CM tools
        "sources",  # SolutionRemoteModel-backed artifact sources (git/oci/helm auth)
    }
)


class IntegrationSpecModel(PlatformBaseModel):
    """Integration specification: what external tool/service this is and what it can do.

    `type` is an open `PlatformName` string (matches `ProvisionerModel.tool`'s
    pattern, ADR-0011) — describes a *specific* tool/service (terraform,
    vault, azure-keyvault, bitwarden, git, or a custom plugin name), an
    unbounded set. `capabilities` is the opposite: a *closed* vocabulary (see
    `VALID_INTEGRATION_CAPABILITIES`).
    """

    type: PlatformName = Field(
        description="Integration type (e.g. terraform, vault, azure-keyvault, bitwarden, git, or a custom name)"
    )
    capabilities: set[str] = Field(
        default_factory=set,
        description="Capabilities this integration provides — see VALID_INTEGRATION_CAPABILITIES",
    )
    description: str | None = Field(None, description="Human-readable description of the integration")
    required: bool = Field(False, description="Whether this integration is required for platform operation")
    enabled: bool = Field(True, description="Whether this integration is enabled")
    authentication: AuthenticationModel | None = Field(
        None, description="Authentication configuration for accessing the integration"
    )
    configuration: dict[str, Any] | None = Field(
        None,
        description="Raw passthrough configuration for this integration (e.g. SDK-specific setup not otherwise "
        "modeled). Not validated by strata, passed through as-is.",
    )
    custom: dict[str, Any] | None = Field(
        None, description="Custom user-defined data for scripts or extensions (e.g. becomes env vars)"
    )
    # No default_tags/custom_tags: an Integration describes a connection to an external
    # system, not a deployed/tagged cloud resource of its own.

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, v: set[str]) -> set[str]:
        """Validate that all capability names are recognized."""
        invalid = v - VALID_INTEGRATION_CAPABILITIES
        if invalid:
            raise ValueError(
                f"Invalid capability names: {sorted(invalid)}. "
                f"Valid capabilities: {sorted(VALID_INTEGRATION_CAPABILITIES)}"
            )
        return v


class IntegrationMetaModel(PlatformBaseModel):
    """Integration metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique integration name")
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: list[Any] | None = Field(None, description="Optional list of tags")


class IntegrationModel(PlatformBaseModel):
    """Root model for an integration configuration file."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2,
        frozen=True,
        description="API version for integration configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.INTEGRATION,
        frozen=True,
        description="Platform kind (always 'integration')",
    )
    meta: IntegrationMetaModel = Field(description="Integration metadata (name, annotations, labels, tags)")
    spec: IntegrationSpecModel = Field(
        description="Integration specification (type, capabilities, authentication, ...)"
    )

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.INTEGRATION)
