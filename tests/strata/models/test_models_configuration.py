#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_models_configuration.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Tests for configuration models in strata.
===============================================================================
"""

import os
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from strata.models.configuration_model import ConfigurationModel


@pytest.fixture(autouse=True)
def set_pythonpath_env(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "src")


CONFIGURATION_FOLDER = os.path.join(os.path.dirname(__file__), "..", "..", "data", "configurations")

SCRIPTS_FOLDER = os.path.join(os.path.dirname(__file__), "..", "..", "data", "scripts")

# List of YAML files to test (extensible)
CONFIGURATION_VALID_FILES = [
    os.path.join(CONFIGURATION_FOLDER, "configuration-standard.yaml"),
    os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "config", "xyz-configuration", "config", "xyz-config.yaml"
    ),
]

# List of invalid YAML files to test (extensible)
CONFIGURATION_INVALID_FILES = [
    os.path.join(CONFIGURATION_FOLDER, "invalid-configuration.yaml"),
]


@pytest.mark.parametrize("yaml_path", CONFIGURATION_VALID_FILES)
def test_configuration_yaml_valid(yaml_path):
    """Test that a configuration YAML file is a valid ConfigurationModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    model = ConfigurationModel.model_validate(data)
    assert model is not None


@pytest.mark.parametrize("yaml_path", CONFIGURATION_INVALID_FILES)
def test_configuration_yaml_invalid(yaml_path):
    """Test that a configuration YAML file is NOT a valid ConfigurationModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    with pytest.raises(ValidationError):
        ConfigurationModel.model_validate(data)
    model = None
    assert model is None


class TestConfigurationLifecycleModel:
    """Test lifecycle phase models in ConfigurationModel."""

    @pytest.fixture
    def valid_lifecycle_config(self, tmp_path):
        """Create valid configuration YAML with lifecycle phases."""
        # Create dummy script files
        script1 = tmp_path / "validate.ps1"
        script1.write_text("Write-Host 'Validate'\nexit 0")
        script2 = tmp_path / "cleanup.ps1"
        script2.write_text("Write-Host 'Cleanup'\nexit 0")

        config_content = f"""
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: test_config
spec:
  lifecycle:
    config_clean_before:
      description: Pre-clean phase
      scripts:
        - {script1}
    config_clean_after:
      description: Post-clean cleanup
      scripts:
        - {script2}
    config_fetch_before:
      description: Pre-fetch validation
"""
        config_file = tmp_path / "test-config.yaml"
        config_file.write_text(config_content)
        return config_file

    def test_lifecycle_phase_with_scripts(self, tmp_path):
        """Test valid lifecycle phase with scripts."""
        # Use existing script from tests/data/scripts
        script_path = os.path.join(SCRIPTS_FOLDER, "validate-before.ps1")

        config_content = f"""
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: test_config
spec:
  lifecycle:
    config_clean_before:
      description: Pre-clean phase
      scripts:
        - {script_path}
"""
        config_file = tmp_path / "test-config.yaml"
        config_file.write_text(config_content)

        with open(config_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        model = ConfigurationModel.model_validate(data)

        assert model.spec.lifecycle is not None
        phase = model.spec.lifecycle.root.get("config_clean_before")
        assert phase is not None
        assert phase.description == "Pre-clean phase"
        assert len(phase.scripts) == 1
        assert Path(phase.scripts[0]).name == "validate-before.ps1"

    def test_lifecycle_phase_without_scripts(self, tmp_path):
        """Test lifecycle phase with only description."""
        config_content = """
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: test_config
spec:
  lifecycle:
    empty_phase:
      description: Empty phase
"""
        config_file = tmp_path / "test-config.yaml"
        config_file.write_text(config_content)

        with open(config_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        model = ConfigurationModel.model_validate(data)

        assert model.spec.lifecycle is not None
        phase = model.spec.lifecycle.root.get("empty_phase")
        assert phase is not None
        assert phase.description == "Empty phase"
        assert phase.scripts is None or len(phase.scripts) == 0

    def test_lifecycle_multiple_phases(self, tmp_path):
        """Test configuration with multiple lifecycle phases."""
        # Use existing scripts from tests/data/scripts
        script1 = os.path.join(SCRIPTS_FOLDER, "validate-before.ps1")
        script2 = os.path.join(SCRIPTS_FOLDER, "validate-after.ps1")

        config_content = f"""
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: test_config
spec:
  lifecycle:
    config_clean_before:
      description: Pre-clean
      scripts:
        - {script1}
    config_clean_after:
      description: Post-clean
      scripts:
        - {script2}
    config_fetch_before:
      description: Pre-fetch
