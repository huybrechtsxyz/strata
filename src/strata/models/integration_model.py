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

Built per ADR-0021 (the integration layer): `type`, `capabilities` (core,
closed, plus open `x-`-prefixed extensions — D9), `description`, `required`,
`enabled`, `authentication` (direct reuse of `AuthenticationModel`),
`transport` (open string — D1; enforced against the resolved class's own
`TRANSPORTS` at runtime, not schema time, since a bare document has no way
to know what class `type` will resolve to), `version` (a constraint string —
D4, replaces `ProvisionerModel.version`, which this ADR removes), `command`
(overrides the class's default executable — needed for a generic wrapper
whose binary isn't known until the document supplies it), `endpoints` (an
address, needed once `transport` is networked), and `lifecycle` (hook
scripts, restricted to `.py` — D11, see `IntegrationLifecyclePhaseModel`).

Still deferred, each a real v1 field with no v2 consumer yet: `properties`
(freeform passthrough — cheap but inert until something reads it) and the
`customsecret`/`customvariable`/... custom-type allowlist (modeling it would
mean inventing an arbitrary "supported custom wrapper types" set with
nothing concrete driving it — ADR-0021 D10 solves the same problem
differently, via installable entry points instead of a type vocabulary).

**`validation` (v1's CLI availability/min_version/max_version block) is not
merely deferred — it is rejected.** ADR-0021 D4 found the same fact modelled
in three places in v1 (`IntegrationModel.validation`,
`WorkspaceIacModel.version`, and the `WorkspaceIacModel.integration` binding
itself) with nothing arbitrating between them, and settled ownership on this
model's own `version` field. Reintroducing `validation` would recreate
exactly that conflict.

v1's runtime `Protocol` capability classes (`IVariableStore`, `ISecretStore`,
etc.) and `IntegrationFactory` registry are pure execution-layer code, not
schema — out of scope for this models-only rewrite entirely, not merely
deferred. ADR-0021 replaces both with `strata.integrations`.
"""

from typing import Any

from pydantic import Field, RootModel, field_validator

from strata.models.auth_models import AuthenticationModel
from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
    validate_script_file,
)

#: Extension prefix marking a capability strata does not dispatch on — an
#: integration's own concern, carried and never rejected as unknown. See
#: `validate_capabilities` and ADR-0021 D9.
_EXTENSION_CAPABILITY_PREFIX = "x-"

#: Extensions an Integration lifecycle hook may use. Narrower than the
#: general `SCRIPT_EXTENSIONS` (`.sh`/`.bash`/`.py`/`.ps1`/`.js`/`.mjs`/`.go`):
#: nothing anywhere dispatches a script to an interpreter yet — v1's only
#: executor hardcodes `python <script>` — so a `.ps1` hook would pass schema
#: validation and then fail at run time. Widen this once an
#: extension -> interpreter map exists (ADR-0021 D11).
_INTEGRATION_SCRIPT_EXTENSIONS = frozenset({".py"})

#: Capability vocabulary v2 currently has real consumers for. v1's real set
#: has ~16 entries (azure/aws/gcloud CLI, identity, siem audit, cve scanner,
#: cost estimator, diagram render, etc.) — deliberately not ported wholesale;
#: extend only when a concrete v2 feature needs the capability (ADR-0003's
#: "minimal slice" policy). Closed/curated on purpose, unlike `type` below —
#: a capability is an abstract contract the codebase itself dispatches on,
#: not an arbitrary tool name (same reasoning as `STANDARD_SLOT_TYPES` being
#: closed while `Module.spec.type` stays open). An `x-`-prefixed capability
#: bypasses this vocabulary entirely (ADR-0021 D9) — this set is the *core*
#: tier only.
VALID_INTEGRATION_CAPABILITIES = frozenset(
    {
        "variables",  # VariableStoreModel-backed stores
        "secrets",  # SecretStoreModel-backed stores
        "features",  # FeatureStoreModel-backed stores
        "infrastructure",  # Provisioner.tool-backed IaC/CM tools
        # Distinct from "infrastructure" even though both map to the same
        # InfraIntegration ABC (ADR-0021 D1/D6) — the label says *what kind*
        # of thing is provisioned (v1's IContainerTool vs IInfrastructureTool
        # split), which matters for a human reading the document even where
        # the runtime contract doesn't distinguish them.
        "container",  # Compose/Helm-backed container tools
        # Named `sources`, not v1's `repository` (which maps to its
        # `IRepositoryTool` Protocol): a v2 remote covers git, OCI registries
        # and Helm chart indexes alike, so the narrower "repository" would be
        # as misleading here as `CONTAINER`/`GITOPS` were on `RemoteType`.
        "sources",  # SolutionRemoteModel-backed artifact sources (git/oci/helm auth)
    }
)


class IntegrationSpecModel(PlatformBaseModel):
    """Integration specification: what external tool/service this is and what it can do.

    `type` is an open `PlatformName` string (matches `ProvisionerModel.tool`'s
    pattern, ADR-0011) — describes a *specific* tool/service (terraform,
    vault, azure-keyvault, bitwarden, git, or a custom plugin name), an
    unbounded set. `capabilities` is mostly closed (see
    `VALID_INTEGRATION_CAPABILITIES`) but accepts an open `x-`-prefixed
    extension tier (ADR-0021 D9).
    """

    type: PlatformName = Field(
        description="Integration type (e.g. terraform, vault, azure-keyvault, bitwarden, git, or a custom name)"
    )
    capabilities: set[str] = Field(
        default_factory=set,
        description="Capabilities this integration provides — core (see VALID_INTEGRATION_CAPABILITIES) or an "
        "'x-'-prefixed extension strata does not dispatch on",
    )
    description: str | None = Field(None, description="Human-readable description of the integration")
    required: bool = Field(False, description="Whether this integration is required for platform operation")
    enabled: bool = Field(True, description="Whether this integration is enabled")
    authentication: AuthenticationModel | None = Field(
        None, description="Authentication configuration for accessing the integration"
    )
    transport: str | None = Field(
        None,
        description="How this integration is reached (e.g. 'cli', 'http', 'sdk'). An open string, checked against "
        "the resolved integration class's own supported transports at runtime — not here, since a bare document "
        "has no way to know what class 'type' will resolve to. Omitted lets the class choose (ADR-0021 D1).",
    )
    version: str | None = Field(
        None,
        description="Tool/service version this integration expects (e.g. '~> 1.9', '>= 1.17'). An assertion, not "
        "an install instruction: strata never installs software, so this is checked at preflight and fails on a "
        "mismatch. The single owner of this fact (ADR-0021 D4) — a Provisioner names an Integration rather than "
        "asserting its own version.",
    )
    command: str | None = Field(
        None,
        description="Overrides the integration class's default executable name. Needed for a generic/custom "
        "wrapper whose binary isn't known until the document supplies it; a built-in like terraform does not "
        "need this.",
    )
    endpoints: "IntegrationEndpointsModel | None" = Field(
        None, description="Service endpoint. Required once 'transport' is networked; meaningless for a CLI-only "
        "integration.",
    )
    lifecycle: "IntegrationLifecycleModel | None" = Field(
        None, description="Hook scripts keyed by phase name (e.g. 'connect_before', 'teardown'). Restricted to "
        "Python scripts — see IntegrationLifecyclePhaseModel.",
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
        """Validate every core capability name; accept any 'x'-prefixed extension unchecked."""
        core = {name for name in v if not name.startswith(_EXTENSION_CAPABILITY_PREFIX)}
        invalid = core - VALID_INTEGRATION_CAPABILITIES
        if invalid:
            raise ValueError(
                f"Invalid capability names: {sorted(invalid)}. "
                f"Valid capabilities: {sorted(VALID_INTEGRATION_CAPABILITIES)}, or an 'x-'-prefixed extension."
            )
        return v


class IntegrationEndpointsModel(PlatformBaseModel):
    """A service endpoint address for a networked integration transport."""

    address: str = Field(min_length=1, description="Service endpoint URL (e.g. https://consul.example.com:8500)")


class IntegrationLifecyclePhaseModel(PlatformBaseModel):
    """One lifecycle phase: a description plus the Python scripts it runs.

    Deliberately its own small model rather than reusing `ScriptsModel`/
    `CommonLifecycleModel` (used by deployment/module/namespace/provider/
    resource/workspace): those accept every `SCRIPT_EXTENSIONS` entry, but
    nothing dispatches a script to an interpreter yet except Python (see
    module docstring), so an Integration hook is restricted at the field
    level instead. Also drops `ScriptPathModel`'s `scope`/`priority`/`target`
    — those describe running once per resource/deployment instance, which
    has no meaning for a hook attached to an external-system connection.
    """

    description: str | None = Field(None, description="Optional description for documentation purposes")
    scripts: list[str] | None = Field(
        None, description="Python script paths for this phase, relative to the solution root"
    )

    @field_validator("scripts")
    @classmethod
    def validate_scripts(cls, v: list[str] | None) -> list[str] | None:
        """Validate each path (see `validate_script_file`), restricted to `.py`."""
        for item in v or []:
            validate_script_file(item, allowed=_INTEGRATION_SCRIPT_EXTENSIONS)
        return v


class IntegrationLifecycleModel(RootModel[dict[str, IntegrationLifecyclePhaseModel]]):
    """Lifecycle phases for an Integration, keyed by phase name.

    Open map (any phase name allowed), same shape as `CommonLifecycleModel`
    used elsewhere — only the phase's own script model differs (see
    `IntegrationLifecyclePhaseModel`).
    """

    root: dict[str, IntegrationLifecyclePhaseModel] = Field(default_factory=dict)


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
