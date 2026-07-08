#!/usr/bin/env python3
"""Service layer exceptions."""

from typing import List, Optional

from strata.exceptions.base_exception import (
    PlatformError,
    PlatformNotFoundError,
    PlatformStateError,
)


class ServiceNotAvailableError(PlatformError):
    """Raised when a required service is not available."""

    def __init__(self, service_name: str, reason: Optional[str] = None):
        msg = f"Service '{service_name}' is not available"
        if reason:
            msg += f": {reason}"

        super().__init__(
            message=msg,
            error_code="SERVICE_NOT_AVAILABLE",
            details={"service": service_name, "reason": reason},
        )


class ServiceNotValidatedError(PlatformStateError):
    """Raised when attempting to use a service before validation."""

    def __init__(self, service_name: str, reason: Optional[str] = None):
        msg = reason or f"Service '{service_name}' must be validated before use. Call validate() first."
        super().__init__(
            message=msg,
            error_code="SERVICE_NOT_VALIDATED",
            details={"service": service_name},
        )


class ServiceLoadError(PlatformError):
    """Raised when a service fails to load."""

    def __init__(self, service_name: str, reason: str, cause: Optional[Exception] = None):
        super().__init__(
            message=f"Failed to load {service_name}: {reason}",
            error_code="SERVICE_LOAD_ERROR",
            details={"service": service_name, "reason": reason},
            cause=cause,
        )


class WorkspaceNotFoundError(PlatformNotFoundError):
    """Raised when a workspace file is not found."""

    def __init__(self, workspace_path: str):
        super().__init__(
            message=f"Workspace not found: {workspace_path}",
            error_code="WORKSPACE_NOT_FOUND",
            details={"path": workspace_path},
        )


class DeploymentNotFoundError(PlatformNotFoundError):
    """Raised when a deployment file is not found."""

    def __init__(self, deployment_path: str):
        super().__init__(
            message=f"Deployment not found: {deployment_path}",
            error_code="DEPLOYMENT_NOT_FOUND",
            details={"path": deployment_path},
        )


class ConfigurationNotFoundError(PlatformNotFoundError):
    """Raised when configuration is not found or not loaded."""

    def __init__(self, detail: Optional[str] = None):
        msg = "Platform configuration not found or not loaded"
        if detail:
            msg += f": {detail}"

        super().__init__(
            message=msg,
            error_code="CONFIGURATION_NOT_FOUND",
            details={"detail": detail},
        )


class ProviderNotFoundError(PlatformNotFoundError):
    """Raised when a provider is not found in configuration."""

    def __init__(self, provider_type: str, available: Optional[List[str]] = None):
        msg = f"Provider '{provider_type}' not found in configuration"
        if available:
            msg += f". Available: {', '.join(available)}"

        super().__init__(
            message=msg,
            error_code="PROVIDER_NOT_FOUND",
            details={"provider_type": provider_type, "available": available},
        )


class ResourceTypeNotFoundError(PlatformNotFoundError):
    """Raised when a resource type is not supported by provider."""

    def __init__(
        self,
        resource_type: str,
        provider_type: str,
        available: Optional[List[str]] = None,
    ):
        msg = f"Resource type '{resource_type}' not supported by provider '{provider_type}'"
        if available:
            msg += f". Available: {', '.join(available)}"

        super().__init__(
            message=msg,
            error_code="RESOURCE_TYPE_NOT_FOUND",
            details={
                "resource_type": resource_type,
                "provider_type": provider_type,
                "available": available,
            },
        )


class PlatformFileNotFoundError(PlatformNotFoundError):
    """Raised when a required file is not found."""

    def __init__(self, file_path: str, file_type: Optional[str] = None):
        msg = f"File not found: {file_path}"
        if file_type:
            msg = f"{file_type} file not found: {file_path}"

        super().__init__(
            message=msg,
            error_code="FILE_NOT_FOUND",
            details={"path": file_path, "file_type": file_type},
        )
