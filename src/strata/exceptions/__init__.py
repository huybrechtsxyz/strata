#!/usr/bin/env python3
"""Custom exceptions for strata."""

from .base_exception import (
    PlatformConfigurationError,
    PlatformError,
    PlatformNotFoundError,
    PlatformStateError,
    PlatformValidationError,
)
from .model_exception import (
    DuplicateNameError,
    InvalidReferenceError,
    ModelValidationError,
    SchemaVersionError,
    UnsupportedKindError,
)
from .path_exception import PathValidationError
from .service_exception import (
    ConfigurationNotFoundError,
    DeploymentNotFoundError,
    PlatformFileNotFoundError,
    ProviderNotFoundError,
    ResourceTypeNotFoundError,
    ServiceLoadError,
    ServiceNotAvailableError,
    ServiceNotValidatedError,
    WorkspaceNotFoundError,
)

__all__ = [
    # Base exceptions
    "PlatformError",
    "PlatformConfigurationError",
    "PlatformNotFoundError",
    "PlatformValidationError",
    "PlatformStateError",
    # Model exceptions
    "ModelValidationError",
    "DuplicateNameError",
    "InvalidReferenceError",
    "UnsupportedKindError",
    "SchemaVersionError",
    # Service exceptions
    "ServiceNotAvailableError",
    "ServiceNotValidatedError",
    "WorkspaceNotFoundError",
    "DeploymentNotFoundError",
    "ConfigurationNotFoundError",
    "ProviderNotFoundError",
    "ResourceTypeNotFoundError",
    "ServiceLoadError",
    "PlatformFileNotFoundError",
    "PathValidationError",
]
