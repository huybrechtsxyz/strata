#!/usr/bin/env python3
"""Custom exceptions for xyz-platform."""

from .base_exception import (
    PlatformException,
    PlatformConfigurationError,
    PlatformNotFoundError,
    PlatformValidationError,
    PlatformStateError,
)

from .model_exception import (
    ModelValidationError,
    DuplicateNameError,
    InvalidReferenceError,
    UnsupportedKindError,
    SchemaVersionError,
)

from .service_exception import (
    ServiceNotAvailableError,
    ServiceNotValidatedError,
    WorkspaceNotFoundError,
    DeploymentNotFoundError,
    ConfigurationNotFoundError,
    ProviderNotFoundError,
    ResourceTypeNotFoundError,
    ServiceLoadError,
    PlatformFileNotFoundError,
)

from .path_exception import PathValidationError

__all__ = [
    # Base exceptions
    "PlatformException",
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
