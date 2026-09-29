#!/usr/bin/env python3
"""Pydantic model for solution-wide configuration validation.

This is a deliberately minimal slice of v1's `configuration_model.py` — v1's
real ConfigurationModel also covers path conventions, logging, manifest/
output shaping, cost and drift tracking, and change tracking. Those are
ported only when the corresponding v2 kind/feature that needs them is built
(see ADR-0003). `spec.security` (below) is the one exception ported ahead
of a specific consuming feature — real usage
(docs/_gap_v1.md gap #7) had no equivalent to derive it from elsewhere,
unlike `zones` (superseded by `TenantSpecModel.geographies` + `ProviderConfigRegionModel.geography`)
or `integrations`/`remotes` (already their own standalone kind/the solution
manifest, respectively).

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

from typing import Annotated, Any

from pydantic import Field, field_validator, model_validator

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


class ConfigurationSpecModel(PlatformBaseModel):
    """Configuration specification.

    Only `providers`/`topologies`/`security` are modeled so far — see module
    docstring for what v1 has that v2 is deliberately deferring.
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
