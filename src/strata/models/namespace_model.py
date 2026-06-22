#!/usr/bin/env python3
"""Pydantic model for namespace configuration validation."""

import warnings
from enum import Enum
from typing import Annotated, Any, Dict, List, Optional

from pydantic import (
    Field,
    StringConstraints,
    model_validator,
)

from strata.models.common_models import (
    CommonLifecycleModel,
    FeatureRefs,
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    SecretRefs,
    VariableRefs,
)


class NamespaceType(str, Enum):
    """Namespace ownership type — controls cross-layer overlap validation."""

    DEDICATED = "dedicated"
    SHARED = "shared"


class NamespaceReferenceModel(PlatformBaseModel):
    """
    References to variables, secrets, and features required by this namespace.

    Lists the keys that must be defined in the environment configuration.
    Actual values and store backends are defined at environment/workspace level.
    """

    variables: VariableRefs = Field(
        None,
        description="List of variable keys this namespace requires from environment",
    )
    secrets: SecretRefs = Field(None, description="List of secret keys this namespace requires from environment")
    features: FeatureRefs = Field(
        None,
        description="List of feature flag keys this namespace requires from environment",
    )


class NamespaceModuleModel(PlatformBaseModel):
    """Model for a namespace module (name, description, properties, lifecycle, labels, tags)."""

    name: PlatformName = Field(description="Unique module name within the namespace")
    description: Optional[Annotated[str, StringConstraints(strip_whitespace=True)]] = Field(
        None, description="Optional description of what this module provides"
    )
    file: str = Field(description="File reference for the module configuration or script")


class NamespaceSpecModel(PlatformBaseModel):
    """Model for namespace spec (lifecycle, modules, validation)."""

    type: NamespaceType = Field(
        default=NamespaceType.DEDICATED,
        description=(
            "Namespace ownership type. 'dedicated' (default) means this namespace belongs "
            "to a single deployment layer — cross-layer overlap is flagged as a warning. "
            "'shared' means the namespace is intentionally used by multiple layers (e.g. "
            "kube-system, traefik) — cross-layer overlap is suppressed."
        ),
    )
    lifecycle: Optional[CommonLifecycleModel] = Field(None, description="Namespace lifecycle phases")
    modules: Optional[List[NamespaceModuleModel]] = Field(None, description="List of modules in the namespace")
    references: Optional[NamespaceReferenceModel] = Field(
        None, description="Namespace references for variable and secret injection"
    )

    @model_validator(mode="after")
    def validate_namespace_spec(self) -> "NamespaceSpecModel":
        """Validate namespace specification."""
        # Ensure namespace has either lifecycle or modules (or both)
        has_lifecycle = self.lifecycle is not None
        has_modules = self.modules is not None and len(self.modules) > 0

        if not has_lifecycle and not has_modules:
            raise ValueError(
                "Namespace must have either lifecycle configuration or modules (or both). "
                "Empty namespaces are not allowed."
            )

        # Warn if no modules
        if not has_modules:
            warnings.warn(
                "No modules specified for namespace. This namespace will only have lifecycle hooks.",
                UserWarning,
                stacklevel=2,
            )

        # Validate unique module names
        if self.modules:
            module_names = [m.name for m in self.modules]
            duplicates = [name for name in module_names if module_names.count(name) > 1]
            if duplicates:
                raise ValueError(f"Duplicate module names found: {', '.join(set(duplicates))}")

        return self


class NamespaceMetaModel(PlatformBaseModel):
    """Model for namespace metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique namespace name")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional labels (key-value pairs for classification/filtering)",
    )
    tags: Optional[List[Any]] = Field(None, description="Optional list of tags")


class NamespaceModel(PlatformBaseModel):
    """Top-level model for a namespace resource."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version for namespace configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.NAMESPACE,
        frozen=True,
        description="Resource kind (always 'Namespace')",
    )
    meta: NamespaceMetaModel = Field(description="Namespace metadata (name, annotations, labels, tags)")
    spec: NamespaceSpecModel = Field(description="Namespace specification (lifecycle, modules, variables, secrets)")
