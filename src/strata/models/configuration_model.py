#!/usr/bin/env python3
"""Pydantic model for solution-wide configuration validation.

This is a deliberately minimal slice of v1's `configuration_model.py` — v1's
real ConfigurationModel also covers security policy, topology component
definitions, path conventions, logging, manifest/output shaping, cost and drift
tracking, and change tracking. Those are ported only when the corresponding
v2 kind/feature that needs them is built (see ADR-0003).

Today this only models the **provider registry** (`spec.providers`, defined in
`config_provider_model.py`): the set of known provider types, their valid
regions, and their supported resource types — enough to unblock
`ProviderService`'s Phase 2 dynamic validation.
"""

from typing import Any

from pydantic import Field, model_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    check_unique_names,
)
from strata.models.config_provider_model import ConfigurationProviderModel


class ConfigurationSpecModel(PlatformBaseModel):
    """Configuration specification.

    Only `providers` is modeled so far — see module docstring for what v1 has
    that v2 is deliberately deferring.
    """

    providers: list[ConfigurationProviderModel] | None = Field(
        None, description="Registry of known provider types, their valid regions, and resource types"
    )

    @model_validator(mode="after")
    def validate_unique_provider_names(self) -> "ConfigurationSpecModel":
        """Validate that all provider names are unique."""
        if self.providers:
            check_unique_names([p.name for p in self.providers], "provider names in configuration")
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
