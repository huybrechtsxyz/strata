#!/usr/bin/env python3
"""Pydantic model for namespace configuration validation."""

import warnings
from enum import Enum
from typing import Any

from pydantic import Field, model_validator

from strata.models.common_models import (
    CommonLifecycleModel,
    ModuleReferenceModel,
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
)
from strata.utils.names import check_unique_names


class NamespaceType(str, Enum):
    """Namespace ownership type — controls cross-layer overlap validation."""

    DEDICATED = "dedicated"
    SHARED = "shared"


class NamespaceSpecModel(PlatformBaseModel):
    """Model for namespace spec (type, lifecycle, modules).

    No ``references`` field (ADR-0002): scoping/typo-catching for any future
    Value bindings is a direct Phase 2 check against a real Environment, not
    an internal declared-keys list.
    """

    type: NamespaceType = Field(
        default=NamespaceType.DEDICATED,
        description="Namespace ownership type. 'dedicated' (default) means this namespace belongs "
        "to a single deployment layer — cross-layer overlap is flagged as a warning. "
        "'shared' means the namespace is intentionally used by multiple layers (e.g. "
        "kube-system, traefik) — cross-layer overlap is suppressed.",
    )
    lifecycle: CommonLifecycleModel | None = Field(None, description="Namespace lifecycle phases")
    modules: list[ModuleReferenceModel] | None = Field(None, description="List of modules in the namespace")

    @model_validator(mode="after")
    def validate_namespace_spec(self) -> "NamespaceSpecModel":
        """Namespace must have lifecycle and/or modules — empty namespaces are not allowed."""
        has_lifecycle = self.lifecycle is not None
        has_modules = self.modules is not None and len(self.modules) > 0

        if not has_lifecycle and not has_modules:
            raise ValueError(
                "Namespace must have either lifecycle configuration or modules (or both). "
                "Empty namespaces are not allowed."
            )

        if not has_modules:
            warnings.warn(
                "No modules specified for namespace. This namespace will only have lifecycle hooks.",
                UserWarning,
                stacklevel=2,
            )

        if self.modules:
            check_unique_names([m.name for m in self.modules], "module names")

        return self


class NamespaceMetaModel(PlatformBaseModel):
    """Namespace metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique namespace name")
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: list[Any] | None = Field(None, description="Optional list of tags")


class NamespaceModel(PlatformBaseModel):
    """Root model for a namespace resource."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2,
        frozen=True,
        description="API version for namespace configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.NAMESPACE,
        frozen=True,
        description="Platform kind (always 'namespace')",
    )
    meta: NamespaceMetaModel = Field(description="Namespace metadata (name, annotations, labels, tags)")
    spec: NamespaceSpecModel = Field(description="Namespace specification (type, lifecycle, modules)")
