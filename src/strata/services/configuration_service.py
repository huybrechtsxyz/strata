"""Configuration service — centralised singleton for platform configuration."""

import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from strata.exceptions import PlatformFileNotFoundError
from strata.logger import get_logger
from strata.models.configuration_model import ConfigurationModel
from strata.models.repository_model import RemoteModel
from strata.services.base_service import BaseService
from strata.utils import config
from strata.utils.configuration_loader import ConfigurationLoader
from strata.utils.system import resolve_path


class ConfigurationService(BaseService["ConfigurationModel"]):
    """Service for handling configuration configurations (Centralized Singleton pattern)."""

    _instances: Dict[str, "ConfigurationService"] = {}
    _lock = threading.Lock()
    _initialized: bool

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

    def __init__(self) -> None:
        """Initialize configuration service with blank config (only once)."""
        if self._initialized:
            return

        # Initialize with empty data - no loading on init
        self.path = None  # ConfigurationService doesn't load from a single path
        self.data = {}
        self.model = None
        self._validated = False
        self._validation_errors: List[str] = []

        # Initialize logger
        self.logger = get_logger(self.__class__.__module__)
        # In-memory environment variables map (key -> value as string)
        self._env_vars: Dict[str, str] = {}
        # Lock for env var access
        self._env_lock = threading.RLock()
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
            duplicates = [name for name in provider_names if provider_names.count(name) > 1]
            if duplicates:
                errors.append(f"Duplicate provider names in merged configuration: {', '.join(set(duplicates))}")

            # Validate unique regions and resources within each provider
            for provider in self.model.spec.providers:
                # Check unique regions
                if provider.regions:
                    region_names = [r if isinstance(r, str) else r.get("name", str(r)) for r in provider.regions]
                    duplicates = [name for name in region_names if region_names.count(name) > 1]
                    if duplicates:
                        errors.append(
                            f"Duplicate regions in provider '{provider.name}' (merged config): {', '.join(set(duplicates))}"
                        )

                # Check unique resources
                if provider.resources:
                    resource_names = [res.name for res in provider.resources]
                    duplicates = [name for name in resource_names if resource_names.count(name) > 1]
                    if duplicates:
                        errors.append(
                            f"Duplicate resources in provider '{provider.name}' (merged config): {', '.join(set(duplicates))}"
                        )

        # Validate unique topology types across merged configuration
        if self.model.spec.topologies:
            topology_types = [t.type for t in self.model.spec.topologies]
            duplicates = [ttype for ttype in topology_types if topology_types.count(ttype) > 1]
            if duplicates:
                errors.append(f"Duplicate topology types in merged configuration: {', '.join(set(duplicates))}")

            # Validate unique component roles within each topology
            for topology in self.model.spec.topologies:
                if topology.components:
                    roles = [comp.role for comp in topology.components]
                    duplicates = [role for role in roles if roles.count(role) > 1]
                    if duplicates:
                        errors.append(
                            f"Duplicate component roles in topology '{topology.type}' (merged config): {', '.join(set(duplicates))}"
                        )

        # Validate zones: each region in providers must exist in a zone (if zones are defined)
        if self.model.spec.zones and self.model.spec.providers:
            # Build flat set of all known zone regions
            zone_regions: set[str] = set()
            for zone in self.model.spec.zones:
                zone_regions.update(zone.regions)

            for provider in self.model.spec.providers:
                if not provider.regions or provider.additional_regions:
                    continue
                for region_entry in provider.regions:
                    region_name = (
                        region_entry if isinstance(region_entry, str) else region_entry.get("name", str(region_entry))
                    )
                    if region_name not in zone_regions:
                        errors.append(
                            f"Provider '{provider.name}' region '{region_name}' is not assigned to any zone. "
                            f"Add it to a zone in spec.zones or set additional_regions=true on the provider."
                        )

        # Validate logging file reference exists on disk
        if work_path and self.model.spec.logging and self.model.spec.logging.file:
            repo_map = self.get_remote_map()
            errors.extend(
                self._validate_file_refs(
                    work_path,
                    repo_map,
                    [("Logging config", self.model.spec.logging.file)],
                )
            )

        return len(errors) == 0, errors

    # Loading methods

    def add_configurations(self, config_files: List[Path]) -> Tuple[bool, List[str]]:
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
            self.logger.info("Adding configuration files", file_count=len(config_files))

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
            self.logger.error(
                "Failed to add configurations",
                config_files=config_files,
                error_type=type(e).__name__,
                exc_info=True,
            )
            return False, [f"Failed to add configurations from {config_files}: {str(e)}"]

    def add_configuration(self, config_file: Path) -> Tuple[bool, List[str]]:
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
            - "~/.strata/config/*.yaml"
            - "/etc/strata/*.yaml"

        Args:
            patterns: List of file patterns (e.g., ["config/*.yaml", "~/.strata/*.yaml"])

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)

        Example:
            >>> config_svc = ConfigurationService.get_instance()
            >>> success, errors = config_svc.load_from_paths([
            ...     "config/base.yaml",
            ...     "config/*.yaml",
            ...     "~/.strata/*.yaml"
            ... ])
            >>> if success:
            ...     print("Configuration loaded")
        """
        from glob import glob

        loader = ConfigurationLoader()

        try:
            self.logger.debug("Loading configurations from patterns", pattern_count=len(patterns), patterns=patterns)

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
                        pattern=pattern,
                        match_count=len(matches),
                    )
                else:
                    self.logger.debug("Pattern matched no files", pattern=pattern)

            if not file_paths:
                error_msg = f"No configuration files found for patterns: {patterns}"
                self.logger.warning(error_msg)
                return False, [error_msg]

            # Load and merge all files
            merged = loader.load_and_merge_yaml_files([Path(fp) for fp in file_paths])

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
                "Configurations loaded from patterns", pattern_count=len(patterns), file_count=len(file_paths)
            )

            # Validate the merged configuration
            success, errors = self.validate()
            return success, errors

        except Exception as e:
            self.logger.error(
                "Failed to load configurations from patterns",
                patterns=patterns,
                error_type=type(e).__name__,
                exc_info=True,
            )
            return False, [f"Failed to load configurations from patterns {patterns}: {str(e)}"]

    # Access methods

    def get_configuration(self) -> Optional[ConfigurationModel]:
        """Get the merged configuration data."""
        self._ensure_validated()
        return self.model if self.model else None

    def get_remotes(self) -> Optional[List[RemoteModel]]:
        """Get the list of remotes from the configuration."""
        self._ensure_validated()
        if self.model and self.model.spec and self.model.spec.remotes:
            return self.model.spec.remotes
        return None

    def get_remote_map(self) -> Dict[str, str]:
        """Return a ``{remote_name: deploy_path}`` mapping for resolving ``@remote_name/...`` references."""
        remotes = self.get_remotes()
        if not remotes:
            return {}
        return {remote.name: remote.deploy_path for remote in remotes if remote.name and remote.deploy_path}

    # Environment variable APIs

    def add_environment_variables(self, env_vars: Dict[str, Any], overwrite: bool = False) -> None:
        """
        Add environment variables to the in-memory map.

        Args:
            env_vars: Mapping of environment variable names to values.
            overwrite: If True, incoming values overwrite existing keys. If False,
                existing keys are preserved and new keys are added.

        Returns:
            None
        """
        if not env_vars:
            return

        with self._env_lock:
            for k, v in env_vars.items():
                if v is None:
                    # skip None values
                    continue
                sval = str(v)
                if overwrite or k not in self._env_vars:
                    self._env_vars[k] = sval
                    self.logger.debug("Set env var", key=k, value=sval)
                else:
                    self.logger.debug("Skipped env var (exists and overwrite=False)", key=k)

    def get_environment_variables(self) -> Dict[str, str]:
        """Return a shallow copy of the in-memory environment variables."""
        with self._env_lock:
            return dict(self._env_vars)

    def get_environment_variable(self, varname: str) -> Optional[str]:
        """Return a single environment variable value or None if not set."""
        with self._env_lock:
            return self._env_vars.get(varname)

    # Get configuration defaults

    def get_configuration_defaults(self) -> Optional[Dict[str, Any]]:
        """Get configuration defaults from the configuration spec."""
        self._ensure_validated()
        return (
            self.model.spec.configuration if self.model and self.model.spec and self.model.spec.configuration else None
        )

    def get_default_build_path(self, work_path: Path, create_path: bool) -> Path:
        """Get the default build path from configuration defaults."""
        build_path: Optional[str] = None
        # Try to get from configuration if validated, otherwise use defaults
        if self.is_validated():
            defaults = self.get_configuration_defaults()
            if defaults and "default_build_path" in defaults:
                build_path = defaults["default_build_path"]

        # Fall back to config constants or hardcoded defaults
        if not build_path:
            if config.DEFAULT_BUILD_PATH is not None and len(config.DEFAULT_BUILD_PATH) > 0:
                build_path = config.DEFAULT_BUILD_PATH
            else:
                build_path = "build/app"

        # Resolve full path (resolve_path handles absolute vs relative)
        target_path = resolve_path(str(work_path), build_path)
        if create_path and not target_path.exists():
            target_path.mkdir(parents=True, exist_ok=True)

        return target_path

    def get_default_dist_path(self, work_path: Path, create_path: bool) -> Path:
        """Get the default dist path from configuration defaults."""
        dist_path: Optional[str] = None
        # Try to get from configuration if validated, otherwise use defaults
        if self.is_validated():
            defaults = self.get_configuration_defaults()
            if defaults and "default_dist_path" in defaults:
                dist_path = defaults["default_dist_path"]

        # Fall back to config constants or hardcoded defaults
        if not dist_path:
            if config.DEFAULT_DIST_PATH is not None and len(config.DEFAULT_DIST_PATH) > 0:
                dist_path = config.DEFAULT_DIST_PATH
            else:
                dist_path = "build/dist"

        # Resolve full path (resolve_path handles absolute vs relative)
        target_path = resolve_path(str(work_path), dist_path)
        if create_path and not target_path.exists():
            target_path.mkdir(parents=True, exist_ok=True)

        return target_path

    def get_default_object_path(self, work_path: Path, create_path: bool) -> Path:
        """Get the default config path from configuration defaults."""
        cfg_path: Optional[str] = None
        # Try to get from configuration if validated, otherwise use defaults
        if self.is_validated():
            defaults = self.get_configuration_defaults()
            if defaults and "default_object_path" in defaults:
                cfg_path = defaults["default_object_path"]

        # Fall back to config constants or hardcoded defaults
        if not cfg_path:
            if config.DEFAULT_OBJECT_PATH is not None and len(config.DEFAULT_OBJECT_PATH) > 0:
                cfg_path = config.DEFAULT_OBJECT_PATH
            else:
                cfg_path = "build/obj"

        # Resolve full path (resolve_path handles absolute vs relative)
        target_path = resolve_path(str(work_path), cfg_path)
        if create_path and not target_path.exists():
            target_path.mkdir(parents=True, exist_ok=True)

        return target_path

    def get_default_state_path(self, work_path: Path, create_path: bool) -> Path:
        """Get the default state path from configuration defaults."""

        state_dir: Optional[str] = None
        # Try to get from configuration if validated, otherwise use defaults
        if self.is_validated():
            defaults = self.get_configuration_defaults()
            if defaults and "default_state_dir" in defaults:
                state_dir = defaults["default_state_dir"]

        # Fall back to config constants or hardcoded defaults
        if not state_dir:
            if config.DEFAULT_STATE_DIR is not None and len(config.DEFAULT_STATE_DIR) > 0:
                state_dir = config.DEFAULT_STATE_DIR
            else:
                state_dir = ".strata"

        # Resolve full path (resolve_path handles absolute vs relative)
        target_path = resolve_path(str(work_path), state_dir)
        if create_path and not target_path.exists():
            target_path.mkdir(parents=True, exist_ok=True)

        return target_path

    def get_default_state_file(self, state_path: Path, create_path: bool) -> Path:
        """Get the default state file path from configuration defaults."""
        state_file: Optional[str] = None
        # Try to get from configuration if validated, otherwise use defaults
        if self.is_validated():
            defaults = self.get_configuration_defaults()
            if defaults and "default_state_file" in defaults:
                state_file = defaults["default_state_file"]

        # Fall back to config constants or hardcoded defaults
        if not state_file:
            if config.DEFAULT_STATE_FILE is not None and len(config.DEFAULT_STATE_FILE) > 0:
                state_file = config.DEFAULT_STATE_FILE
            else:
                state_file = "state.json"

        # Resolve full path (resolve_path handles absolute vs relative)
        target_path = resolve_path(str(state_path), state_file)
        if create_path and not target_path.parent.exists():
            target_path.parent.mkdir(parents=True, exist_ok=True)

        return target_path

    def get_deploy_log_path(self, work_path: Path, create_path: bool = True) -> Path:
        """Get the deploy-log output path.

        Resolution order:
        1. ``spec.deployment.audit.path`` (from configuration YAML — when the audit
           config model is implemented this field will be read here)
        2. ``config.SOLUTION_DIR / config.SOLUTION_DEPLOY_LOG_DIR`` (constant fallback)

        Args:
            work_path:   Workspace root path.
            create_path: When True, create the directory if it does not exist.
        """
        deploy_log_path = f"{config.SOLUTION_DIR}/{config.SOLUTION_DEPLOY_LOG_DIR}"

        target_path = resolve_path(str(work_path), deploy_log_path)
        if create_path and not target_path.exists():
            target_path.mkdir(parents=True, exist_ok=True)

        return target_path

    def get_temp_path(self, work_path: Path, create_path: bool) -> Path:
        """Get a temporary path for intermediate files."""
        temp_dir = self.get_default_state_path(work_path=work_path, create_path=False)

        if create_path and not temp_dir.exists():
            temp_dir.mkdir(parents=True, exist_ok=True)

        if not temp_dir.exists():
            raise PlatformFileNotFoundError(f"Failed to create temp directory at {temp_dir}")

        return temp_dir

    def get_temp_configuration_path(self, work_path: Path, create_path: bool) -> Path:
        """Get a temporary path for merged configuration files."""
        temp_path = self.get_temp_path(work_path, create_path)
        config_temp_path = temp_path / "configuration.yaml"
        return config_temp_path

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
                self.logger.debug("Using absolute logging config path", path=logging_path)
                return str(Path(logging_path).resolve())
            else:
                self.logger.warning("Logging config file not found", path=logging_path)
                return None

        # Handle relative paths (relative to workspace root)
        full_path = work_path / logging_path
        if full_path.exists():
            self.logger.debug("Using relative logging config path", path=str(full_path))
            return str(full_path.resolve())
        else:
            self.logger.warning(
                "Logging config file not found relative to workspace",
                path=str(full_path),
                work_path=str(work_path),
            )
            return None
