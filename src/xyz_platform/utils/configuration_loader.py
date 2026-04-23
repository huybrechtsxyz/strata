"""Low-level YAML file loader and merger.

Handles file I/O and data merging only.
No knowledge of schemas, validation, or file selection strategy.
"""

from pathlib import Path
from typing import Any, Dict, List

import yaml

from xyz_platform.logger import get_logger


class ConfigurationLoader:
    """
    Low-level YAML file loader and merger.

    Responsibilities:
    - Load YAML files from disk
    - Deep merge dictionaries
    - Handle file I/O errors

    Does NOT:
    - Know about configuration schemas
    - Decide which files to load
    - Validate configuration content
    - Manage state or caching
    - Handle file selection or glob patterns
    """

    def __init__(self):
        """Initialize the configuration loader."""
        self._logger = get_logger(__name__)

    def apply_overrides(self, base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply overrides to a base configuration (alias for deep_merge for clarity).

        Use this when applying environment-specific overrides or other layered configs.

        Args:
            base: Base configuration dictionary
            overrides: Override values to apply (takes precedence)

        Returns:
            Merged configuration dictionary (new dict, originals unchanged)
        """
        return self.deep_merge(base, overrides)

    def load_yaml_file(self, file_path: Path) -> Dict[str, Any]:
        """
        Load a single YAML file.

        Args:
            file_path: Path to the YAML file

        Returns:
            Loaded configuration dictionary (empty dict if file is empty)

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If file is not a valid YAML dictionary
            yaml.YAMLError: If YAML parsing fails
        """
        if not file_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {file_path}")

        if not file_path.is_file():
            raise ValueError(f"Path is not a file: {file_path}")

        self._logger.debug("Loading YAML file", file=str(file_path))

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            self._logger.error(
                "YAML parsing failed",
                exc_info=True,
                file=str(file_path),
                error=str(e),
            )
            raise

        if config is None:
            self._logger.debug("YAML file is empty", file=str(file_path))
            return {}

        if not isinstance(config, dict):
            raise ValueError(
                f"Configuration file must contain a YAML dictionary, got {type(config).__name__}: {file_path}"
            )

        return config

    def load_yaml_files(self, file_paths: List[Path]) -> List[Dict[str, Any]]:
        """
        Load multiple YAML files.

        Args:
            file_paths: List of paths to YAML files

        Returns:
            List of loaded configuration dictionaries

        Raises:
            FileNotFoundError: If any file doesn't exist
            ValueError: If any file is not a valid YAML dictionary
            yaml.YAMLError: If YAML parsing fails
        """
        configs = []

        for file_path in file_paths:
            config = self.load_yaml_file(file_path)
            configs.append(config)

        self._logger.debug(
            "Loaded YAML files",
            file_count=len(configs),
        )

        return configs

    def load_and_merge_yaml_files(self, file_paths: List[Path]) -> Dict[str, Any]:
        """
        Load multiple YAML files and merge them into a single configuration.

        Convenience method that combines load_yaml_files() and merge_configs().

        Args:
            file_paths: List of paths to YAML files (in priority order - later files override earlier)

        Returns:
            Single merged configuration dictionary

        Raises:
            FileNotFoundError: If any file doesn't exist
            ValueError: If any file is not a valid YAML dictionary
            yaml.YAMLError: If YAML parsing fails
        """
        configs = self.load_yaml_files(file_paths)
        return self.merge_configs(configs)

    def merge_configs(self, configs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Deep merge multiple configuration dictionaries.

        Later configurations override earlier ones.
        Nested dictionaries are merged recursively.

        Args:
            configs: List of configuration dictionaries to merge (in priority order)

        Returns:
            Single merged configuration dictionary

        Example:
            >>> loader = ConfigurationLoader()
            >>> configs = [
            ...     {"a": 1, "b": {"x": 1, "y": 2}},       # Base config
            ...     {"b": {"y": 3, "z": 4}, "c": 5}        # Override config
            ... ]
            >>> loader.merge_configs(configs)
            {'a': 1, 'b': {'x': 1, 'y': 3, 'z': 4}, 'c': 5}
        """
        if not configs:
            self._logger.debug("No configurations to merge")
            return {}

        if len(configs) == 1:
            # Single config, no merging needed
            return configs[0].copy()

        self._logger.debug(
            "Merging configurations",
            config_count=len(configs),
        )

        # Start with empty dict
        merged: Dict[str, Any] = {}

        # Merge each config in order (later configs override earlier ones)
        for config in configs:
            merged = self.deep_merge(merged, config)

        self._logger.debug(
            "Configurations merged successfully",
            config_count=len(configs),
        )

        return merged

    def deep_merge(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """
        Recursively merge override dictionary into base dictionary.

        Args:
            base: Base configuration dictionary
            override: Override configuration dictionary (takes precedence)

        Returns:
            Merged configuration dictionary (new dict, originals unchanged)
        """
        result = base.copy()

        for key, value in override.items():
            if key in result:
                # If both values are dicts, merge recursively
                if isinstance(result[key], dict) and isinstance(value, dict):
                    result[key] = self.deep_merge(result[key], value)
                else:
                    # Otherwise, override takes precedence
                    result[key] = value
            else:
                # New key, just add it
                result[key] = value

        return result
