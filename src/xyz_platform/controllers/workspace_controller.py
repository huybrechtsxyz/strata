#!/usr/bin/env python3
"""
===============================================================================
Script Name   : workspace_controller.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Controller for orchestrating workspace hierarchy and configuration.
                Handles infrastructure merging, configuration layering, and validation.
===============================================================================
"""

import yaml
from glob import glob
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import os
import re

from xyz_platform.logger.logger import get_logger
from xyz_platform.models.common_models import PlatformKind
from xyz_platform.services.configuration_service import ConfigurationService
from xyz_platform.services.deployment_service import DeploymentService
from xyz_platform.services.base_service import BaseService
from xyz_platform.services.unknown_service import UnknownService
from xyz_platform.utils import system
from xyz_platform.utils.configuration_loader import ConfigurationLoader
from xyz_platform.logger.logger import reconfigure_logging, configure_logging


class WorkspaceController:
    """
    Controller for orchestrating workspace hierarchy and configuration.
    """

    def __init__(self):
        """Initialize the workspace controller."""
        self.logger = get_logger(self.__class__.__module__)
        self._errors: List[str] = []
        self._messages: List[str] = []

    # Error / message accumulation helpers

    def has_errors(self) -> bool:
        return bool(self._errors)

    def get_errors(self) -> List[str]:
        return list(self._errors)

    def clear_errors(self) -> None:
        self._errors.clear()

    def get_messages(self) -> List[str]:
        return list(self._messages)

    def clear_messages(self) -> None:
        self._messages.clear()

    # Environment Variable Management

    def load_environment_variables(
        self,
        work_path: Path,
        env_path: Optional[Path] = None,
        env_file: Optional[Path] = None,
        overwrite: bool = False,
    ) -> Tuple[bool, List[str]]:
        """
        Discover and load .env-style files into the ConfigurationService in-memory map.

        Precedence (applied in this order, later files override earlier keys
        in the local merged dict; when calling ConfigurationService the
        `overwrite` flag controls whether existing in-memory keys are replaced):
          explicit file (envfile) > .env.local > .env > .xyz-platform/.env

        Args:
            work_path: Workspace root path
            envpath: Optional directory to look for env files (absolute or relative to work_path)
            envfile: Optional explicit env filename to load (highest precedence)
            overwrite: Whether to overwrite existing keys in ConfigurationService

        Returns:
            Tuple[bool, List[str]]: (success, list_of_loaded_paths_or_errors)
        """
        try:
            candidates: List[Path] = []
            config_service = ConfigurationService.get_instance()
            local_path = config_service.get_default_state_path(
                work_path, create_path=False
            )

            if (
                env_path
                and env_path.is_absolute()
                and env_path.is_dir()
                and not env_path.exists()
            ):
                self.logger.warning(
                    "Env path is invalid",
                    extra={"env_path": str(env_path)},
                )
                return False, [f"Env path is invalid: {env_path}"]
            elif (
                env_path
                and not env_path.is_absolute()
                and not (work_path / env_path).exists()
            ):
                self.logger.warning(
                    "Env path is invalid (relative path does not exist)",
                    extra={
                        "env_path": str(env_path),
                        "resolved": str(work_path / env_path),
                    },
                )
                return False, [f"Env path is invalid: {env_path}"]

            if (
                env_file
                and env_file.is_absolute()
                and env_file.is_file()
                and not env_file.exists()
            ):
                self.logger.warning(
                    "Env file is invalid",
                    extra={"env_file": str(env_file)},
                )
                return False, [f"Env file is invalid: {env_file}"]
            elif (
                env_file
                and not env_file.is_absolute()
                and not (work_path / env_file).exists()
            ):
                self.logger.warning(
                    "Env file is invalid (relative path does not exist)",
                    extra={
                        "env_file": str(env_file),
                        "resolved": str(work_path / env_file),
                    },
                )
                return False, [f"Env file is invalid: {env_file}"]

            if work_path:
                candidates.append(work_path / ".env")
            elif local_path:
                candidates.append(local_path / ".env")

            if env_path:
                if env_path.is_absolute():
                    pattern = str(env_path / "*.env")
                else:
                    pattern = str(work_path / env_path / "*.env")

                self.logger.debug(
                    "Resolving env-path pattern",
                    extra={"pattern": pattern},
                )

                matches = glob(pattern, recursive=False)

                if matches:
                    # Sort for consistent ordering and convert to Path objects
                    candidates.extend([Path(p) for p in sorted(matches)])
                    self.logger.debug(
                        "Config-path matched files",
                        extra={"pattern": pattern, "count": len(matches)},
                    )
                else:
                    self.logger.debug(
                        "Config-path matched no files",
                        extra={"pattern": pattern},
                    )

            if env_file and env_file.is_absolute():
                candidates.append(env_file)
            elif env_file:
                candidates.append(work_path / env_file)

            # Keep only existing files in discovery order
            found_files: List[Path] = [
                p for p in candidates if p.exists() and p.is_file()
            ]

            if not found_files:
                self.logger.debug(
                    "No .env files found to load",
                    extra={"candidates": [str(p) for p in candidates]},
                )
                return True, []

            # Seed merged with current process environment so .env files
            # can override system values. Use a copy to avoid mutating os.environ.
            merged: Dict[str, str] = dict(os.environ)

            def _parse_and_merge(p: Path, target: Dict[str, str]):
                text = p.read_text(encoding="utf-8")
                for raw in text.splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    # Remove surrounding quotes
                    if (val.startswith('"') and val.endswith('"')) or (
                        val.startswith("'") and val.endswith("'")
                    ):
                        val = val[1:-1]

                    # Expand ${VAR} or $VAR using merged then os.environ
                    def _exp(m):
                        name = m.group(1) or m.group(2)
                        return str(target.get(name) or os.environ.get(name) or "")

                    val = re.sub(r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)", _exp, val)

                    # Set into target merged dict (later files will override earlier ones)
                    target[key] = val

            for p in found_files:
                try:
                    _parse_and_merge(p, merged)
                    self.logger.debug(
                        "Loaded env file", extra={"path": str(p), "vars": len(merged)}
                    )
                except Exception as e:
                    self.logger.error(
                        "Failed to parse env file",
                        extra={"path": str(p), "error": str(e)},
                    )
                    return False, [f"Failed to parse env file {p}: {e}"]

            # Push merged vars into ConfigurationService
            config_svc = ConfigurationService.get_instance()
            config_svc.add_environment_variables(merged, overwrite=overwrite)

            loaded_paths = [str(p.resolve()) for p in found_files]
            self._messages.append(f"Loaded environment variables from: {loaded_paths}")
            return True, loaded_paths

        except Exception as exc:
            self.logger.error(
                "Failed to load environment variables",
                extra={"error": str(exc)},
                exc_info=True,
            )
            return False, [str(exc)]

    # Configuration Management

    # Get the configuration service instance
    def get_configuration_service(self) -> ConfigurationService:
        return ConfigurationService.get_instance()

    # Resolve configuration files based on input parameters and workspace structure
    def resolve_configuration_files(
        self,
        work_path: Path,
        data_path: Optional[Path] = None,
        config_file: Optional[str] = None,
        config_path: Optional[str] = None,
    ) -> List[str]:
        """
        Resolve configuration file paths in priority order.

        Returns ordered list of configuration files (lowest to highest priority):
        1. Bundled standard configuration (package data/configuration.yaml) - always loaded, lowest priority
        2. Configuration directory path (--config-path) - medium priority
        3. Specific configuration file (--config-file) - highest priority

        Files are returned in processing order so later files override earlier ones.

        Args:
            work_path: Workspace root path
            config_file: Optional specific configuration file (highest priority)
            config_path: Optional configuration directory path (medium priority)

        Returns:
            List[str]: Ordered list of configuration file paths to load

        Raises:
            FileNotFoundError: If specified config_file doesn't exist

        Example:
            >>> controller = WorkspaceController()
            >>> files = controller.resolve_configuration_files(
            ...     work_path=Path.cwd(),
            ...     config_file="custom-config.yaml",
            ...     config_path="config/configurations"
            ... )
            >>> print(f"Will load {len(files)} configuration files")
        """
        file_paths = []

        # Priority 1 (lowest): Bundled standard configuration from package
        bundled_config = system.get_default_config_path(data_path=data_path)
        if bundled_config.exists():
            file_paths.append(str(bundled_config))
            self.logger.debug(
                "Found bundled standard configuration",
                extra={"config": str(bundled_config)},
            )
        else:
            self.logger.warning(
                "Bundled standard configuration not found (should always exist)",
                extra={"expected_path": str(bundled_config)},
            )

        # Priority 2 (medium): Configuration directory path (--config-path)
        if config_path:
            config_dir = Path(config_path)
            if config_dir.is_absolute():
                pattern = str(config_dir / "*.yaml")
            else:
                pattern = str(work_path / config_dir / "*.yaml")

            self.logger.debug(
                "Resolving config-path pattern",
                extra={"pattern": pattern},
            )

            matches = glob(pattern, recursive=False)

            if matches:
                # Sort for consistent ordering
                file_paths.extend(sorted(matches))
                self.logger.debug(
                    "Config-path matched files",
                    extra={"pattern": pattern, "count": len(matches)},
                )
            else:
                self.logger.debug(
                    "Config-path matched no files",
                    extra={"pattern": pattern},
                )

        # Priority 3 (highest): Specific configuration file (--config-file)
        if config_file:
            config_file_path = Path(config_file)
            if not config_file_path.is_absolute():
                config_file_path = work_path / config_file_path

            if config_file_path.exists():
                file_paths.append(str(config_file_path))
                self.logger.debug(
                    "Adding specific config file (highest priority)",
                    extra={"config_file": str(config_file_path)},
                )
            else:
                error_msg = f"Specified config file not found: {config_file_path}"
                self.logger.error(error_msg)
                raise FileNotFoundError(error_msg)

        self.logger.debug(
            "Resolved configuration files",
            extra={"file_count": len(file_paths), "files": file_paths},
        )

        return file_paths

    # Load and validate configuration from resolved file paths
    def load_configuration(
        self, work_path: Path, file_paths: List[str]
    ) -> Tuple[bool, List[str]]:
        """
        Load and validate configuration from resolved file paths.

        Takes a list of configuration file paths (typically from resolve_configuration_files)
        and loads them in order, with later files overriding earlier ones.

        Args:
            work_path: Workspace root path
            file_paths: Ordered list of configuration file paths to load and merge

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)

        Example:
            >>> controller = WorkspaceController()
            >>> files = controller.resolve_configuration_files(
            ...     work_path=Path.cwd(),
            ...     config_file="custom.yaml"
            ... )
            >>> success, errors = controller.load_configuration(work_path, files)
            >>> if success:
            ...     print("Configuration loaded successfully")
        """
        if not file_paths:
            self.logger.info(
                "No configuration files to load - continuing without configuration"
            )
            return True, []  # Success - no config is valid

        self.logger.info(
            "Loading configuration files",
            extra={"file_count": len(file_paths), "files": file_paths},
        )

        config_service = ConfigurationService.get_instance()

        try:
            loader = ConfigurationLoader()
            merged_config = loader.load_and_merge_yaml_files(
                [Path(f) for f in file_paths]
            )

            # Update ConfigurationService with merged config
            config_service.data = merged_config
            config_service._validated = False
            config_service._validation_errors = []
            config_service.model = None

            # Save merged configuration to temp directory for debugging
            try:
                merged_config_file = config_service.get_temp_configuration_path(
                    work_path=work_path, create_path=True
                )

                with open(merged_config_file, "w", encoding="utf-8") as f:
                    yaml.dump(
                        merged_config, f, default_flow_style=False, sort_keys=False
                    )

                self.logger.debug(
                    "Saved merged configuration for debugging",
                    extra={"path": str(merged_config_file)},
                )
            except Exception as e:
                # Don't fail if we can't save debug file
                self.logger.debug(
                    f"Failed to save merged configuration for debugging: {e}"
                )

            # Validate
            success, errors = config_service.validate(work_path=str(work_path))
            if success:
                self.logger.info(
                    "Configuration loaded and validated successfully",
                    extra={"file_count": len(file_paths)},
                )
            else:
                self.logger.error(
                    "Configuration validation failed",
                    extra={"errors": errors},
                )

            return success, errors

        except Exception as e:
            error_msg = f"Failed to load configuration files: {str(e)}"
            self.logger.error(
                error_msg,
                exc_info=True,
                extra={"file_paths": file_paths},
            )
            return False, [error_msg]

    # Load deployment-specific configuration files from deployment spec.configurations and merge with existing config
    def load_deployment_configuration(
        self,
        deployment_data: Dict,
        work_path: Path,
    ) -> Tuple[bool, List[str]]:
        """
        Load configuration files from deployment's spec.configurations.

        This method loads deployment-specific configuration files into the
        ConfigurationService singleton, merging them with any existing configuration.

        Called BEFORE deployment validation, since validation may require these
        configurations to be loaded.

        Args:
            deployment_data: Raw deployment data (dict) with spec.configurations
            work_path: Workspace root path for resolving relative paths

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)

        Example:
            >>> controller = WorkspaceController()
            >>> # Load raw deployment data
            >>> with open("deployment.yaml") as f:
            ...     deployment_data = yaml.safe_load(f)
            >>> # Load deployment configs BEFORE validation
            >>> success, errors = controller.load_deployment_configuration(
            ...     deployment_data=deployment_data,
            ...     work_path=Path.cwd()
            ... )
        """
        if not deployment_data:
            return True, []  # No data, nothing to load

        # Extract spec.configurations from raw data
        spec = deployment_data.get("spec", {})
        configurations = spec.get("configurations", [])

        if not configurations:
            self.logger.debug("No deployment configurations to load")
            return True, []  # No configurations defined, success

        self.logger.info(
            "Loading deployment-specific configurations",
            extra={"count": len(configurations)},
        )

        # Get current configuration service to merge with
        config_service = ConfigurationService.get_instance()

        # Resolve configuration file paths from raw data
        config_files = []
        for config_ref in configurations:
            # Extract file path from raw dict
            config_file = config_ref.get("file", "")
            config_name = config_ref.get("name", config_file)

            if not config_file:
                error_msg = (
                    f"Configuration reference missing 'file' field: {config_name}"
                )
                self.logger.error(error_msg)
                return False, [error_msg]

            config_path = work_path / config_file
            if config_path.exists():
                config_files.append(str(config_path))
                self.logger.debug(
                    f"Found deployment configuration: {config_name}",
                    extra={"path": str(config_path)},
                )
            else:
                error_msg = f"Configuration file not found: {config_file}"
                self.logger.error(
                    error_msg,
                    extra={"config_name": config_name, "path": str(config_path)},
                )
                return False, [error_msg]

        # Load and merge deployment configurations with existing config
        if config_files:
            try:
                loader = ConfigurationLoader()
                deployment_config = loader.load_and_merge_yaml_files(config_files)

                # Merge with existing configuration in singleton
                # If existing config exists, deployment config overrides it
                if config_service.data:
                    self.logger.debug(
                        "Merging deployment config with existing configuration",
                        extra={"deployment_files": len(config_files)},
                    )
                    merged_config = loader.merge_configs(
                        [config_service.data, deployment_config]
                    )
                else:
                    self.logger.debug(
                        "No existing config, using deployment config only",
                        extra={"deployment_files": len(config_files)},
                    )
                    merged_config = deployment_config

                # Update ConfigurationService singleton with merged config
                config_service.data = merged_config
                config_service._validated = False
                config_service._validation_errors = []
                config_service.model = None

                # Validate the merged configuration
                validation_success, validation_errors = config_service.validate(
                    work_path=str(work_path)
                )
                if not validation_success:
                    error_msg = "Merged configuration validation failed"
                    self.logger.error(error_msg, extra={"errors": validation_errors})
                    return False, validation_errors

                # Save merged configuration to temp directory for debugging
                try:
                    merged_config_file = config_service.get_temp_configuration_path(
                        work_path=work_path, create_path=True
                    )

                    with open(merged_config_file, "w", encoding="utf-8") as f:
                        yaml.dump(
                            merged_config, f, default_flow_style=False, sort_keys=False
                        )

                    self.logger.debug(
                        "Saved merged configuration for debugging",
                        extra={"path": str(merged_config_file)},
                    )
                except Exception as e:
                    # Don't fail if we can't save debug file
                    self.logger.debug(
                        f"Failed to save merged configuration for debugging: {e}"
                    )

                self.logger.info(
                    "Deployment configurations loaded and merged successfully",
                    extra={"file_count": len(config_files)},
                )
                return True, []

            except Exception as e:
                error_msg = f"Failed to load deployment configurations: {str(e)}"
                self.logger.error(
                    error_msg, exc_info=True, extra={"config_files": config_files}
                )
                return False, [error_msg]

        return True, []

    # Load the logging configuration from the already-loaded platform configuration and apply it
    def configure_logging_from_platform_config(
        self,
        work_path: Path,
        fallback_console: bool = True,
    ) -> bool:
        """
        Configure logging from already-loaded platform configuration.

        Assumes platform configuration has already been loaded via load_configuration().
        Extracts the logging file path from the loaded configuration and applies it.
        Falls back to standard console logging if no logging config is found.

        Args:
            work_path: Workspace root path
            fallback_console: Enable console logging if no config is found

        Returns:
            bool: True if logging was configured from platform config, False if fallback used

        Example:
            >>> controller = WorkspaceController()
            >>> # Step 1: Resolve config files
            >>> files = controller.resolve_configuration_files(
            ...     work_path=Path.cwd(),
            ...     config_file="config.yaml"
            ... )
            >>> # Step 2: Load configuration
            >>> success, errors = controller.load_configuration(work_path, files)
            >>> # Step 3: Apply logging from loaded config
            >>> if success:
            ...     controller.configure_logging_from_platform_config(
            ...         work_path=Path.cwd()
            ...     )
        """
        config_service = ConfigurationService.get_instance()

        try:
            # Check if configuration is already loaded and validated
            if not config_service.is_validated():
                self.logger.debug(
                    "Platform configuration not loaded, using fallback logging"
                )
                if fallback_console:
                    configure_logging(level="INFO", enable_console=True)
                return False

            # Extract logging configuration from loaded platform config
            log_config_path = config_service.get_logging_configuration(work_path)

            if log_config_path:
                self.logger.debug(
                    "Applying logging configuration from platform config",
                    extra={"config_path": log_config_path},
                )
                reconfigure_logging(config_path=log_config_path)
                return True
            else:
                self.logger.debug(
                    "No logging configuration in platform config, using fallback"
                )
                if fallback_console:
                    configure_logging(level="INFO", enable_console=True)
                return False

        except Exception as e:
            self.logger.warning(
                "Failed to apply logging from platform config",
                extra={"error": str(e)},
                exc_info=True,
            )
            if fallback_console:
                configure_logging(level="INFO", enable_console=True)
            return False

    # Platform Management

    # Load and validate a platform file
    def load_and_validate_file(
        self,
        platform_file: str,
        expected_kind: Optional[PlatformKind] = None,
        work_path: Optional[str] = None,
        configuration_service: Optional["ConfigurationService"] = None,
    ) -> Tuple[Optional[BaseService], List[str]]:
        """
        Load and validate a platform file.

        Uses UnknownService to detect kind and route to appropriate service.

        Args:
            platform_file: Path to the platform YAML file
            expected_kind: Optional expected platform kind for validation
            work_path: Optional working directory for validating file paths
            configuration_service: Optional ConfigurationService instance for dynamic validation

        Returns:
            Tuple of (service instance, list of errors)
            - If successful: (service, [])
            - If failed: (None or UnknownService, [error messages])
        """
        unknown_service, load_errors = self.load_platform_file(
            platform_file=platform_file,
            expected_kind=expected_kind,
            work_path=work_path,
        )

        if unknown_service is None or load_errors:
            # Loading failed or has errors, return them
            return unknown_service, load_errors

        known_service, load_errors = self.load_platform_service(
            unknown_service=unknown_service
        )
        if known_service is None or load_errors:
            # Instantiating specific service failed, return errors
            return known_service, load_errors

        # Loading succeeded without errors, now validate with the specific service
        return self.validate_platform_service(
            known_service=known_service,
            configuration_service=configuration_service,
            work_path=work_path,
        )

    # Load the platform file using UnknownService to detect if its a platform file
    def load_platform_file(
        self,
        platform_file: str,
        expected_kind: Optional[PlatformKind] = None,
        work_path: Optional[str] = None,
    ) -> Tuple[Optional[UnknownService], List[str]]:
        """Load a platform file using UnknownService to detect its kind.

        Handles ``@repo_name/...`` cross-repo references automatically by
        looking up the repo's ``deploy_path`` from the loaded ConfigurationService.
        """
        # Resolve @repo_name/... cross-repo references using the loaded ConfigurationService
        platform_path: Path
        platform_file = str(platform_file) if platform_file else ""
        if platform_file.startswith("@"):
            repo_map = {}
            config_service = ConfigurationService.get_instance()
            repo_map = config_service.get_repo_map()

            try:
                platform_path = system.resolve_path(
                    str(work_path), platform_file, repo_map=repo_map
                )
            except ValueError as exc:
                error_msg = str(exc)
                self.logger.error(error_msg)
                return None, [error_msg]
        else:
            # Resolve the platform file path - pass as target_path (2nd param) not sub_path
            platform_path = system.resolve_path(str(work_path), platform_file)

        # Validate file exists
        if not platform_path.exists():
            self.logger.error(
                "Platform file not found", extra={"file": str(platform_file)}
            )
            error = f"Platform file not found: {platform_file}"
            return None, [error]

        if not platform_path.is_file():
            self.logger.error("Path is not a file", extra={"file": str(platform_file)})
            error = f"Path is not a file: {platform_file}"
            return None, [error]

        # Validate file extension
        if platform_path.suffix.lower() not in (".yml", ".yaml"):
            self.logger.error(
                "Invalid file type",
                extra={
                    "file": platform_file,
                    "suffix": platform_path.suffix,
                    "expected": ".yml or .yaml",
                },
            )
            error = f"Invalid file type: {platform_file} (expected .yml or .yaml)"
            return None, [error]

        self.logger.debug(
            "Loading platform file",
            extra={"file": str(platform_file)},
        )

        # Load with UnknownService to detect kind
        try:
            unknown_service = UnknownService(path=platform_file)
            is_valid, validation_errors = unknown_service.validate()

            if not is_valid:
                self.logger.error(
                    "Platform file validation failed (UnknownService)",
                    extra={
                        "file": str(platform_file),
                        "error_count": len(validation_errors),
                    },
                )
                for error in validation_errors:
                    self.logger.error(
                        "Validation error detail", extra={"error": str(error)}
                    )
                return unknown_service, validation_errors

            detected_kind = unknown_service.get_kind()
            # get_kind() returns a string, not an enum
            detected_kind_str = (
                detected_kind.value
                if isinstance(detected_kind, PlatformKind)
                else detected_kind
            )
            self.logger.debug(
                "Detected platform kind",
                extra={"file": str(platform_file), "kind": detected_kind_str},
            )

            # Validate expected kind if provided
            if expected_kind is not None:
                expected_kind_str = (
                    expected_kind.value
                    if hasattr(expected_kind, "value")
                    else str(expected_kind)
                )
                if detected_kind_str != expected_kind_str:
                    self.logger.error(
                        "Invalid platform kind",
                        extra={
                            "file": str(platform_file),
                            "detected": detected_kind_str,
                            "expected": expected_kind_str,
                        },
                    )
                    error = f"Invalid platform kind: {detected_kind_str} (expected: {expected_kind_str})"
                    return unknown_service, [error]

            # Loading succeeded - return the unknown_service with no errors
            return unknown_service, []

        except Exception as e:
            self.logger.error(
                "Failed to load platform file",
                extra={
                    "file": str(platform_file),
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
                exc_info=True,
            )
            error = f"Failed to load platform file: {str(e)}"
            return None, [error]

    # Get the specific service for the detected kind using UnknownService
    def load_platform_service(
        self,
        unknown_service: UnknownService,
    ) -> Tuple[BaseService, List[str]]:
        """Load the typed service from an UnknownService by routing on detected kind."""
        try:
            detected_kind = unknown_service.get_kind()
            detected_kind_str = (
                detected_kind.value
                if isinstance(detected_kind, PlatformKind)
                else detected_kind
            )
            self.logger.info(
                "Loading service",
                extra={
                    "file": str(unknown_service.path),
                    "kind": detected_kind_str,
                },
            )
            known_service = unknown_service.get_service_by_kind()

            valid, errors = known_service.validate()
            if not valid:
                self.logger.error(
                    "Service validation failed",
                    extra={
                        "file": str(unknown_service.path),
                        "kind": detected_kind_str,
                        "error_count": len(errors),
                    },
                )
                for error in errors:
                    self.logger.error(
                        "Validation error detail",
                        extra={"error": str(error), "kind": detected_kind_str},
                    )
                return known_service, errors

            return known_service, []
        except Exception as e:
            detected_kind = unknown_service.get_kind()
            detected_kind_str = (
                detected_kind.value
                if isinstance(detected_kind, PlatformKind)
                else detected_kind
            )
            self.logger.error(
                "Failed to instantiate service",
                extra={
                    "file": str(unknown_service.path),
                    "kind": detected_kind_str,
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
                exc_info=True,
            )
            error = f"Failed to instantiate service for {detected_kind_str}: {str(e)}"
            return unknown_service, [error]

    # Validate a platform file with the specific service
    def validate_platform_service(
        self,
        known_service: BaseService,
        configuration_service: Optional["ConfigurationService"] = None,
        work_path: Optional[str] = None,
    ) -> Tuple[BaseService, List[str]]:
        """Validate a platform file using the specific service for its detected kind."""
        try:
            # Get the kind string for logging
            detected_kind = known_service.get_kind()
            detected_kind_str = (
                detected_kind.value
                if isinstance(detected_kind, PlatformKind)
                else detected_kind
            )

            # Get configuration model for dynamic validation if provided
            configuration_model = None
            if configuration_service and configuration_service.model:
                configuration_model = configuration_service.model
                self.logger.debug(
                    "Using provided configuration for validation",
                    extra={"has_configuration": True},
                )
            # Validate with the specific service, passing configuration_model and work_path
            is_valid, validation_errors = known_service.validate(
                configuration_model=configuration_model, work_path=work_path
            )

            if not is_valid:
                self.logger.error(
                    "Service validation failed",
                    extra={
                        "file": (
                            str(configuration_service.path)
                            if configuration_service
                            else str(known_service.path)
                        ),
                        "kind": detected_kind_str,
                        "error_count": len(validation_errors),
                    },
                )
                for error in validation_errors:
                    self.logger.error(
                        "Validation error detail",
                        extra={"error": str(error), "kind": detected_kind_str},
                    )
                return known_service, validation_errors

            self.logger.info(
                "Successfully loaded and validated service",
                extra={"file": str(known_service.path), "kind": detected_kind_str},
            )

            return known_service, []

        except Exception as e:
            detected_kind = known_service.get_kind()
            detected_kind_str = (
                detected_kind.value
                if isinstance(detected_kind, PlatformKind)
                else detected_kind
            )
            self.logger.error(
                "Failed to instantiate service",
                extra={
                    "file": str(known_service.path),
                    "kind": detected_kind_str,
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
                exc_info=True,
            )
            error = f"Failed to instantiate service for {detected_kind_str}: {str(e)}"
            return known_service, [error]

    # Load related services for a deployment
    def load_related_services(
        self,
        deployment_service: "DeploymentService",
        objects_path: Path | str,
        stage_name: Optional[str] = None,
    ) -> bool:
        """
        Load related services for a deployment.

        Orchestrates loading of workspace, providers, resources, environments,
        and other related services required for deployment validation and execution.

        Args:
            deployment_service: The deployment service to load related services for
            objects_path: Path to the objects directory
            stage_name: Optional stage name to load only that stage's environments

        Returns:
            Tuple of (related_services dict, success bool)
            - related_services: Dictionary containing loaded services
            - success: True if all services loaded successfully, False otherwise
        """
        self.logger.info(
            "Loading related services for deployment",
            extra={"stage_name": stage_name} if stage_name else {},
        )

        load_success = deployment_service.load_deploy_services(
            objects_path=str(objects_path)
        )

        if not load_success:
            error_msg = "Failed to load deployment related services"
            self.logger.error(error_msg)
            self._errors.append(error_msg)

            # Log validation errors from deployment service
            validation_errors = deployment_service.get_validation_errors()
            if validation_errors:
                for err in validation_errors:
                    self.logger.error(f"  - {err}")

            return False

        ws_service = deployment_service.get_workspace_service()
        ws_name = ws_service.get_name() if ws_service is not None else ""
        env_service = deployment_service.get_environment_service()
        env_name = env_service.get_name() if env_service is not None else ""
        self.logger.info(
            "Related services loaded successfully",
            extra={
                "workspace": ws_name,
                "environment": env_name,
            },
        )

        return True

    # Workspace Paths

    # Get the deployed build path (buildpath/deploy_version/*)
    def get_workspace_buildpath_instance(
        self, deployment_service: DeploymentService, build_path: Path
    ) -> Path:
        """Get the deployed build path for a specific deployment version."""
        deploy_path = deployment_service.get_build_path(build_path)
        self.logger.debug(
            "Target deployed build directory",
            extra={"deployed_build_path": str(deploy_path)},
        )
        return deploy_path

    # Get the build path based on work path
    def get_workspace_buildpath(self, work_path: Path) -> Path:
        """Get the build path for the given workspace."""
        config_service = ConfigurationService.get_instance()
        build_path = config_service.get_default_build_path(work_path, create_path=True)
        self.logger.debug(
            "Target build directory", extra={"build_path": str(build_path)}
        )
        return build_path

    # Get the object path based on work path
    def get_workspace_objectpath(self, work_path: Path) -> Path:
        """Get the object path for the given workspace."""
        config_service = ConfigurationService.get_instance()
        object_path = config_service.get_default_object_path(
            work_path, create_path=True
        )
        self.logger.debug(
            "Target object directory", extra={"object_path": str(object_path)}
        )
        return object_path

    # Get the provisioner path based on work path
    def get_workspace_provisionerpath(self, work_path: Path) -> Path:
        """
        Get the provisioner path for the given workspace.

        NOTE: ConfigurationService.get_default_provisioner_path() is not available
        in this version. Falls back to <work_path>/provisioner.
        """
        provisioner_path = work_path / "provisioner"
        provisioner_path.mkdir(parents=True, exist_ok=True)
        self.logger.debug(
            "Target provisioner directory (default fallback)",
            extra={"provisioner_path": str(provisioner_path)},
        )
        return provisioner_path

    # Get the dist path based on work path
    def get_workspace_distpath(self, work_path: Path) -> Path:
        """Get the dist path for the given workspace."""
        config_service = ConfigurationService.get_instance()
        dist_path = config_service.get_default_dist_path(work_path, create_path=True)
        self.logger.debug("Target dist directory", extra={"dist_path": str(dist_path)})
        return dist_path

    # Get the work path based on input or default to current directory
    def get_workspace_workpath(self, work_path: Path) -> Path:
        """Get the work path for the given workspace."""
        # If work_path is provided, use it directly
        if work_path is not None and work_path != "":
            work_path = work_path.resolve()
            self.logger.debug(
                "Target work directory from argument",
                extra={"work_path": str(work_path)},
            )
            return work_path

        # Use current working directory as default
        if Path.cwd().is_absolute():
            work_path = Path.cwd()
        else:
            work_path = Path.cwd().resolve()
        self.logger.debug(
            "Target work directory (default)", extra={"work_path": str(work_path)}
        )
        return work_path

    # Get the repository maps from the loaded configuration
    def get_workspace_repo_maps(self) -> Dict[str, str]:
        """Get the repository maps from the loaded configuration."""
        config_service = ConfigurationService.get_instance()
        repo_map = config_service.get_repo_map()
        self.logger.debug(
            "Retrieved repository map from configuration",
            extra={"repo_count": len(repo_map), "repos": list(repo_map.keys())},
        )
        return repo_map
