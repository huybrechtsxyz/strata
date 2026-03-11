#!/usr/bin/env python3
"""
===============================================================================
Script Name   : configuration_service.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.9+
Description   : Configuration service class (Centralized Singleton pattern)
===============================================================================
"""

import threading
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from xyz_platform.models.configuration_model import ConfigurationModel
from xyz_platform.models.repository_model import RepositoryModel
from xyz_platform.services.base_service import BaseService
from xyz_platform.utils.configuration_loader import ConfigurationLoader
from xyz_platform.utils import config
from xyz_platform.utils.system import resolve_path


class ConfigurationService(BaseService):
    """Service for handling configuration configurations (Centralized Singleton pattern)."""

    _instances: Dict[str, "ConfigurationService"] = {}
    _lock = threading.Lock()

    @classmethod
    def _get_instance_key_static(cls, class_ref, *args, **kwargs) -> str:
        """Get instance key for singleton. Override for multiple instances."""
        return "default"

    def __new__(cls, *args, **kwargs):
        """Create or return existing singleton instance (thread-safe)."""
        instance_key = cls._get_instance_key_static(cls, *args, **kwargs)
        full_key = f"{cls.__name__}::{instance_key}"

        with cls._lock:
            if full_key not in cls._instances:
                instance = super().__new__(cls)
                instance._initialized = False
                cls._instances[full_key] = instance
            return cls._instances[full_key]

    def __init__(self):
        """Initialize configuration service with blank config (only once)."""
        if self._initialized:
            return
        # Initialize with empty data - no loading on init
        self.path = None  # ConfigurationService doesn't load from a single path
        self.data = {}
        self.model: Optional[ConfigurationModel] = None
        self._validated = False
        self._validation_errors = []
        # Initialize logger
        from xyz_platform.logger import get_logger

        self.logger = get_logger(self.__class__.__module__)
        self._initialized = True

    @classmethod
    def get_instance(cls) -> "ConfigurationService":
        """Get singleton instance."""
        return cls()

    @classmethod
    def reset(cls):
        """Reset all singleton instances (useful for testing)."""
        with cls._lock:
            cls._instances.clear()

    # Override BaseService methods

    def _get_model_class(self):
        """Return the ConfigurationModel class for validation."""
        return ConfigurationModel

    def _validate_dynamic(
        self,
        configuration_model: Optional["ConfigurationModel"] = None,
        work_path: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Phase 2: Dynamic validation against configuration.

        This validates the merged configuration across all loaded files.
        While MODEL validators check single files, this checks the merged result
        for duplicates that may occur when merging multiple configuration files.

        Validates:
        1. Unique provider names across merged configs
        2. Unique topology types across merged configs
        3. Unique regions within each provider (across merged configs)
        4. Unique resources within each provider (across merged configs)
        5. Unique component roles within each topology (across merged configs)

        Args:
            configuration_model: Optional ConfigurationModel for cross-validation

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)
        """
        errors = []

        if not self.model or not self.model.spec:
            return True, []

        # Validate unique provider names across merged configuration
        if self.model.spec.providers:
            provider_names = [p.name for p in self.model.spec.providers]
            duplicates = [
                name for name in provider_names if provider_names.count(name) > 1
            ]
            if duplicates:
                errors.append(
                    f"Duplicate provider names in merged configuration: {', '.join(set(duplicates))}"
                )

            # Validate unique regions and resources within each provider
            for provider in self.model.spec.providers:
                # Check unique regions
                if provider.regions:
                    region_names = []
                    for region in provider.regions:
                        if isinstance(region, dict) and "name" in region:
                            region_names.append(region["name"])
                        elif isinstance(region, str):
                            region_names.append(region)
                    duplicates = [
                        name for name in region_names if region_names.count(name) > 1
                    ]
                    if duplicates:
                        errors.append(
                            f"Duplicate regions in provider '{provider.name}' (merged config): {', '.join(set(duplicates))}"
                        )

                # Check unique resources
                if provider.resources:
                    resource_names = [res.name for res in provider.resources]
                    duplicates = [
                        name
                        for name in resource_names
                        if resource_names.count(name) > 1
                    ]
                    if duplicates:
                        errors.append(
                            f"Duplicate resources in provider '{provider.name}' (merged config): {', '.join(set(duplicates))}"
                        )

        # Validate unique topology types across merged configuration
        if self.model.spec.topologies:
            topology_types = [t.type for t in self.model.spec.topologies]
            duplicates = [
                ttype for ttype in topology_types if topology_types.count(ttype) > 1
            ]
            if duplicates:
                errors.append(
                    f"Duplicate topology types in merged configuration: {', '.join(set(duplicates))}"
                )

            # Validate unique component roles within each topology
            for topology in self.model.spec.topologies:
                if topology.components:
                    roles = [comp.role for comp in topology.components]
                    duplicates = [role for role in roles if roles.count(role) > 1]
                    if duplicates:
                        errors.append(
                            f"Duplicate component roles in topology '{topology.type}' (merged config): {', '.join(set(duplicates))}"
                        )

        return len(errors) == 0, errors

    # Loading methods

    def add_configurations(self, config_files: List[str]) -> Tuple[bool, List[str]]:
        """
        Add multiple configuration files to the service.
        Merges all files with existing configuration and replaces the model.

        Args:
            config_files: List of paths to configuration files to add

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)
        """
        loader = ConfigurationLoader()

        try:
            self.logger.info(
                "Adding configuration files", extra={"file_count": len(config_files)}
            )

            # Load and merge all new configuration files
            new_config = loader.load_and_merge_yaml_files(config_files)

            # Merge with existing data
            if self.data:
                # Deep merge: new_config takes precedence
                merged = loader.deep_merge(self.data, new_config)
                self.logger.debug("Merged with existing configuration")
            else:
                merged = new_config

            # Update internal state
            self.data = merged
            self._validated = False
            self._validation_errors = []
            self.model = None

            # Validate the merged configuration
            return self.validate()

        except Exception as e:
            error_msg = f"Failed to add configurations from {config_files}: {str(e)}"
            self.logger.error(
                error_msg,
                exc_info=True,
                extra={"config_files": config_files, "error_type": type(e).__name__},
            )
            return False, [error_msg]

    def add_configuration(self, config_file: str) -> Tuple[bool, List[str]]:
        """
        Add a single configuration file to the service.
        Merges with existing configuration and replaces the model.

        Args:
            config_file: Path to the configuration file to add

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)
        """
        return self.add_configurations([config_file])

    def load_from_paths(self, patterns: List[str]) -> Tuple[bool, List[str]]:
        """
        Load and merge configuration files from path patterns.

        Supports glob patterns, recursive patterns, and user home expansion.
        Examples:
            - "config/*.yaml"
            - "config/**/*.yaml"
            - "~/.xyz/config/*.yaml"
            - "/etc/xyz-platform/*.yaml"

        Args:
            patterns: List of file patterns (e.g., ["config/*.yaml", "~/.xyz/*.yaml"])

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)

        Example:
            >>> config_svc = ConfigurationService.get_instance()
            >>> success, errors = config_svc.load_from_paths([
            ...     "config/base.yaml",
            ...     "config/*.yaml",
            ...     "~/.xyz/*.yaml"
            ... ])
            >>> if success:
            ...     print("Configuration loaded")
        """
        from glob import glob

        loader = ConfigurationLoader()

        try:
            self.logger.debug(
                "Loading configurations from patterns",
                extra={"pattern_count": len(patterns), "patterns": patterns},
            )

            # Expand glob patterns to file paths
            file_paths = []
            for pattern in patterns:
                # Expand user home directory (~)
                expanded_pattern = str(Path(pattern).expanduser())

                # Use glob to find matching files
                matches = glob(expanded_pattern, recursive=True)

                if matches:
                    file_paths.extend(matches)
                    self.logger.debug(
                        "Pattern matched files",
                        extra={"pattern": pattern, "match_count": len(matches)},
                    )
                else:
                    self.logger.debug(
                        "Pattern matched no files", extra={"pattern": pattern}
                    )

            if not file_paths:
                error_msg = f"No configuration files found for patterns: {patterns}"
                self.logger.warning(error_msg)
                return False, [error_msg]

            # Load and merge all files
            merged = loader.load_and_merge_yaml_files(file_paths)

            # Merge with existing data if present
            if self.data:
                # Deep merge: new config takes precedence
                merged = loader.deep_merge(self.data, merged)
                self.logger.debug("Merged with existing configuration")

            # Update internal state
            self.data = merged
            self._validated = False
            self._validation_errors = []
            self.model = None

            self.logger.debug(
                "Configurations loaded from patterns",
                extra={"pattern_count": len(patterns), "file_count": len(file_paths)},
            )

            # Validate the merged configuration
            success, errors = self.validate()
            return success, errors

        except Exception as e:
            error_msg = (
                f"Failed to load configurations from patterns {patterns}: {str(e)}"
            )
            self.logger.error(
                error_msg,
                exc_info=True,
                extra={"patterns": patterns, "error_type": type(e).__name__},
            )
            return False, [error_msg]

    # Access methods

    def get_configuration(self) -> Optional[ConfigurationModel]:
        """Get the merged configuration data."""
        self._ensure_validated()
        return self.model if self.model else None

    def get_repositories(self) -> Optional[List[RepositoryModel]]:
        """Get the list of repositories from the configuration."""
        self._ensure_validated()
        if self.model and self.model.spec and self.model.spec.repositories:
            return self.model.spec.repositories
        return None

    # Get configuration defaults

    def get_configuration_defaults(self) -> Optional[Dict[str, Any]]:
        """Get configuration defaults from the configuration spec."""
        self._ensure_validated()
        return (
            self.model.spec.configuration
            if self.model and self.model.spec and self.model.spec.configuration
            else None
        )

    def get_default_build_path(
        self, work_path: str, create_path: bool
    ) -> Optional[Path]:
        """Get the default build path from configuration defaults."""
        build_path: str = None
        # Try to get from configuration if validated, otherwise use defaults
        if self.is_validated():
            defaults = self.get_configuration_defaults()
            if defaults and "default_build_path" in defaults:
                build_path = defaults["default_build_path"]

        # Fall back to config constants or hardcoded defaults
        if not build_path:
            if (
                config.DEFAULT_BUILD_PATH is not None
                and len(config.DEFAULT_BUILD_PATH) > 0
            ):
                build_path = config.DEFAULT_BUILD_PATH
            else:
                build_path = "build/app"

        # Resolve full path (resolve_path handles absolute vs relative)
        target_path = resolve_path(work_path, build_path)
        if create_path and not target_path.exists():
            target_path.mkdir(parents=True, exist_ok=True)

        return target_path

    def get_default_dist_path(
        self, work_path: str, create_path: bool
    ) -> Optional[Path]:
        """Get the default dist path from configuration defaults."""
        dist_path: str = None
        # Try to get from configuration if validated, otherwise use defaults
        if self.is_validated():
            defaults = self.get_configuration_defaults()
            if defaults and "default_dist_path" in defaults:
                dist_path = defaults["default_dist_path"]

        # Fall back to config constants or hardcoded defaults
        if not dist_path:
            if (
                config.DEFAULT_DIST_PATH is not None
                and len(config.DEFAULT_DIST_PATH) > 0
            ):
                dist_path = config.DEFAULT_DIST_PATH
            else:
                dist_path = "build/dist"

        # Resolve full path (resolve_path handles absolute vs relative)
        target_path = resolve_path(str(work_path), dist_path)
        if create_path and not target_path.exists():
            target_path.mkdir(parents=True, exist_ok=True)

        return target_path

    def get_default_object_path(
        self, work_path: str, create_path: bool
    ) -> Optional[Path]:
        """Get the default config path from configuration defaults."""
        cfg_path: str = None
        # Try to get from configuration if validated, otherwise use defaults
        if self.is_validated():
            defaults = self.get_configuration_defaults()
            if defaults and "default_config_path" in defaults:
                cfg_path = defaults["default_config_path"]

        # Fall back to config constants or hardcoded defaults
        if not cfg_path:
            if (
                config.DEFAULT_CONFIG_PATH is not None
                and len(config.DEFAULT_CONFIG_PATH) > 0
            ):
                cfg_path = config.DEFAULT_CONFIG_PATH
            else:
                cfg_path = "build/obj"

        # Resolve full path (resolve_path handles absolute vs relative)
        target_path = resolve_path(str(work_path), cfg_path)
        if create_path and not target_path.exists():
            target_path.mkdir(parents=True, exist_ok=True)

        return target_path

    def get_logging_configuration(self, work_path: Path) -> Optional[str]:
        """
        Get the logging configuration file path from the configuration.

        Args:
            work_path: Workspace root path

        Returns:
            Optional[str]: Absolute path to logging config file, or None if not configured

        Example:
            >>> config_svc = ConfigurationService.get_instance()
            >>> log_config = config_svc.get_logging_configuration(Path.cwd())
            >>> if log_config:
            ...     configure_logging(config_path=log_config)
        """
        if not self.model or not self.model.spec.logging:
            self.logger.debug("No logging configuration specified in platform config")
            return None

        # Access the file property from the logging model
        if not self.model.spec.logging.file:
            self.logger.debug("No logging file specified in platform config")
            return None

        logging_path = self.model.spec.logging.file

        # Handle absolute paths
        if Path(logging_path).is_absolute():
            if Path(logging_path).exists():
                self.logger.debug(
                    "Using absolute logging config path", extra={"path": logging_path}
                )
                return str(Path(logging_path).resolve())
            else:
                self.logger.warning(
                    "Logging config file not found", extra={"path": logging_path}
                )
                return None

        # Handle relative paths (relative to workspace root)
        full_path = work_path / logging_path
        if full_path.exists():
            self.logger.debug(
                "Using relative logging config path", extra={"path": str(full_path)}
            )
            return str(full_path.resolve())
        else:
            self.logger.warning(
                "Logging config file not found relative to workspace",
                extra={"path": str(full_path), "work_path": str(work_path)},
            )
            return None
