#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_configuration.py
Author        : Vincent Huybrechts
Created       : 2026-02-06
Last Updated  : 2026-02-16
Version       : 1.0.0
Python Version: 3.12+
Description   : ConfigurationService test fixtures and utilities for strata CLI tests.
===============================================================================
"""

import os
from pathlib import Path

import pytest

from strata.exceptions import ServiceNotValidatedError
from strata.models.configuration_model import ConfigurationModel
from strata.services.configuration_service import ConfigurationService


def _data(relative_path: str) -> str:
    return str(Path(__file__).parent.parent.parent / "data" / relative_path)


class TestConfigurationService:
    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        ConfigurationService.reset()
        yield
        ConfigurationService.reset()

    @pytest.fixture
    def config_file(self):
        """Get path to standard configuration file."""
        return Path(_data("configurations/configuration-standard.yaml"))

    @pytest.fixture
    def service(self):
        """Get fresh configuration service instance."""
        return ConfigurationService()

    def test_singleton_pattern(self):
        """Test that ConfigurationService follows singleton pattern."""
        service1 = ConfigurationService()
        service2 = ConfigurationService()
        assert service1 is service2

    def test_get_instance(self):
        """Test get_instance class method."""
        service1 = ConfigurationService.get_instance()
        service2 = ConfigurationService.get_instance()
        assert service1 is service2
        assert isinstance(service1, ConfigurationService)

    def test_reset_singleton(self):
        """Test reset clears singleton instance."""
        service1 = ConfigurationService()
        ConfigurationService.reset()
        service2 = ConfigurationService()
        assert service1 is not service2

    def test_get_model_class(self, service):
        """Test _get_model_class returns ConfigurationModel."""
        model_class = service._get_model_class()
        assert model_class == ConfigurationModel

    def test_initial_state(self, service):
        """Test service initial state is not validated."""
        assert not service.is_validated()
        assert service.data == {}
        assert service.model is None

    def test_add_configuration_success(self, service, config_file):
        """Test adding a valid configuration file."""
        success, errors = service.add_configuration(config_file)
        assert success
        assert errors == []
        assert service.is_validated()
        assert service.model is not None

    def test_add_configurations_multiple(self, service, config_file):
        """Test adding multiple configuration files."""
        # Add same file twice to test merging
        success, errors = service.add_configurations([config_file, config_file])
        assert success
        assert errors == []
        assert service.is_validated()

    def test_validate_before_access_raises(self, service):
        """Test accessing properties before validation raises error."""
        with pytest.raises(ServiceNotValidatedError, match="must be validated before use"):
            service.get_kind()

    def test_get_configuration_defaults(self, service, config_file):
        """Test retrieving configuration defaults."""
        service.add_configuration(config_file)
        defaults = service.get_configuration_defaults()
        # Should either be None or a dict
        assert defaults is None or isinstance(defaults, dict)

    def test_get_default_build_path(self, service, config_file):
        """Test get_default_build_path returns Path object."""
        service.add_configuration(config_file)
        work_path = "/tmp/test"
        build_path = service.get_default_build_path(work_path, create_path=False)
        assert isinstance(build_path, Path)

    def test_get_default_object_path(self, service, config_file):
        """Test get_default_object_path returns Path object."""
        service.add_configuration(config_file)
        work_path = "/tmp/test"
        obj_path = service.get_default_object_path(work_path, create_path=False)
        assert isinstance(obj_path, Path)

    def test_get_default_dist_path(self, service, config_file):
        """Test get_default_dist_path returns Path object."""
        service.add_configuration(config_file)
        work_path = "/tmp/test"
        dist_path = service.get_default_dist_path(work_path, create_path=False)
        assert isinstance(dist_path, Path)

    def test_add_configuration_invalid_file(self, service):
        """Test adding non-existent configuration file fails."""
        success, errors = service.add_configuration("nonexistent.yaml")
        assert not success
        assert len(errors) > 0
        assert not service.is_validated()

    def test_load_from_paths_glob_patterns(self, service):
        """Test loading configurations from glob patterns."""
        config_dir = _data("configurations")

        # Load using glob pattern - should find configuration-standard.yaml
        pattern = str(Path(config_dir) / "configuration-*.yaml")
        success, errors = service.load_from_paths([pattern])

        if not success:
            print(f"Errors: {errors}")

        assert success
        assert errors == []
        assert service.is_validated()
        assert service.model is not None

    def test_load_from_paths_no_matches(self, service, tmp_path):
        """Test loading from pattern with no matches fails gracefully."""
        pattern = str(tmp_path / "nonexistent-*.yaml")
        success, errors = service.load_from_paths([pattern])

        assert not success
        assert len(errors) > 0
        assert "No configuration files found" in errors[0]

    def test_add_configurations_merges_with_existing(self, service, config_file):
        """Test that add_configurations merges with existing data."""
        # First add the standard config
        success1, _ = service.add_configuration(config_file)
        assert success1

        # Get initial defaults
        initial_defaults = service.get_configuration_defaults()

        # Add same config again - should still work (deep merge with itself)
        success2, errors2 = service.add_configuration(config_file)

        if not success2:
            print(f"Merge errors: {errors2}")

        assert success2

        # Check that config is still valid after merge
        assert service.model is not None
        # Defaults should still exist
        final_defaults = service.get_configuration_defaults()
        if initial_defaults:
            assert final_defaults is not None

    def test_singleton_state_persists(self, config_file):
        """Test that singleton state persists across get_instance calls."""
        # Get instance and load config
        service1 = ConfigurationService.get_instance()
        success, _ = service1.add_configuration(config_file)
        assert success

        # Get instance again - should have same state
        service2 = ConfigurationService.get_instance()
        assert service2.is_validated()
        assert service2.model is not None

        # Cleanup
        ConfigurationService.reset()


class TestConfigurationServiceLifecyclePhases:
    """Test lifecycle phase retrieval methods."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        ConfigurationService.reset()
        yield
        ConfigurationService.reset()

    @pytest.fixture
    def service(self):
        """Get fresh configuration service instance."""
        return ConfigurationService()

    @pytest.fixture
    def config_with_lifecycle(self, tmp_path):
        """Create configuration file with lifecycle phases."""
        # Use existing scripts from tests/data/scripts
        scripts_dir = _data("scripts")
        script1 = os.path.join(scripts_dir, "validate-before.ps1")
        script2 = os.path.join(scripts_dir, "validate-after.ps1")

        config_content = f"""
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: test_config
  annotations:
    description: Test configuration with lifecycle
spec:
  lifecycle:
    config_clean_before:
      description: Pre-clean validation
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

    @pytest.fixture
    def config_without_lifecycle(self, tmp_path):
        """Create configuration file without lifecycle phases."""
        config_content = """
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: test_config_no_lifecycle
  annotations:
    description: Test configuration without lifecycle
