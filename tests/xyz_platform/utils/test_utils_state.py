#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_utils_state.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.11+
Description   : Unit tests for WorkspaceState utility class.
===============================================================================
"""

import json
import time
import pytest
from datetime import datetime

from xyz_platform.utils.state import WorkspaceState


@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace directory."""
    workspace = tmp_path / "test_workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def workspace_state(temp_workspace):
    """Create a WorkspaceState instance with temporary workspace."""
    return WorkspaceState(str(temp_workspace))


class TestWorkspaceStateInitialization:
    """Tests for WorkspaceState initialization."""

    def test_init_creates_state_directory(self, temp_workspace):
        """Test that initialization creates the .xyz-platform directory."""
        state = WorkspaceState(str(temp_workspace))

        assert state.state_dir.exists()
        assert state.state_dir.is_dir()
        assert state.state_dir.name == ".xyz-platform"

    def test_init_creates_nested_paths(self, temp_workspace):
        """Test that state paths are correctly set."""
        state = WorkspaceState(str(temp_workspace))

        expected_state_dir = temp_workspace / ".xyz-platform"
        expected_state_file = expected_state_dir / "state.json"

        assert state.state_dir == expected_state_dir
        assert state.state_file == expected_state_file

    def test_init_adds_to_existing_gitignore(self, temp_workspace):
        """Test that .xyz-platform/ is added to existing .gitignore."""
        gitignore = temp_workspace / ".gitignore"
        gitignore.write_text("# Existing content\n*.pyc\n")

        WorkspaceState(str(temp_workspace))

        content = gitignore.read_text()
        assert ".xyz-platform/" in content
        assert "*.pyc" in content  # Original content preserved

    def test_init_does_not_duplicate_gitignore_entry(self, temp_workspace):
        """Test that .xyz-platform/ is not duplicated in .gitignore."""
        gitignore = temp_workspace / ".gitignore"
        gitignore.write_text("# Existing\n.xyz-platform/\n")

        WorkspaceState(str(temp_workspace))

        content = gitignore.read_text()
        assert content.count(".xyz-platform/") == 1

    def test_init_without_gitignore(self, temp_workspace):
        """Test that initialization works when .gitignore doesn't exist."""
        gitignore = temp_workspace / ".gitignore"
        assert not gitignore.exists()

        state = WorkspaceState(str(temp_workspace))

        assert state.state_dir.exists()
        # Implementation only adds to existing .gitignore
        assert not gitignore.exists()

    def test_init_loads_existing_state(self, temp_workspace):
        """Test that initialization loads existing state from disk."""
        state1 = WorkspaceState(str(temp_workspace))
        state1.set("persistent_key", "persistent_value")
        state1.save()

        # Create new instance - should load existing state
        state2 = WorkspaceState(str(temp_workspace))
        assert state2.get("persistent_key") == "persistent_value"


class TestWorkspaceStateGet:
    """Tests for getting state values."""

    def test_get_returns_none_for_missing_key(self, workspace_state):
        """Test that get returns None for non-existent keys."""
        result = workspace_state.get("nonexistent")
        assert result is None

    def test_get_returns_default_for_missing_key(self, workspace_state):
        """Test that get returns provided default for non-existent keys."""
        result = workspace_state.get("nonexistent", "default_value")
        assert result == "default_value"

    def test_get_returns_stored_value(self, workspace_state):
        """Test that get returns previously set values."""
        workspace_state.set("test_key", "test_value")
        result = workspace_state.get("test_key")
        assert result == "test_value"

    def test_get_works_with_different_types(self, workspace_state):
        """Test that get works with various data types."""
        test_values = {
            "string": "hello",
            "number": 42,
            "float": 3.14,
            "boolean": True,
            "list": [1, 2, 3],
            "dict": {"nested": "value"},
            "none": None,
        }

        for key, value in test_values.items():
            workspace_state.set(key, value)

        for key, expected in test_values.items():
            assert workspace_state.get(key) == expected


