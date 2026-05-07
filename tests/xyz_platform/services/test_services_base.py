#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_base.py
Author        : Vincent Huybrechts
Created       : 2026-02-10
Last Updated  : 2026-02-10
Version       : 1.0.0
Python Version: 3.12+
Description   : Unit tests for BaseService.
===============================================================================
"""

from unittest.mock import MagicMock, patch

from pydantic import BaseModel, Field

from xyz_platform.services.base_service import BaseService


# Model for testing
class SampleModel(BaseModel):
    """Sample Pydantic model for testing."""

    kind: str
    meta: dict = Field(default_factory=dict)
    spec: dict = Field(default_factory=dict)


# Concrete implementation for testing
class SampleService(BaseService):
    """Concrete sample service for testing."""

    def _get_model_class(self):
        return SampleModel

    def _validate_dynamic(self, configuration_model=None, work_path=None):
        return True, []


class TestBaseServiceGetData:
    """Test get_data method."""

    def test_get_data_with_validated_model(self, tmp_path):
        """get_data() returns model_dump() when model is validated."""
        test_file = tmp_path / "test.yaml"
        test_file.write_text(
            """
kind: test
meta:
  name: test_service
spec:
  value: 123
"""
        )

        service = SampleService(path=str(test_file))
        service.validate()

        data = service.get_data()

        assert data is not None
        assert data["kind"] == "test"
        assert data["meta"]["name"] == "test_service"
        assert data["spec"]["value"] == 123

    def test_get_data_without_validation(self, tmp_path):
        """get_data() returns raw data when not validated."""
        test_file = tmp_path / "test.yaml"
        test_file.write_text(
            """
kind: test
meta:
  name: raw_service
"""
        )

        service = SampleService(path=str(test_file))
        # Don't validate

        data = service.get_data()

        assert data is not None
        assert data["kind"] == "test"
        assert data["meta"]["name"] == "raw_service"

    def test_get_data_with_in_memory_data(self):
        """get_data() works with in-memory data."""
        test_data = {
            "kind": "test",
            "meta": {"name": "memory_service"},
            "spec": {"key": "value"},
        }

        service = SampleService(data=test_data)
        service.validate()

        data = service.get_data()

        assert data is not None
        assert data["kind"] == "test"
        assert data["meta"]["name"] == "memory_service"
        assert data["spec"]["key"] == "value"

    def test_get_data_returns_none_when_no_data(self):
        """get_data() returns None when service has no data."""
        with patch.object(SampleService, "_load_data"):
            service = SampleService.__new__(SampleService)
            service.data = None
            service.model = None
            service._validated = False

            data = service.get_data()

            assert data is None


class TestBaseServiceReloadData:
    """Test reload_data method."""

    def test_reload_data_with_validation(self, tmp_path):
        """reload_data() loads new data and validates."""
        test_file = tmp_path / "test.yaml"
        test_file.write_text(
            """
kind: test
meta:
  name: original
