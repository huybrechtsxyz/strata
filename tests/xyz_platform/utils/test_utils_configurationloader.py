#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_utils_configurationloader.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Tests for ConfigurationLoader class (simplified API v2.0).
===============================================================================
"""

from pathlib import Path

import pytest
import yaml

from xyz_platform.utils.configuration_loader import ConfigurationLoader


class TestConfigurationLoader:
    """Test ConfigurationLoader basic functionality."""

    def test_init(self):
        """ConfigurationLoader can be instantiated."""
        loader = ConfigurationLoader()
        assert loader is not None
        assert loader._logger is not None

    def test_load_single_file(self, tmp_path):
        """Load a single configuration file."""
        loader = ConfigurationLoader()

        # Create test config file
        config_file = tmp_path / "config.yaml"
        config_data = {
            "kind": "configuration",
            "meta": {"name": "test-config", "version": "1.0.0"},
            "spec": {"configuration": {"default_value": 42}},
        }

        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        # Load file
        config = loader.load_yaml_file(config_file)

        assert config["kind"] == "configuration"
        assert config["meta"]["name"] == "test-config"
        assert config["spec"]["configuration"]["default_value"] == 42

    def test_load_multiple_files(self, tmp_path):
        """Load multiple files."""
        loader = ConfigurationLoader()

        # Create multiple config files
        file_paths = []
        for i in range(3):
            config_file = tmp_path / f"config-{i}.yaml"
            config_data = {
                "kind": "configuration",
                "meta": {"name": f"config-{i}"},
            }
            with open(config_file, "w") as f:
                yaml.dump(config_data, f)
            file_paths.append(config_file)

        # Load files
        configs = loader.load_yaml_files(file_paths)

        assert len(configs) == 3
        names = [c["meta"]["name"] for c in configs]
        assert names == ["config-0", "config-1", "config-2"]

    def test_load_yaml_file_with_path_object(self, tmp_path):
        """Load file using Path object."""
        loader = ConfigurationLoader()

        config_file = tmp_path / "config.yaml"
        with open(config_file, "w") as f:
            yaml.dump({"kind": "configuration"}, f)

        # Load with Path object (the standard way)
        config = loader.load_yaml_file(config_file)
        assert config["kind"] == "configuration"

    def test_load_invalid_yaml(self, tmp_path):
        """Loading invalid YAML raises exception."""
        loader = ConfigurationLoader()

        # Create invalid YAML file
        config_file = tmp_path / "invalid.yaml"
        with open(config_file, "w") as f:
            f.write("{ invalid yaml content")

        # Should raise exception
        with pytest.raises(yaml.YAMLError):
            loader.load_yaml_file(config_file)

    def test_load_nonexistent_file(self):
        """Loading nonexistent file raises FileNotFoundError."""
        loader = ConfigurationLoader()

        with pytest.raises(FileNotFoundError):
            loader.load_yaml_file(Path("/nonexistent/path/config.yaml"))

    def test_load_empty_yaml_file(self, tmp_path):
        """Loading empty YAML file returns empty dict."""
        loader = ConfigurationLoader()

        config_file = tmp_path / "empty.yaml"
        config_file.touch()  # Create empty file

        config = loader.load_yaml_file(config_file)
        assert config == {}

    def test_load_yaml_with_non_dict_content(self, tmp_path):
        """Loading YAML with non-dict content raises ValueError."""
        loader = ConfigurationLoader()

        config_file = tmp_path / "list.yaml"
        with open(config_file, "w") as f:
            yaml.dump(["item1", "item2"], f)

        with pytest.raises(ValueError, match="must contain a YAML dictionary"):
            loader.load_yaml_file(config_file)

    def test_load_yaml_file_not_a_file(self, tmp_path):
        """Loading a directory raises ValueError."""
        loader = ConfigurationLoader()

        directory = tmp_path / "dir"
        directory.mkdir()

        with pytest.raises(ValueError, match="Path is not a file"):
            loader.load_yaml_file(directory)


class TestConfigurationMerging:
    """Test configuration merging functionality."""

    def test_merge_empty_list(self):
        """Merging empty list returns empty dict."""
        loader = ConfigurationLoader()
        result = loader.merge_configs([])
        assert result == {}

    def test_merge_single_config(self):
        """Merging single config returns copy of that config."""
        loader = ConfigurationLoader()
        config = {"key": "value"}
        result = loader.merge_configs([config])

        assert result == config
        assert result is not config  # Should be a copy

    def test_merge_two_configs_simple(self):
        """Merge two simple configs with non-overlapping keys."""
        loader = ConfigurationLoader()

        config1 = {"a": 1, "b": 2}
        config2 = {"c": 3, "d": 4}

        result = loader.merge_configs([config1, config2])

        assert result == {"a": 1, "b": 2, "c": 3, "d": 4}

    def test_merge_two_configs_override(self):
        """Later config overrides earlier config for same keys."""
        loader = ConfigurationLoader()

        config1 = {"a": 1, "b": 2}
        config2 = {"b": 99, "c": 3}

        result = loader.merge_configs([config1, config2])

        assert result == {"a": 1, "b": 99, "c": 3}

    def test_merge_nested_dicts(self):
        """Nested dictionaries are merged recursively."""
        loader = ConfigurationLoader()

        config1 = {"spec": {"configuration": {"option1": "value1", "option2": "value2"}}}
        config2 = {"spec": {"configuration": {"option2": "override", "option3": "value3"}}}

        result = loader.merge_configs([config1, config2])

        expected = {
            "spec": {
                "configuration": {
                    "option1": "value1",
                    "option2": "override",
                    "option3": "value3",
                }
            }
        }

        assert result == expected

    def test_merge_multiple_configs(self):
        """Merge multiple configs in order."""
        loader = ConfigurationLoader()

        config1 = {"a": 1, "b": {"x": 1}}
        config2 = {"b": {"y": 2}, "c": 3}
        config3 = {"a": 99, "b": {"x": 99}}

        result = loader.merge_configs([config1, config2, config3])

        expected = {"a": 99, "b": {"x": 99, "y": 2}, "c": 3}
        assert result == expected

    def test_merge_override_dict_with_scalar(self):
        """Scalar value overrides dict value."""
        loader = ConfigurationLoader()

        config1 = {"key": {"nested": "value"}}
        config2 = {"key": "scalar"}

        result = loader.merge_configs([config1, config2])

        assert result == {"key": "scalar"}

    def test_merge_override_scalar_with_dict(self):
        """Dict value overrides scalar value."""
        loader = ConfigurationLoader()

        config1 = {"key": "scalar"}
        config2 = {"key": {"nested": "value"}}

        result = loader.merge_configs([config1, config2])

        assert result == {"key": {"nested": "value"}}

    def test_merge_preserves_types(self):
        """Merging preserves data types."""
        loader = ConfigurationLoader()

        config1 = {"int": 42, "float": 3.14, "bool": True, "list": [1, 2, 3]}
        config2 = {"string": "text", "none": None}

        result = loader.merge_configs([config1, config2])

        assert result["int"] == 42
        assert result["float"] == 3.14
        assert result["bool"] is True
        assert result["list"] == [1, 2, 3]
        assert result["string"] == "text"
        assert result["none"] is None

    def test_deep_merge_directly(self):
        """Test deep_merge method directly."""
        loader = ConfigurationLoader()

        base = {"a": 1, "b": {"x": 1, "y": 2}}
        override = {"b": {"y": 3, "z": 4}, "c": 5}

        result = loader.deep_merge(base, override)

        expected = {"a": 1, "b": {"x": 1, "y": 3, "z": 4}, "c": 5}
        assert result == expected

    def test_apply_overrides(self):
        """Test apply_overrides method (alias for deep_merge)."""
        loader = ConfigurationLoader()

        base = {"env": "dev", "config": {"debug": True}}
        overrides = {"config": {"debug": False, "verbose": True}}

        result = loader.apply_overrides(base, overrides)

        expected = {"env": "dev", "config": {"debug": False, "verbose": True}}
        assert result == expected


class TestLoadAndMergeYamlFiles:
    """Test the load_and_merge_yaml_files convenience method."""

    def test_load_and_merge_yaml_files(self, tmp_path):
        """Load and merge files in one call."""
        loader = ConfigurationLoader()

        # Create config files
        config1 = tmp_path / "base.yaml"
        config2 = tmp_path / "override.yaml"

        with open(config1, "w") as f:
            yaml.dump({"kind": "configuration", "meta": {"name": "base"}}, f)

        with open(config2, "w") as f:
            yaml.dump({"kind": "configuration", "meta": {"name": "override"}}, f)

        # Load and merge
        result = loader.load_and_merge_yaml_files([config1, config2])

        assert result["kind"] == "configuration"
        # Last file wins (override)
        assert result["meta"]["name"] == "override"

    def test_load_and_merge_empty_list(self):
        """Load and merge with empty list returns empty dict."""
        loader = ConfigurationLoader()

        result = loader.load_and_merge_yaml_files([])
        assert result == {}

    def test_load_and_merge_single_file(self, tmp_path):
        """Load and merge single file."""
        loader = ConfigurationLoader()

        config_file = tmp_path / "config.yaml"
        with open(config_file, "w") as f:
            yaml.dump({"key": "value"}, f)

        result = loader.load_and_merge_yaml_files([config_file])
        assert result == {"key": "value"}

    def test_load_and_merge_with_nested_overrides(self, tmp_path):
        """Load and merge with nested dictionary overrides."""
        loader = ConfigurationLoader()

        config1 = tmp_path / "base.yaml"
        config2 = tmp_path / "override.yaml"

        with open(config1, "w") as f:
            yaml.dump({"spec": {"option1": "value1", "option2": "value2"}}, f)

        with open(config2, "w") as f:
            yaml.dump({"spec": {"option2": "override", "option3": "value3"}}, f)

        result = loader.load_and_merge_yaml_files([config1, config2])

        expected = {"spec": {"option1": "value1", "option2": "override", "option3": "value3"}}
        assert result == expected


class TestConfigurationLoaderEdgeCases:
    """Test edge cases and error handling."""

    def test_load_yaml_files_empty_list(self):
        """Loading empty list returns empty list."""
        loader = ConfigurationLoader()
        configs = loader.load_yaml_files([])
        assert configs == []

    def test_merge_does_not_modify_originals(self):
        """Merging configs does not modify original dicts."""
        loader = ConfigurationLoader()

        config1 = {"a": 1, "b": {"x": 1}}
        config2 = {"b": {"y": 2}}

        original_config1 = config1.copy()
        original_config2 = config2.copy()

        result = loader.merge_configs([config1, config2])

        # Originals should be unchanged
        assert config1 == original_config1
        assert config2 == original_config2
        # Result should be different
        assert result != config1
        assert result != config2

    def test_deep_merge_does_not_modify_originals(self):
        """Deep merge does not modify original dicts."""
        loader = ConfigurationLoader()

        base = {"a": 1, "b": {"x": 1}}
        override = {"b": {"y": 2}}

        original_base = {"a": 1, "b": {"x": 1}}
        original_override = {"b": {"y": 2}}

        result = loader.deep_merge(base, override)

        # Originals should be unchanged
        assert base == original_base
        assert override == original_override
        # Result should be new
        assert result is not base
        assert result is not override

    def test_load_yaml_file_encoding(self, tmp_path):
        """Test loading file with UTF-8 encoding."""
        loader = ConfigurationLoader()

        config_file = tmp_path / "unicode.yaml"
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump({"name": "café", "emoji": "🚀"}, f)

        config = loader.load_yaml_file(config_file)
        assert config["name"] == "café"
        assert config["emoji"] == "🚀"

    def test_load_multiple_files_maintains_order(self, tmp_path):
        """Load multiple files maintains order."""
        loader = ConfigurationLoader()

        # Create files
        files = []
        for i in range(5):
            config_file = tmp_path / f"config-{i}.yaml"
            with open(config_file, "w") as f:
                yaml.dump({"order": i}, f)
            files.append(config_file)

        configs = loader.load_yaml_files(files)

        # Should maintain input order
        for i, config in enumerate(configs):
            assert config["order"] == i