class TestWorkspaceStateSet:
    """Tests for setting state values."""

    def test_set_stores_value_in_memory(self, workspace_state):
        """Test that set stores values in memory (before save)."""
        workspace_state.set("key1", "value1")
        workspace_state.set("key2", "value2")

        assert workspace_state.get("key1") == "value1"
        assert workspace_state.get("key2") == "value2"

        # State file should not exist yet (not saved)
        assert not workspace_state.state_file.exists()

    def test_set_overwrites_existing_value(self, workspace_state):
        """Test that set overwrites existing values."""
        workspace_state.set("key", "old_value")
        workspace_state.set("key", "new_value")

        assert workspace_state.get("key") == "new_value"

    def test_set_adds_last_updated_timestamp(self, workspace_state):
        """Test that set adds last_updated timestamp."""
        workspace_state.set("test_key", "test_value")

        last_updated = workspace_state.get("last_updated")
        assert last_updated is not None

        # Verify it's a valid ISO format timestamp
        parsed_time = datetime.fromisoformat(last_updated)
        assert isinstance(parsed_time, datetime)

    def test_set_updates_timestamp_on_each_call(self, workspace_state):
        """Test that last_updated is updated on each set call."""
        workspace_state.set("key1", "value1")
        first_timestamp = workspace_state.get("last_updated")

        time.sleep(0.01)  # Small delay to ensure different timestamp

        workspace_state.set("key2", "value2")
        second_timestamp = workspace_state.get("last_updated")

        assert first_timestamp != second_timestamp

    def test_set_preserves_other_keys(self, workspace_state):
        """Test that set preserves other keys when updating."""
        workspace_state.set("key1", "value1")
        workspace_state.set("key2", "value2")
        workspace_state.set("key3", "value3")

        assert workspace_state.get("key1") == "value1"
        assert workspace_state.get("key2") == "value2"
        assert workspace_state.get("key3") == "value3"


class TestWorkspaceStateSave:
    """Tests for state save functionality."""

    def test_save_persists_state_to_disk(self, workspace_state):
        """Test that save writes state to disk."""
        workspace_state.set("key", "value")
        assert not workspace_state.state_file.exists()

        workspace_state.save()
        assert workspace_state.state_file.exists()

    def test_save_creates_valid_json(self, workspace_state):
        """Test that save creates valid JSON file."""
        workspace_state.set("test_key", "test_value")
        workspace_state.save()

        # Read and parse the file directly
        content = workspace_state.state_file.read_text()
        data = json.loads(content)

        assert isinstance(data, dict)
        assert data["test_key"] == "test_value"

    def test_save_formats_json_with_indentation(self, workspace_state):
        """Test that saved JSON is formatted with indentation."""
        workspace_state.set("test_key", "test_value")
        workspace_state.save()

        content = workspace_state.state_file.read_text()
        # Indented JSON should contain newlines
        assert "\n" in content
        # Should have proper indentation (2 spaces)
        assert "  " in content

    def test_save_without_changes(self, workspace_state):
        """Test that save works even with empty state."""
        workspace_state.save()
        # Should create file even if empty
        assert workspace_state.state_file.exists()

    def test_multiple_save_calls(self, workspace_state):
        """Test that multiple save calls work correctly."""
        workspace_state.set("key1", "value1")
        workspace_state.save()

        workspace_state.set("key2", "value2")
        workspace_state.save()

        # Load to verify both keys persisted
        state2 = WorkspaceState(str(workspace_state._work_path))
        assert state2.get("key1") == "value1"
        assert state2.get("key2") == "value2"


class TestWorkspaceStatePersistence:
    """Tests for state persistence across instances."""

    def test_state_persists_across_instances(self, temp_workspace):
        """Test that state persists when creating new instances."""
        state1 = WorkspaceState(str(temp_workspace))
        state1.set("persistent_key", "persistent_value")
        state1.save()

        # Create new instance with same workspace
        state2 = WorkspaceState(str(temp_workspace))
        assert state2.get("persistent_key") == "persistent_value"

    def test_state_loaded_on_init(self, temp_workspace):
        """Test that state is loaded immediately on initialization."""
        state1 = WorkspaceState(str(temp_workspace))
        state1.set("data", "value")
        state1.save()

        # New instance should load state immediately
        state2 = WorkspaceState(str(temp_workspace))
        # Should work without calling any load method
        assert state2.get("data") == "value"

    def test_multiple_updates_maintain_consistency(self, temp_workspace):
        """Test that multiple updates maintain state consistency."""
        state = WorkspaceState(str(temp_workspace))

        # Perform multiple operations
        state.set("counter", 0)
        state.set("counter", 1)
        state.set("counter", 2)
        state.set("other", "value")
        state.save()

        # Create new instance and verify final state
        state2 = WorkspaceState(str(temp_workspace))
        assert state2.get("counter") == 2
        assert state2.get("other") == "value"


