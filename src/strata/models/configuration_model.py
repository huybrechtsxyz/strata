#!/usr/bin/env python3
"""Pydantic model for solution-wide configuration validation.

This is a deliberately minimal slice of v1's `configuration_model.py` — v1's
real ConfigurationModel also covers security policy, path conventions,
logging, manifest/output shaping, cost and drift tracking, and change
tracking. Those are ported only when the corresponding v2 kind/feature that
needs them is built (see ADR-0003).

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

from typing import Any

from pydantic import Field, field_validator, model_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
)
from strata.utils.names import check_unique_names


class ConfigurationSpecModel(PlatformBaseModel):
    """Configuration specification.

    Only `providers`/`topologies` are modeled so far — see module docstring
    for what v1 has that v2 is deliberately deferring.
    """

    properties: dict[str, Any] | None = Field(
        None, description="Optional additional properties for the configuration."
    )
    configuration: dict[str, Any] | None = Field(
        None, description="Optional configuration-specific properties."
    )
    custom: dict[str, Any] | None = Field(
        None, description="Optional custom properties for the configuration."
    )

    providers: list[PlatformName] | None = Field(
        None, description="Provider type registry: names of ProviderConfig documents"
    )
    additional_topologies: bool = Field(
        False, description="Allow topology types not listed in spec.topologies"
    )
    topologies: list[PlatformName] | None = Field(
        None, description="Topology type registry: names of TopologyConfig documents"
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