"""
        )

        service = SampleService(path=str(test_file))
        service.validate()

        # Reload with new data
        new_data = {
            "kind": "test",
            "meta": {"name": "updated"},
            "spec": {"new_field": "new_value"},
        }

        success, errors = service.reload_data(new_data)

        assert success is True
        assert len(errors) == 0
        assert service.is_validated()
        assert service.get_data()["meta"]["name"] == "updated"
        assert service.get_data()["spec"]["new_field"] == "new_value"

    def test_reload_data_without_validation(self):
        """reload_data() can skip validation."""
        test_data = {"kind": "test", "meta": {"name": "original"}}

        service = SampleService(data=test_data)
        service.validate()

        # Reload without validation
        new_data = {"kind": "test", "meta": {"name": "updated"}}

        success, errors = service.reload_data(new_data, validate=False)

        assert success is True
        assert len(errors) == 0
        assert service.is_validated() is False
        assert service.data["meta"]["name"] == "updated"

    def test_reload_data_with_invalid_data(self):
        """reload_data() returns errors for invalid data."""
        test_data = {"kind": "test", "meta": {"name": "original"}}

        service = SampleService(data=test_data)
        service.validate()

        # Reload with invalid data (missing required field)
        invalid_data = {"meta": {"name": "no_kind"}}  # Missing 'kind'

        success, errors = service.reload_data(invalid_data)

        assert success is False
        assert len(errors) > 0
        assert any("kind" in err.lower() for err in errors)
        assert service.is_validated() is False

    def test_reload_data_resets_validation_state(self):
        """reload_data() resets validation state before reloading."""
        test_data = {"kind": "test", "meta": {"name": "original"}}

        service = SampleService(data=test_data)
        service.validate()

        assert service.is_validated() is True

        # Reload without validation - should reset state
        new_data = {"kind": "test", "meta": {"name": "updated"}}
        service.reload_data(new_data, validate=False)

        assert service.is_validated() is False

    def test_reload_data_with_configuration_model(self):
        """reload_data() passes configuration_model to validation."""
        test_data = {"kind": "test", "meta": {"name": "original"}}

        service = SampleService(data=test_data)

        # Mock configuration model
        config_model = MagicMock()

        new_data = {"kind": "test", "meta": {"name": "updated"}}

        with patch.object(service, "_validate_dynamic") as mock_validate:
            mock_validate.return_value = (True, [])

            success, errors = service.reload_data(new_data, configuration_model=config_model)

            assert success is True
            # Verify _validate_dynamic was called with config_model
            mock_validate.assert_called_once()
            call_kwargs = mock_validate.call_args[1]
            assert call_kwargs["configuration_model"] == config_model

    def test_reload_data_with_work_path(self):
        """reload_data() passes work_path to validation."""
        test_data = {"kind": "test", "meta": {"name": "original"}}

        service = SampleService(data=test_data)

        new_data = {"kind": "test", "meta": {"name": "updated"}}
        work_path = "/some/work/path"
        config_model = MagicMock()

        with patch.object(service, "_validate_dynamic") as mock_validate:
            mock_validate.return_value = (True, [])

            success, errors = service.reload_data(new_data, configuration_model=config_model, work_path=work_path)

            assert success is True
            # Verify _validate_dynamic was called with work_path
            mock_validate.assert_called_once()
            # Use kwargs attribute to access keyword arguments
            assert mock_validate.call_args.kwargs["work_path"] == work_path
            assert mock_validate.call_args.kwargs["configuration_model"] == config_model


class TestBaseServiceDataWorkflow:
    """Test get_data and reload_data workflow together."""

    def test_get_modify_reload_workflow(self, tmp_path):
        """Complete workflow: get data, modify, reload."""
        test_file = tmp_path / "test.yaml"
        test_file.write_text(
            """
kind: test
meta:
  name: original
spec:
  count: 1
"""
        )

        service = SampleService(path=str(test_file))
        service.validate()

        # Get data
        data = service.get_data()
        assert data["spec"]["count"] == 1

        # Modify data
        data["spec"]["count"] = 5
        data["spec"]["new_field"] = "added"

        # Reload
        success, errors = service.reload_data(data)

        assert success is True
        assert len(errors) == 0

        # Verify changes
        updated_data = service.get_data()
        assert updated_data["spec"]["count"] == 5
        assert updated_data["spec"]["new_field"] == "added"

    def test_merge_scenario(self):
        """Test merging data from multiple sources."""
        base_data = {
            "kind": "test",
            "meta": {"name": "base"},
            "spec": {"setting1": "base_value", "setting2": "base_value"},
        }

        service = SampleService(data=base_data)
        service.validate()

        # Get base data
        data = service.get_data()

        # Merge with overrides
        overrides = {"spec": {"setting2": "override_value", "setting3": "new_value"}}

        data["spec"].update(overrides["spec"])

        # Reload with merged data
        success, errors = service.reload_data(data)

        assert success is True

        # Verify merged result
        merged = service.get_data()
        assert merged["spec"]["setting1"] == "base_value"  # From base
        assert merged["spec"]["setting2"] == "override_value"  # Overridden
        assert merged["spec"]["setting3"] == "new_value"  # Added


class TestBaseServiceEdgeCases:
    """Test edge cases for get_data and reload_data."""

    def test_reload_data_preserves_path(self, tmp_path):
        """reload_data() preserves original path."""
        test_file = tmp_path / "test.yaml"
        test_file.write_text(
            """
kind: test
meta:
  name: test
"""
        )

        service = SampleService(path=str(test_file))
        original_path = service.path

        new_data = {"kind": "test", "meta": {"name": "updated"}}
        service.reload_data(new_data)

        assert service.path == original_path

    def test_multiple_reload_cycles(self):
        """Service can be reloaded multiple times."""
        test_data = {"kind": "test", "meta": {"name": "v1"}}

        service = SampleService(data=test_data)
        service.validate()

        # Reload multiple times
        for i in range(2, 6):
            new_data = {"kind": "test", "meta": {"name": f"v{i}"}}
            success, errors = service.reload_data(new_data)

            assert success is True
            assert service.get_data()["meta"]["name"] == f"v{i}"

    def test_reload_with_empty_spec(self):
        """reload_data() handles empty spec section."""
        test_data = {"kind": "test", "meta": {"name": "test"}}

        service = SampleService(data=test_data)

        new_data = {"kind": "test", "meta": {"name": "updated"}, "spec": {}}

        success, errors = service.reload_data(new_data)

        assert success is True
        assert service.get_data()["spec"] == {}