class TestWorkspaceStateClear:
    """Tests for state clear functionality."""

    def test_clear_removes_state_file(self, workspace_state):
        """Test that clear removes the state file."""
        workspace_state.set("key", "value")
        workspace_state.save()
        assert workspace_state.state_file.exists()

        workspace_state.clear()
        assert not workspace_state.state_file.exists()

    def test_clear_resets_memory(self, workspace_state):
        """Test that clear resets internal state."""
        workspace_state.set("key", "value")
        workspace_state.save()

        workspace_state.clear()
        assert workspace_state.get("key") is None

    def test_clear_when_no_state_exists(self, workspace_state):
        """Test that clear works when no state file exists."""
        workspace_state.clear()  # Should not error
        assert not workspace_state.state_file.exists()

    def test_state_usable_after_clear(self, workspace_state):
        """Test that state can be used after clear."""
        workspace_state.set("key1", "value1")
        workspace_state.save()

        workspace_state.clear()

        workspace_state.set("key2", "value2")
        workspace_state.save()

        assert workspace_state.get("key1") is None
        assert workspace_state.get("key2") == "value2"


class TestWorkspaceStateEdgeCases:
    """Tests for edge cases and error conditions."""

    def test_handles_special_characters_in_values(self, workspace_state):
        """Test that special characters in values are handled correctly."""
        special_values = {
            "unicode": "Hello 世界 🌍",
            "quotes": 'String with "quotes"',
            "newlines": "Line1\nLine2\nLine3",
            "backslashes": "C:\\path\\to\\file",
        }

        for key, value in special_values.items():
            workspace_state.set(key, value)
        workspace_state.save()

        # Load in new instance to verify round-trip
        state2 = WorkspaceState(str(workspace_state._work_path))
        for key, expected in special_values.items():
            assert state2.get(key) == expected

    def test_handles_empty_string_key(self, workspace_state):
        """Test behavior with empty string as key."""
        workspace_state.set("", "empty_key_value")
        workspace_state.save()

        state2 = WorkspaceState(str(workspace_state._work_path))
        assert state2.get("") == "empty_key_value"

    def test_handles_empty_string_value(self, workspace_state):
        """Test storing empty string as value."""
        workspace_state.set("empty_value", "")
        workspace_state.save()

        state2 = WorkspaceState(str(workspace_state._work_path))
        assert state2.get("empty_value") == ""

    def test_handles_deeply_nested_structures(self, workspace_state):
        """Test storing deeply nested data structures."""
        nested = {"level1": {"level2": {"level3": {"level4": ["a", "b", "c"]}}}}

        workspace_state.set("nested", nested)
        workspace_state.save()

        state2 = WorkspaceState(str(workspace_state._work_path))
        result = state2.get("nested")
        assert result == nested
        assert result["level1"]["level2"]["level3"]["level4"] == ["a", "b", "c"]

    def test_handles_corrupted_state_file(self, temp_workspace):
        """Test that corrupted state file returns empty state."""
        state = WorkspaceState(str(temp_workspace))
        state.state_file.write_text("not valid json {{{")

        # Should load empty state without crashing
        state2 = WorkspaceState(str(temp_workspace))
        assert state2.get("anything") is None

    def test_handles_missing_state_directory(self, temp_workspace):
        """Test that missing state directory is created."""
        state_dir = temp_workspace / ".xyz-platform"
        assert not state_dir.exists()

        state = WorkspaceState(str(temp_workspace))
        assert state_dir.exists()