"""
        config_file = tmp_path / "test-config.yaml"
        config_file.write_text(config_content)

        with open(config_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        model = ConfigurationModel.model_validate(data)

        assert model.spec.lifecycle is not None
        assert len(model.spec.lifecycle.root) == 3

        clean_before = model.spec.lifecycle.root.get("config_clean_before")
        assert clean_before is not None
        assert clean_before.description == "Pre-clean"
        assert len(clean_before.scripts) == 1

        clean_after = model.spec.lifecycle.root.get("config_clean_after")
        assert clean_after is not None
        assert clean_after.description == "Post-clean"

        fetch_before = model.spec.lifecycle.root.get("config_fetch_before")
        assert fetch_before is not None
        assert fetch_before.description == "Pre-fetch"

    def test_lifecycle_phase_multiple_scripts(self, tmp_path):
        """Test lifecycle phase with multiple scripts."""
        # Use existing scripts from tests/data/scripts
        script1 = os.path.join(SCRIPTS_FOLDER, "validate-before.ps1")
        script2 = os.path.join(SCRIPTS_FOLDER, "validate-after.ps1")
        script3 = os.path.join(SCRIPTS_FOLDER, "mock_validate.sh")

        config_content = f"""
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: test_config
spec:
  lifecycle:
    multi_script_phase:
      description: Phase with multiple scripts
      scripts:
        - {script1}
        - {script2}
        - {script3}
"""
        config_file = tmp_path / "test-config.yaml"
        config_file.write_text(config_content)

        with open(config_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        model = ConfigurationModel.model_validate(data)

        assert model.spec.lifecycle is not None
        phase = model.spec.lifecycle.root.get("multi_script_phase")
        assert phase is not None
        assert len(phase.scripts) == 3
        assert Path(phase.scripts[0]).name == "validate-before.ps1"
        assert Path(phase.scripts[1]).name == "validate-after.ps1"
        assert Path(phase.scripts[2]).name == "mock_validate.sh"

    def test_configuration_without_lifecycle(self, tmp_path):
        """Test configuration without lifecycle section."""
        config_content = """
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: test_config
spec:
  additional_topologies: false
"""
        config_file = tmp_path / "test-config.yaml"
        config_file.write_text(config_content)

        with open(config_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        model = ConfigurationModel.model_validate(data)

        assert model.spec.lifecycle is None or len(model.spec.lifecycle.root) == 0

    def test_lifecycle_phase_script_path_validation(self, tmp_path):
        """Test that script file paths are validated as Path objects."""
        # Use existing scripts from tests/data/scripts
        script1 = os.path.join(SCRIPTS_FOLDER, "validate-before.ps1")
        script2 = os.path.join(SCRIPTS_FOLDER, "mock_validate.sh")

        config_content = f"""
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: test_config
spec:
  lifecycle:
    test_phase:
      scripts:
        - {script1}
        - {script2}
"""
        config_file = tmp_path / "test-config.yaml"
        config_file.write_text(config_content)

        with open(config_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        model = ConfigurationModel.model_validate(data)

        phase = model.spec.lifecycle.root.get("test_phase")
        assert phase is not None

        # Scripts are stored as strings (raw path); verify via Path helper

        assert isinstance(phase.scripts[0], str)
        assert isinstance(phase.scripts[1], str)
        assert Path(phase.scripts[0]).name == "validate-before.ps1"
        assert Path(phase.scripts[1]).name == "mock_validate.sh"

    def test_lifecycle_phase_naming_convention(self, tmp_path):
        """Test that common lifecycle phase names are accepted."""
        common_phases = [
            "config_clean_before",
            "config_clean_after",
            "config_fetch_before",
            "config_fetch_after",
            "config_build_before",
            "config_build_after",
            "custom_phase_name",
        ]

        config_content = """
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: test_config
spec:
  lifecycle:
"""
        # Add all phases
        for phase_name in common_phases:
            config_content += f"    {phase_name}:\n      description: Test {phase_name}\n"

        config_file = tmp_path / "test-config.yaml"
        config_file.write_text(config_content)

        with open(config_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        model = ConfigurationModel.model_validate(data)

        assert model.spec.lifecycle is not None
        assert len(model.spec.lifecycle.root) == len(common_phases)

        # Verify all phases exist
        for phase_name in common_phases:
            phase = model.spec.lifecycle.root.get(phase_name)
            assert phase is not None
            assert phase.description == f"Test {phase_name}"
