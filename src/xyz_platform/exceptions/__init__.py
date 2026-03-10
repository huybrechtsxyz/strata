#!/usr/bin/env python3
"""
===============================================================================
Script Name   : __init__.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Custom exceptions for xyz-platform
===============================================================================
"""

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
    FileNotFoundError as PlatformFileNotFoundError,
)

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
]
