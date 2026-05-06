#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_utils_system.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Unit tests for system utilities in xyz-platform.
===============================================================================
"""

import subprocess
import sys
import pytest
from xyz_platform.utils.system import (
    CommandResult,
    run_command,
)


# ============================================================================
# CommandResult Tests
# ============================================================================


class TestCommandResult:
    """Test CommandResult dataclass."""

    def test_command_result_creation(self):
        """Test creating a CommandResult instance."""
        result = CommandResult(
            returncode=0,
            stdout="output",
            stderr="",
            command="echo test",
            duration_ms=10.5,
        )
        assert result.returncode == 0
        assert result.stdout == "output"
        assert result.stderr == ""
        assert result.command == "echo test"
        assert result.duration_ms == 10.5

    def test_is_successful_property(self):
        """Test is_successful property."""
        # Successful command (returncode 0)
        success = CommandResult(0, "output", "", "echo test", 10.0)
        assert success.is_successful is True

        # Failed command (returncode != 0)
        failure = CommandResult(1, "", "error", "false", 10.0)
        assert failure.is_successful is False

    def test_has_output_property(self):
        """Test has_output property."""
        # Has output
        with_output = CommandResult(0, "output", "", "echo test", 10.0)
        assert with_output.has_output is True

        # No output
        no_output = CommandResult(0, "", "", "true", 10.0)
        assert no_output.has_output is False

    def test_has_errors_property(self):
        """Test has_errors property."""
        # Has errors
        with_errors = CommandResult(1, "", "error", "false", 10.0)
        assert with_errors.has_errors is True

        # No errors
        no_errors = CommandResult(0, "output", "", "echo test", 10.0)
        assert no_errors.has_errors is False

    def test_string_representation_success(self):
        """Test __str__ for successful command."""
        result = CommandResult(0, "output", "", "echo test", 123.45)
        str_repr = str(result)
        assert "SUCCESS" in str_repr
        assert "echo test" in str_repr
        assert "123.45ms" in str_repr

    def test_string_representation_failure(self):
        """Test __str__ for failed command."""
        result = CommandResult(127, "", "not found", "invalid_cmd", 50.0)
        str_repr = str(result)
        assert "FAILED" in str_repr
        assert "exit 127" in str_repr
        assert "invalid_cmd" in str_repr


# ============================================================================
# run_command Tests
# ============================================================================


class TestRunCommand:
    """Test run_command function."""

    def test_successful_command(self):
        """Test running a successful command."""
        result = run_command("echo hello")

        assert isinstance(result, CommandResult)
        assert result.is_successful
        assert result.returncode == 0
        assert "hello" in result.stdout.lower()
        assert result.stderr == ""
        assert result.duration_ms > 0

    def test_failed_command(self):
        """Test running a command that fails."""
        # Use a command that should fail on all platforms
        result = run_command("exit 1" if sys.platform == "win32" else "false")

        assert isinstance(result, CommandResult)
        assert not result.is_successful
        assert result.returncode != 0

    def test_command_with_output(self):
        """Test command that produces output."""
        # Platform-independent test
        result = run_command("echo test123")

        assert result.is_successful
        assert "test123" in result.stdout

    def test_command_with_list_args(self):
        """Test command with list of arguments."""
        # Use Python to echo something (cross-platform)
        result = run_command(["python", "-c", "print('hello world')"])

        assert result.is_successful
        assert "hello world" in result.stdout.lower()

    def test_command_with_check_flag_success(self):
        """Test command with check=True that succeeds."""
        # Should not raise exception
        result = run_command("echo success", check=True)
        assert result.is_successful

    def test_command_with_check_flag_failure(self):
        """Test command with check=True that fails."""
        # Should raise CalledProcessError
        with pytest.raises(subprocess.CalledProcessError):
            if sys.platform == "win32":
                run_command(["cmd", "/c", "exit 1"], check=True)
            else:
                run_command("false", check=True)

    def test_command_with_timeout(self):
        """Test command execution with timeout."""
        # Quick command should complete
        result = run_command("echo fast", timeout=5)
        assert result.is_successful

    def test_command_with_cwd(self):
        """Test command with custom working directory."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            # Use Python to print working directory (cross-platform)
            result = run_command(
                ["python", "-c", "import os; print(os.getcwd())"], cwd=tmpdir
            )
            assert result.is_successful
            # The output should contain the temp directory path
            assert tmpdir.replace("\\", "/") in result.stdout.replace("\\", "/")

    def test_command_result_includes_command(self):
        """Test that CommandResult includes the command that was run."""
        result = run_command("echo testing")
        assert "echo" in result.command.lower()
        assert "testing" in result.command.lower()

    def test_command_result_includes_duration(self):
        """Test that CommandResult includes execution duration."""
        result = run_command("echo test")
        assert result.duration_ms >= 0
        assert isinstance(result.duration_ms, float)

    def test_command_exception_handling(self):
        """Test that exceptions are handled gracefully."""
        # Invalid command should return CommandResult with returncode -1
        result = run_command(["nonexistent_command_xyz_123"])

        assert isinstance(result, CommandResult)
        assert result.returncode == 127
        assert result.has_errors
        assert len(result.stderr) > 0


# ============================================================================
# Integration Tests
# ============================================================================


class TestCommandIntegration:
    """Integration tests combining run_command and CommandResult."""

    def test_run_and_check_success(self):
        """Test running command and checking result."""
        result = run_command("echo integration test")

        assert result.is_successful
        assert result.has_output
        assert not result.has_errors
        assert len(result.stdout) > 0

    def test_run_and_check_failure(self):
        """Test running failed command and checking result."""
        if sys.platform == "win32":
            result = run_command(["cmd", "/c", "exit 42"])
        else:
            result = run_command("sh -c 'exit 42'")

        assert not result.is_successful
        assert result.returncode == 42

    def test_command_result_string_output(self):
        """Test that CommandResult string representation is informative."""
        result = run_command("echo test")
        result_str = str(result)

        # Should contain key information
        assert "echo" in result_str.lower()
        assert "ms" in result_str.lower()  # duration
        assert any(word in result_str for word in ["SUCCESS", "FAILED"])