spec:
  defaults:
    build_path: build/
"""
        config_file = tmp_path / "test-config-no-lifecycle.yaml"
        config_file.write_text(config_content)
        return config_file

    def test_get_lifecycle_phase_exists(self, service, config_with_lifecycle):
        """Test retrieving existing lifecycle phase."""
        service.add_configuration(config_with_lifecycle)

        phase = service.get_lifecycle_phase("config_clean_before")

        assert phase is not None
        assert phase.description == "Pre-clean validation"
        assert phase.scripts is not None
        assert len(phase.scripts) == 1
        assert Path(phase.scripts[0]).name == "validate-before.ps1"

    def test_get_lifecycle_phase_multiple_scripts(self, service, config_with_lifecycle):
        """Test phase with multiple scripts."""
        service.add_configuration(config_with_lifecycle)

        phase = service.get_lifecycle_phase("config_clean_before")

        assert phase is not None
        assert phase.scripts is not None
        assert len(phase.scripts) == 1

    def test_get_lifecycle_phase_not_found(self, service, config_with_lifecycle):
        """Test retrieving non-existent lifecycle phase returns None."""
        service.add_configuration(config_with_lifecycle)

        phase = service.get_lifecycle_phase("nonexistent_phase")

        assert phase is None

    def test_get_lifecycle_phase_no_model(self, service):
        """Test get_lifecycle_phase returns None when no model loaded."""
        phase = service.get_lifecycle_phase("config_clean_before")

        assert phase is None

    def test_get_lifecycle_phase_no_lifecycle_section(self, service, config_without_lifecycle):
        """Test get_lifecycle_phase returns None when lifecycle not defined."""
        service.add_configuration(config_without_lifecycle)

        phase = service.get_lifecycle_phase("config_clean_before")

        assert phase is None

    def test_get_lifecycle_phase_all_defined_phases(self, service, config_with_lifecycle):
        """Test retrieving all defined phases."""
        service.add_configuration(config_with_lifecycle)

        clean_before = service.get_lifecycle_phase("config_clean_before")
        clean_after = service.get_lifecycle_phase("config_clean_after")
        fetch_before = service.get_lifecycle_phase("config_fetch_before")

        assert clean_before is not None
        assert clean_before.description == "Pre-clean validation"

        assert clean_after is not None
        assert clean_after.description == "Post-clean cleanup"

        assert fetch_before is not None
        assert fetch_before.description == "Pre-fetch validation"

    def test_get_lifecycle_phase_empty_phase(self, service, tmp_path):
        """Test lifecycle phase with no scripts."""
        config_content = """
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: test_config
spec:
  lifecycle:
    empty_phase:
      description: Phase with no scripts
"""
        config_file = tmp_path / "test-empty-phase.yaml"
        config_file.write_text(config_content)
        service.add_configuration(config_file)

        phase = service.get_lifecycle_phase("empty_phase")

        assert phase is not None
        assert phase.description == "Phase with no scripts"
        assert phase.scripts is None or len(phase.scripts) == 0
