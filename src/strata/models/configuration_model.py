#!/usr/bin/env python3
"""Pydantic model for solution-wide configuration validation.

This is a deliberately minimal slice of v1's `configuration_model.py` — v1's
real ConfigurationModel also covers logging, manifest/output shaping, cost
and drift tracking, and change tracking. Those are ported only when the
corresponding v2 kind/feature that needs them is built (see ADR-0003).
`spec.security`/`spec.paths`/`spec.audit` (below) are exceptions ported
ahead of a specific consuming feature — real usage (docs/design/gap_fit_v1.md gap #7)
had no equivalent to derive them from elsewhere, unlike `zones` (superseded
by `TenantSpecModel.geographies` + `ProviderConfigRegionModel.geography`)
or `integrations`/`remotes` (already their own standalone kind/the solution
manifest, respectively). `spec.paths` is narrower than v1's own version —
see docs/design/path-conventions.md for what's deliberately not ported
(the generic `validate:` sub-block, the full policy engine). `spec.audit`
is narrower still — see docs/design/audit-trail.md's "v2 Design Notes" for
what's deliberately not ported (the CLI-invocation journal, a durable local
append series, the full v1 event-type vocabulary).

`spec.providers`/`spec.topologies` are plain lists of ProviderConfig/
TopologyConfig document **names**, resolved by discovery against the
`(kind, meta.name)` index. v1 (and this codebase's own earlier pass)
embedded the full registry entries directly on `ConfigurationSpecModel`,
which doesn't scale: a platform with many provider/topology types would need
one shared, ever-growing file with no per-type ownership or reviewable
diffs. Promoted to standalone kinds for the same reason `Integration`/
`Topology` were (see their own docstrings/ADR-0011) — each provider/topology
type gets its own file.

They were briefly `{name, file}` pointers; the `file` half is gone because
it conflated identity with location and forced `name` to duplicate the
target's own `meta.name` (see `workspace_model.py`'s module docstring for
the full reasoning).

`spec.remotes` is NOT here — it lives on the solution manifest
(`solution_model.py`, `strata.yaml`). Bootstrap ordering forces it: v1's own
`solution.json` registers a `config` repository, i.e. Configuration itself
can live in a remote, so remotes must resolve before Configuration loads.
Configuration holds platform *policy*; the solution manifest holds
*composition*.
"""

from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator

from strata.models.audit_model import AuditConfigModel
from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
)
from strata.models.reference_fields import References
from strata.utils.names import check_unique_names


class ConfigurationSecurityModel(PlatformBaseModel):
    """Allow-lists restricting which store types a `variable`/`secret`/`feature`
    may declare, platform-wide.

    Ported from v1's real, active usage (`config/stores.yaml`'s
    `spec.security`, cfg-int-deployment) — ADR-0020/gap #7 originally
    deferred this alongside `zones`/`remotes`/`policies`/`audit`/`paths`, but
    unlike those, `security` has no v2 equivalent elsewhere to derive it
    from (compare `zones`, superseded by `TenantSpecModel.geographies`).

    Each field is `None` (no restriction — every store type recognized by
    the corresponding `StoreType` enum is allowed) or a closed allow-list —
    there is no `additional_*_stores: bool` escape hatch, since v1's own
    real usage is already a strict allow-list ("Anything not listed here is
    rejected") with no such toggle. A builtin store type (`constant`,
    `environment`, ...) still needs to appear in the list to be allowed —
    v1's real `config/stores.yaml` deliberately excludes `environment`
    platform-wide this way, proving builtins are not implicitly exempt.
    """

    allowed_secret_stores: list[str] | None = Field(
        None,
        description="Secret store types (SecretStoreType values, e.g. 'azure-keyvault') permitted in any "
        "`kind: environment` document. None means unrestricted.",
    )
    allowed_variable_stores: list[str] | None = Field(
        None,
        description="Variable store types (VariableStoreType values, e.g. 'azure-appconfig') permitted in "
        "any `kind: environment` document. None means unrestricted.",
    )
    allowed_feature_stores: list[str] | None = Field(
        None,
        description="Feature store types (FeatureStoreType values, e.g. 'azure-appconfig') permitted in any "
        "`kind: environment` document. None means unrestricted.",
    )


class PathSegmentModel(PlatformBaseModel):
    """One named capture within a `resolves: layers` `PathConventionModel.pattern`.

    `pattern` is a static regex only — it cannot look up a value from
    elsewhere in the model (docs/design/path-conventions.md's own
    'Deliberately out of scope': a dynamic cross-check needs a dedicated
    function, e.g. `_check_providers()`'s region check, not a generic
    expression mechanism here).
    """

    name: str = Field(description="Segment name, matching a {name} capture in pattern or filename_pattern")
    pattern: str = Field(description="Regex the captured value must match, e.g. '^[a-z]{3}$' for a 3-letter ring")
    description: str | None = Field(None, description="What this segment represents")


