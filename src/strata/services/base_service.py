#!/usr/bin/env python3
"""Base class for platform services with YAML loading and Pydantic validation."""

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Generic, List, Mapping, Optional, Tuple, Type, TypeVar, cast

import yaml
from pydantic import BaseModel, ValidationError

from strata.exceptions import (
    ModelValidationError,
    PlatformConfigurationError,
    PlatformFileNotFoundError,
    ServiceNotValidatedError,
)
from strata.logger import get_logger
from strata.models.configuration_model import ConfigurationModel
from strata.utils.system import resolve_path

ModelT = TypeVar("ModelT", bound=BaseModel)


class BaseService(ABC, Generic[ModelT]):
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
        from strata.utils.service_cache import get_cache_key, get_or_cache

        # Generate cache key
        cache_key = get_cache_key(cls, path)

        # Factory function for creating service
        def create_service():
            logger = get_logger(cls.__module__)
            logger.debug("Creating new service", service_class=cls.__name__, path=path)

            service = cls(path=path)

            if validate:
                is_valid, errors = service.validate()
                if not is_valid:
                    logger.warning(
                        "Service validation failed",
                        service_class=cls.__name__,
                        error_count=len(errors),
                        path=path,
                    )
                    # Note: Still return service even if invalid (don't cache)
                    # Caller can check service.is_validated()
                    service._errors.extend(errors)

            return service

        return get_or_cache(cache_key, create_service)

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None):
        self._errors: List[str] = []
        self._validation_exception: Optional[ModelValidationError] = None
        self.path = path
        self.data = data
        self.model: Optional[ModelT] = None
        self._validated = False
        self._repo_map: Optional[Dict[str, str]] = None
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
    def _get_model_class(self) -> Type[BaseModel]:
        """Return the Pydantic model class for validation."""
        raise NotImplementedError("Subclasses must implement _get_model_class()")

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

    def _validate_self(self) -> Tuple[bool, List[str]]:
        """
        Phase 1.5: Self-consistency checks that always run (no external dependencies).

        Called after Pydantic structural validation and before Phase 2 dynamic validation.
        Use this for intra-document consistency checks: cross-field references that are
        only resolvable within the same file (e.g. service depends_on entries must name
        real services in the same module).

        This is intentionally separate from _validate_dynamic so that Phase 1 structural
        validation (BaseService.load()) remains cheap and purely Pydantic-based, while
        intra-document checks are still always enforced without needing a configuration_model.

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)
        """
        return True, []

    # Validation

    def get_validation_errors(self) -> List[str]:
        """Return a copy of the accumulated validation error list."""
        return self._errors.copy()

    def get_structured_errors(self) -> List:
        """Return structured ValidationError instances from the most recent Pydantic validation.

        Converts the stored ``ModelValidationError`` (Phase 1) into a list of
        ``ValidationError`` dataclass instances.  Returns an empty list when Phase 1
        passed or when no structured error information is available.
        """
        from strata.models.validation_error import ValidationError

        if self._validation_exception is None:
            return []
        structured = []
        for err in self._validation_exception.details.get("errors", []):
            structured.append(
                ValidationError(
                    code="PYDANTIC_FIELD_ERROR",
                    message=err.get("message", ""),
                    phase=1,
                    field=err.get("field"),
                    context={"type": err.get("type")},
                )
            )
        return structured

    def validate(
        self,
        configuration_model: Optional["ConfigurationModel"] = None,
        work_path: Optional[str] = None,
        repo_map: Optional[Dict[str, str]] = None,
    ) -> Tuple[bool, List[str]]:
        """Validate the loaded data against the model.

        Args:
            configuration_model: Optional ConfigurationModel for cross-validation
            work_path: Optional working directory for validating file paths
            repo_map: Optional solution-level repo map for resolving @repo/... refs

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
                service_class=self.__class__.__name__,
            )
            model_class: Type[BaseModel] = self._get_model_class()
            self.model = cast(ModelT, model_class.model_validate(self.data))

            # Phase 1.5: always run self-consistency checks (no external dependencies).
            self_valid, self_errors = self._validate_self()
            if not self_valid:
                self.model = None
                self._validated = False
                self.logger.warning(
                    "Self-consistency validation failed",
                    service_class=self.__class__.__name__,
                    error_count=len(self_errors),
                )
                errors.extend(self_errors)
                return False, errors

            # Phase 2: run dynamic validation only when configuration_model is provided.
            if configuration_model:
                # Store solution repo_map so _validate_dynamic can access it via self._repo_map
                self._repo_map = repo_map
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
                        "Dynamic validation failed",
                        service_class=self.__class__.__name__,
                        error_count=len(dynamic_errors),
                    )
                    errors.extend(dynamic_errors)
                    return False, errors

            self._validated = True
            self.logger.debug(
                "Service validation successful",
                service_class=self.__class__.__name__,
                path=self.path if self.path else "in-memory",
            )
            # Call ready hook after successful validation
            self.on_ready()
            return True, []
        except ValidationError as e:
            # Reset model since validation failed
            self.model = None
            self._validated = False

            # Convert Pydantic errors to our format
            file_ref = f" [{self.path}]" if self.path else ""
            pydantic_errors = []
            for error in e.errors():
                field_path = " -> ".join(str(loc) for loc in error["loc"])
                error_msg = self._humanize_pydantic_error(error)
                error_type = error["type"]
                errors.append(f"Field '{field_path}': {error_msg} (type: {error_type}){file_ref}")
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
                file_path=self.path,
            )
            self.logger.error(
                "Validation failed",
                service_class=self.__class__.__name__,
                error_count=len(errors),
                path=self.path if self.path else "in-memory",
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
        return getattr(self.model, "kind", None) if self.model else None

    def get_name(self) -> Optional[str]:
        """Get the name from meta section."""
        self._ensure_validated()
        try:
            meta = getattr(self.model, "meta", None) if self.model else None
            return meta.name if meta else None
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
            meta = getattr(self.model, "meta", None) if self.model else None
            labels = getattr(meta, "labels", None) if meta else None
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
            meta = getattr(self.model, "meta", None) if self.model else None
            labels = getattr(meta, "labels", None) if meta else None
            if isinstance(labels, dict):
                return labels.get("version")
            return getattr(labels, "version", None)
        except (AttributeError, KeyError, TypeError):
            return None

    def get_lifecycle_phase(self, phase_name: Optional[str] = None):
        """
        Get a lifecycle phase model by name from the configuration.

        Args:
            phase_name: Name of the lifecycle phase (e.g., 'config_clean_before')

        Returns:
            ConfigurationLifecyclePhaseModel or None if not found
        """
        if not self.model:
            return None

        spec = getattr(self.model, "spec", None)
        if not spec:
            return None

        lifecycle = getattr(spec, "lifecycle", None)
        if not lifecycle:
            return None

        if phase_name is None:
            return lifecycle

        root = getattr(lifecycle, "root", None)
        return root.get(phase_name) if root and phase_name in root else None

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
            return self.validate(configuration_model=configuration_model, work_path=work_path)

        return True, []

    # Lifecycle Hooks Section
    def on_init(self) -> None:
        """Lifecycle hook: Called after __init__ completes.

        Override to perform initialization tasks after base initialization.
        """
        pass

    def on_ready(self) -> None:
        """Lifecycle hook: Called after validation succeeds.

        Override to perform tasks that require a validated model.
        """
        pass

    def on_shutdown(self) -> None:
        """Lifecycle hook: Called before cleanup/destruction.

        Override to perform cleanup tasks.
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

    @staticmethod
    def _humanize_pydantic_error(error: Mapping[str, Any]) -> str:
        """Translate cryptic Pydantic error messages to actionable human text."""
        error_type = error.get("type", "")
        ctx = error.get("ctx", {})

        if error_type == "string_pattern_mismatch":
            pattern = ctx.get("pattern", "")
            if pattern:
                return f"must match pattern '{pattern}' (lowercase letters, numbers, underscores, or hyphens)"
            return "must be lowercase letters, numbers, underscores, or hyphens"
        if error_type == "enum":
            expected = ctx.get("expected", "")
            if expected:
                return f"must be one of: {expected}"
        if error_type == "literal_error":
            expected = ctx.get("expected", "")
            if expected:
                return f"must be one of: {expected}"
        if error_type == "missing":
            return "this field is required"
        if error_type == "extra_forbidden":
            # Try to suggest the closest valid field name
            input_value = error.get("input")
            loc = error.get("loc", ())
            if loc:
                bad_field = str(loc[-1])
                return f"unexpected field '{bad_field}' (not allowed — check spelling or remove it)"
            return "unexpected field (not allowed)"

        return error.get("msg", str(error))

    def _load_data(self):
        """Load YAML data from the specified path."""
        if self.data is not None:
            self.logger.debug("Using provided data", service_class=self.__class__.__name__)
            return  # Data already provided
        if self.path is None:
            raise PlatformConfigurationError("Either path or data must be provided to load the service.")

        if os.path.exists(self.path):
            self.logger.debug("Loading YAML data", path=self.path)
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = yaml.safe_load(f) or {}
            self.logger.debug("Successfully loaded data", path=self.path)
        else:
            self.data = None
            self.logger.error("File not found", path=self.path)
            raise PlatformFileNotFoundError(self.path)

    def _ensure_validated(self):
        """Ensure the model has been validated before accessing properties."""
        if not self.model or not self._validated:
            raise ServiceNotValidatedError(self.__class__.__name__)

    def _resolve_file_path(
        self,
        file_ref: str,
        object_path: Optional[str] = None,
        repo_map: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Resolve a file reference to an absolute path.

        Handles plain relative/absolute paths and ``@repo_name/...`` cross-repo
        references (resolved via the supplied *repo_map*).

        Args:
            file_ref: File reference (relative, absolute, or @repo_name/... path)
            object_path: Object directory for resolving relative paths
            repo_map: Optional ``{repo_name: deploy_path}`` mapping for @-references

        Returns:
            Absolute path to the file
        """
        # Handle @repo_name/... cross-repo references
        if str(file_ref).startswith("@"):
            try:
                if object_path is None:
                    return str(file_ref)
                resolved = resolve_path(object_path, file_ref, repo_map=repo_map or {})
                return str(resolved)
            except ValueError:
                # repo not found — return as-is so the caller gets a clear file-not-found
                return str(file_ref)

        # If already absolute, return as-is
        file_path = Path(file_ref)
        if file_path.is_absolute():
            return str(file_path)

        # Try relative to object_path
        if object_path is None:
            return str(file_ref)
        resolved = resolve_path(object_path, file_ref)
        if resolved.exists():
            return str(resolved)

        # Return base_dir resolution even if it doesn't exist (let caller handle)
        return str(Path(object_path) / file_path)

    def _validate_file_refs(
        self,
        work_path: str,
        repo_map: Optional[Dict[str, str]],
        file_refs: List[Tuple[str, str]],
    ) -> List[str]:
        """
        Validate that file references resolve to existing files on disk.

        Handles plain relative/absolute paths and @repo_name/sub/path references.
        Skips silently when work_path is None.

        Args:
            work_path: Workspace root path for resolving relative paths
            repo_map: {repo_name: deploy_path} for resolving @repo_name/... refs
            file_refs: List of (label, file_str) pairs to check

        Returns:
            List[str]: Error messages for missing or unresolvable files
        """
        if not work_path:
            return []
        errors = []
        for label, file_str in file_refs:
            if not file_str:
                continue
            try:
                resolved = resolve_path(work_path, file_str, repo_map=repo_map)
                if not resolved.exists():
                    errors.append(f"{label}: file not found: '{file_str}' (resolved: {resolved})")
            except ValueError as exc:
                errors.append(f"{label}: {exc}")
        return errors
