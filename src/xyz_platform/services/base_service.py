#!/usr/bin/env python3
"""
===============================================================================
Script Name   : base_service.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Base class for platform services with YAML loading and Pydantic validation.
===============================================================================
"""

import os
import yaml
from pathlib import Path
from pydantic import ValidationError
from typing import Optional, Tuple, List
from abc import ABC, abstractmethod

from xyz_platform.models.configuration_model import ConfigurationModel
from xyz_platform.utils.system import resolve_path
from xyz_platform.exceptions import (
    PlatformConfigurationError,
    ServiceNotValidatedError,
    ModelValidationError,
    PlatformFileNotFoundError,
)
from xyz_platform.logger import get_logger


class BaseService(ABC):
    """Base class for all platform services."""

    # Initialization

    @classmethod
    def load(cls, path: str, validate: bool = True):
        """Load a service from file with caching.

        Args:
            path: Path to service file
            validate: Whether to validate after loading

        Returns:
            Service instance (cached or new)
        """
        from xyz_platform.utils.service_cache import get_or_cache, get_cache_key

        # Generate cache key
        cache_key = get_cache_key(cls, path)

        # Factory function for creating service
        def create_service():
            logger = get_logger(cls.__module__)
            logger.debug(f"Creating new {cls.__name__}", extra={"path": path})

            service = cls(path=path)

            if validate:
                is_valid, errors = service.validate()
                if not is_valid:
                    logger.warning(
                        f"Service validation failed for {cls.__name__}",
                        extra={"error_count": len(errors), "path": path},
                    )
                    # Note: Still return service even if invalid (don't cache)
                    # Caller can check service.is_validated()

            return service

        return get_or_cache(cache_key, create_service)

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None):
        self.path = path
        self.data = data
        self.model = None
        self._validated = False
        self.logger = get_logger(self.__class__.__module__)
        self._load_data()
        # Call initialization hook
        self.on_init()

    def __del__(self):
        """Destructor: Ensures cleanup happens even if not explicitly called."""
        try:
            self.on_shutdown()
        except Exception:
            # Suppress errors during cleanup in destructor
            pass

    # Abstract methods for subclasses to implement

    @abstractmethod
    def _get_model_class(self):
        """Return the Pydantic model class for validation."""
        pass

    @abstractmethod
    def _validate_dynamic(
        self,
        configuration_model: Optional["ConfigurationModel"] = None,
        work_path: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Phase 2: Dynamic validation against configuration.

        This method performs business logic validation that requires dynamic configuration,
        such as validating provider types, regions, and resource types against the
        configuration model.

        Args:
            configuration_model: Optional ConfigurationModel for cross-validation
            work_path: Optional working directory for validating file paths

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)

        Note:
            This method should be overridden by subclasses that need dynamic validation.
            The default implementation returns success with no errors.
        """
        # Abstract implementation - override in subclasses for specific validation
        return True, []

    # Validation

    def validate(
        self,
        configuration_model: Optional["ConfigurationModel"] = None,
        work_path: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """Validate the loaded data against the model.

        Args:
            configuration_model: Optional ConfigurationModel for cross-validation
            work_path: Optional working directory for validating file paths

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)
        """
        errors = []
        if not self.data:
            error_msg = f"{self.__class__.__name__} data is empty or not loaded."
            self.logger.error(error_msg)
            errors.append(error_msg)
            return False, errors

        try:
            self.logger.debug(
                "Validating service data",
                extra={"service_class": self.__class__.__name__},
            )
            model_class = self._get_model_class()
            self.model = model_class.model_validate(self.data)

            if configuration_model:
                # Pass both configuration_model and work_path to _validate_dynamic
                # Use keyword arguments to allow subclasses to accept either parameter
                dynamic_valid, dynamic_errors = self._validate_dynamic(
                    configuration_model=configuration_model, work_path=work_path
                )
                if not dynamic_valid:
                    # Reset model since validation failed
                    self.model = None
                    self._validated = False
                    self.logger.warning(
                        f"{self.__class__.__name__} dynamic validation failed",
                        extra={"error_count": len(dynamic_errors)},
                    )
                    errors.extend(dynamic_errors)
                    return False, errors

            self._validated = True
            self.logger.debug(
                "Service validation successful",
                extra={
                    "service_class": self.__class__.__name__,
                    "path": self.path if self.path else "in-memory",
                },
            )
            # Call ready hook after successful validation
            self.on_ready()
            return True, []
        except ValidationError as e:
            # Reset model since validation failed
            self.model = None
            self._validated = False

            # Convert Pydantic errors to our format
            pydantic_errors = []
            for error in e.errors():
                field_path = " -> ".join(str(loc) for loc in error["loc"])
                error_msg = error["msg"]
                error_type = error["type"]
                errors.append(f"Field '{field_path}': {error_msg} (type: {error_type})")
                pydantic_errors.append(
                    {
                        "field": field_path,
                        "message": error_msg,
                        "type": error_type,
                    }
                )

            # Store structured error for programmatic access
            self._validation_exception = ModelValidationError(
                model_name=self.__class__.__name__,
                validation_errors=pydantic_errors,
            )
            self.logger.error(
                f"{self.__class__.__name__} validation failed",
                extra={
                    "error_count": len(errors),
                    "path": self.path if self.path else "in-memory",
                },
            )
            return False, errors

    def is_validated(self) -> bool:
        """
        Check if the service has been validated successfully.

        Returns:
            bool: True if validated, False otherwise
        """
        return self._validated and self.model is not None

    # Data Accessors

    def get_kind(self) -> Optional[str]:
        """Get the kind from root section."""
        self._ensure_validated()
        return self.model.kind if self.model else None

    def get_name(self) -> Optional[str]:
        """Get the name from meta section."""
        self._ensure_validated()
        try:
            return self.model.meta.name
        except AttributeError:
            return None

    def get_model(self):
        """Get the validated model instance."""
        self._ensure_validated()
        return self.model

    def get_label(self, label_key: str) -> Optional[str]:
        """Get a specific label from meta.labels section."""
        self._ensure_validated()
        try:
            labels = self.model.meta.labels
            if isinstance(labels, dict):
                return labels.get(label_key)
            return getattr(labels, label_key, None)
        except (AttributeError, KeyError, TypeError):
            return None

    def get_version(self) -> Optional[str]:
        """Get the version from meta.labels.version section."""
        self._ensure_validated()
        try:
            # return self.model.meta.labels.version
            # Works for both dict and object
            labels = self.model.meta.labels
            if isinstance(labels, dict):
                return labels.get("version")
            return getattr(labels, "version", None)
        except (AttributeError, KeyError, TypeError):
            return None

    def get_lifecycle_phase(self, phase_name: str = None):
        """
        Get a lifecycle phase model by name from the configuration.

        Args:
            phase_name: Name of the lifecycle phase (e.g., 'config_clean_before')

        Returns:
            ConfigurationLifecyclePhaseModel or None if not found
        """
        if not self.model:
            return None

        if not hasattr(self.model.spec, "lifecycle") or not self.model.spec.lifecycle:
            return None

        if phase_name is None:
            return self.model.spec.lifecycle

        return (
            self.model.spec.lifecycle.root.get(phase_name)
            if self.model.spec.lifecycle.root
            and phase_name in self.model.spec.lifecycle.root
            else None
        )

    def get_data(self) -> Optional[dict]:
        """
        Get the service's data as a dictionary.

        Uses model_dump() if model is validated, otherwise returns raw data.

        Returns:
            Dictionary representation of the service data, or None if no data

        Example:
            data = service.get_data()
            if data:
                # Modify data
                data['meta']['name'] = 'new_name'
                # Reload with modifications
                service.reload_data(data)
        """
        if self.model and self._validated:
            return self.model.model_dump()
        return self.data

    def reload_data(
        self,
        data: dict,
        validate: bool = True,
        configuration_model: Optional["ConfigurationModel"] = None,
        work_path: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Reload the service with new data and optionally validate.

        This method allows the WorkspaceController to merge configurations
        and reload services with the merged data.

        Args:
            data: New data dictionary to load
            validate: Whether to validate after loading (default: True)
            configuration_model: Optional ConfigurationModel for validation
            work_path: Optional working directory for validation

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)

        Example:
            # Get current data
            data = service.get_data()

            # Merge with overrides
            data['spec']['replicas'] = 3

            # Reload and validate
            success, errors = service.reload_data(data)
            if not success:
                print(f"Validation errors: {errors}")
        """
        # Reset validation state
        self._validated = False
        self.model = None

        # Update data
        self.data = data

        # Validate if requested
        if validate:
            return self.validate(
                configuration_model=configuration_model, work_path=work_path
            )

        return True, []

    # Lifecycle Hooks Section

    def on_init(self) -> None:
        """
        Lifecycle hook: Called after __init__ completes.

        Override this method to perform initialization tasks that should
        happen after the base initialization is complete.

        Example:
            def on_init(self):
                self.logger.info("Custom initialization")
                self._setup_connections()
        """
        pass

    def on_ready(self) -> None:
        """
        Lifecycle hook: Called after validation succeeds.

        Override this method to perform tasks that require a validated model,
        such as loading related resources or establishing connections.

        Example:
            def on_ready(self):
                self.logger.info("Service ready")
                self._load_dependencies()
        """
        pass

    def on_shutdown(self) -> None:
        """
        Lifecycle hook: Called before cleanup/destruction.

        Override this method to perform cleanup tasks such as:
        - Closing file handles
        - Removing temporary directories
        - Closing network connections
        - Saving state

        Example:
            def on_shutdown(self):
                self.logger.info("Cleaning up")
                if self._temp_dir:
                    shutil.rmtree(self._temp_dir)
        """
        pass

    def is_healthy(self) -> bool:
        """
        Health check for the service.

        Override this method to implement custom health checks.

        Returns:
            True if service is healthy and operational, False otherwise

        Example:
            def is_healthy(self) -> bool:
                return self._validated and self.model is not None
        """
        return self._validated

    # Base service utility methods

    def _load_data(self):
        """Load YAML data from the specified path."""
        if self.data is not None:
            self.logger.debug(
                "Using provided data", extra={"service_class": self.__class__.__name__}
            )
            return  # Data already provided
        if self.path is None:
            raise PlatformConfigurationError(
                "Either path or data must be provided to load the service."
            )

        if os.path.exists(self.path):
            self.logger.debug("Loading YAML data", extra={"path": self.path})
            with open(self.path, "r") as f:
                self.data = yaml.safe_load(f) or {}
            self.logger.debug("Successfully loaded data", extra={"path": self.path})
        else:
            self.data = None
            self.logger.error("File not found", extra={"path": self.path})
            raise PlatformFileNotFoundError(self.path)

    def _ensure_validated(self):
        """Ensure the model has been validated before accessing properties."""
        if not self.model or not self._validated:
            raise ServiceNotValidatedError(self.__class__.__name__)

    def _resolve_file_path(
        self, file_ref: str, object_path: Optional[str] = None
    ) -> str:
        """
        Resolve a file reference to an absolute path.

        Args:
            file_ref: File reference (relative or absolute path)
            object_path: Object directory for resolving relative paths

        Returns:
            Absolute path to the file
        """
        # If already absolute, return as-is
        file_path = Path(file_ref)
        if file_path.is_absolute():
            return str(file_path)

        # Try relative to platform_dir first
        resolved = resolve_path(object_path, file_ref)
        if resolved.exists():
            return str(resolved)

        # Return base_dir resolution even if it doesn't exist (let caller handle)
        return str(Path(object_path) / file_path)