class PathConventionModel(PlatformBaseModel):
    """One directory-family naming convention (docs/design/path-conventions.md,
    docs/design/gap_fit_v1.md gap #7).

    Ported from v1's real, active `enforce-path-conventions` policy
    (`config/paths.yaml`, cfg-int-deployment) — structural shape only
    (does a document's real file path match `pattern`/`filename_pattern`,
    under `scope`). `pattern` and `filename_pattern` are independently
    optional matches against two different substrings of a document's real
    relative path (directory vs. filename, split on the final '/') — not
    every real convention cares about the filename at all (v1's own real
    `hub-path`/`control-path` never mention it), and `provider-path`'s
    real capture (`{region}`) lives entirely in the filename with no
    directory capture at all, so one combined full-path field cannot
    cleanly express both shapes (docs/design/path-conventions.md's "Why
    pattern and filename_pattern are split"). v1's generic `validate:`
    sub-block (arbitrary YAML-expression/file-existence cross-checks) is
    deliberately not ported: both real instances of it are already covered
    by more precise v2 checks elsewhere (provider region membership,
    tenant reference existence) — see the design doc's "What's already
    solved differently" section.
    """

    name: PlatformName = Field(description="Convention name, referenced by DeploymentLayersModel.follows")
    scope: str = Field(
        description="Directory prefix this convention governs, e.g. 'deploy/hubs/**'. Deliberately narrower "
        "than a full glob engine — always '<relative-dir>/**'."
    )
    pattern: str = Field(
        description="Directory template with {segment} captures, e.g. 'deploy/hubs/{hub}/{spoke}/{customer}/"
        "{ring}/{environment}'. Matched against a document's containing directory only, relative to the "
        "solution root — the filename itself is a separate, independent match (see filename_pattern)."
    )
    filename_pattern: str | None = Field(
        None,
        description="Optional {segment}-capable template matched against just the filename (not the whole "
        "path), e.g. 'tenant.yaml' or '{region}.yaml'. None (default) accepts any filename under a directory "
        "that matches 'pattern' — not every real convention cares what the file is called.",
    )
    resolves: Literal["tenant", "layers"] | None = Field(
        None,
        description="'tenant': the single {code}-shaped capture (from 'pattern' or 'filename_pattern', "
        "whichever declares it) is cross-checked against the real Tenant document filed there (meta.name). "
        "'layers': this convention's segments are DeploymentLayersModel's resolution target — segments is "
        "required when this is set. None: structural shape check only.",
    )
    segments: list[PathSegmentModel] | None = Field(
        None, description="Per-segment name + validation pattern. Required when resolves == 'layers'."
    )
    enforcement: Literal["warn", "deny"] = Field(
        "warn",
        description="'warn' (default, matches the one real enabled policy today) reports a mismatch without "
        "failing validate; 'deny' fails it. Modeled directly here rather than through a separate policy "
        "document — v1's generic policy engine (deny/warn dispatch across many policy types) is not ported; "
        "path_convention is the one real, active type.",
    )

    @model_validator(mode="after")
    def validate_segments_required_for_layers(self) -> "PathConventionModel":
        """A `layers`-resolving convention with no declared segments can never be
        cross-checked against anything — same discipline as `VariableStoreModel`'s
        `store: artifact` requiring `field` (docs/design/path-conventions.md's
        Open Questions #1, resolved)."""
        if self.resolves == "layers" and not self.segments:
            raise ValueError(f"Path convention '{self.name}': 'resolves: layers' requires 'segments' to be set.")
        return self


class ConfigurationSpecModel(PlatformBaseModel):
    """Configuration specification.

    Only `providers`/`topologies`/`security`/`paths`/`audit` are modeled so
    far — see module docstring for what v1 has that v2 is deliberately
    deferring.
    """

    properties: dict[str, Any] | None = Field(None, description="Optional additional properties for the configuration.")
    configuration: dict[str, Any] | None = Field(None, description="Optional configuration-specific properties.")
    custom: dict[str, Any] | None = Field(None, description="Optional custom properties for the configuration.")

    providers: list[Annotated[PlatformName, References(PlatformKind.PROVIDERCONFIG)]] | None = Field(
        None, description="Provider type registry: names of ProviderConfig documents"
    )
    additional_topologies: bool = Field(False, description="Allow topology types not listed in spec.topologies")
    topologies: list[Annotated[PlatformName, References(PlatformKind.TOPOLOGYCONFIG)]] | None = Field(
        None, description="Topology type registry: names of TopologyConfig documents"
    )
    security: ConfigurationSecurityModel | None = Field(
        None, description="Allow-lists restricting which store types a variable/secret/feature may declare."
    )
    paths: list[PathConventionModel] | None = Field(
        None, description="Directory-family naming conventions, checked structurally against real file paths."
    )
    audit: AuditConfigModel | None = Field(
        None, description="Deployment audit trail: local manifest/metrics + optional git/SIEM distribution."
    )

    @model_validator(mode="after")
    def validate_unique_provider_names(self) -> "ConfigurationSpecModel":
        """Validate that all provider names are unique."""
        if self.providers:
            check_unique_names(self.providers, "provider names in configuration")
        return self

    @model_validator(mode="after")
    def validate_unique_topology_names(self) -> "ConfigurationSpecModel":
        """Validate that all topology type names are unique."""
        if self.topologies:
            check_unique_names(self.topologies, "topology type names in configuration")
        return self

    @model_validator(mode="after")
    def validate_unique_path_convention_names(self) -> "ConfigurationSpecModel":
        """Validate that all path convention names are unique."""
        if self.paths:
            check_unique_names([p.name for p in self.paths], "path convention names in configuration")
        return self


class ConfigurationMetaModel(PlatformBaseModel):
    """Metadata for the configuration model."""

    name: PlatformName = Field(description="Unique name for the configuration resource.")
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(None, description="Labels for categorization and filtering.")
    tags: list[Any] | None = Field(None, description="Optional list of tags.")


class ConfigurationModel(PlatformBaseModel):
    """Root model for a configuration file."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2,
        frozen=True,
        description="API version of the configuration model.",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.CONFIGURATION,
        frozen=True,
        description="Platform kind: always 'configuration'.",
    )
    meta: ConfigurationMetaModel = Field(description="Metadata for the configuration model.")
    spec: ConfigurationSpecModel = Field(description="Specification for the configuration.")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.CONFIGURATION)
