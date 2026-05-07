"""Tests for the ``tools`` command group."""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from xyz_platform.commands.cli_tools import tools_group


_SAMPLE_ROWS = [
    {"name": "git", "available": True, "version": "2.40.0", "capabilities": ["IRepositoryTool"], "command": "git"},
    {"name": "docker", "available": False, "version": None, "capabilities": ["IContainerTool"], "command": "docker"},
    {"name": "terraform", "available": True, "version": "1.9.2", "capabilities": ["IInfrastructureTool"], "command": "terraform"},
]

_SAMPLE_DETAIL = {
    "name": "git",
    "command": "git",
    "install_url": "https://git-scm.com/downloads",
    "env_vars": [],
    "auth_methods": [
        {"method": "SSH keys", "description": "Add SSH key to remote."},
    ],
    "yaml_example": None,
    "available": True,
    "version": "2.40.0",
    "capabilities": ["IRepositoryTool"],
}


class TestToolsStatus:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(tools_group, ["status", "--help"])
        assert result.exit_code == 0

    def test_status_exit_0(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.tools.status_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.status.return_value = (True, _SAMPLE_ROWS, [])
            result = runner.invoke(tools_group, ["status", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_status_shows_integration_names(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.tools.status_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.status.return_value = (True, _SAMPLE_ROWS, [])
            result = runner.invoke(tools_group, ["status", "--work-path", str(tmp_path)])
        assert "git" in result.output
        assert "docker" in result.output
        assert "terraform" in result.output

    def test_status_shows_availability_icons(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.tools.status_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.status.return_value = (True, _SAMPLE_ROWS, [])
            result = runner.invoke(tools_group, ["status", "--work-path", str(tmp_path)])
        assert "✓" in result.output
        assert "✗" in result.output

    def test_status_empty_rows(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.tools.status_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.status.return_value = (True, [], [])
            result = runner.invoke(tools_group, ["status", "--work-path", str(tmp_path)])
        assert result.exit_code == 0


class TestToolsCheck:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(tools_group, ["check", "--help"])
        assert result.exit_code == 0
        assert "NAME" in result.output

    def test_check_known_integration_exit_0(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.tools.check_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.check.return_value = (True, _SAMPLE_DETAIL, [])
            result = runner.invoke(tools_group, ["check", "git", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_check_shows_integration_name(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.tools.check_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.check.return_value = (True, _SAMPLE_DETAIL, [])
            result = runner.invoke(tools_group, ["check", "git", "--work-path", str(tmp_path)])
        assert "git" in result.output

    def test_check_shows_install_url(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.tools.check_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.check.return_value = (True, _SAMPLE_DETAIL, [])
            result = runner.invoke(tools_group, ["check", "git", "--work-path", str(tmp_path)])
        assert "git-scm.com" in result.output

    def test_check_unavailable_exits_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.tools.check_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.check.return_value = (
                False,
                {**_SAMPLE_DETAIL, "available": False, "version": None},
                ["Integration 'git' is not available."],
            )
            result = runner.invoke(tools_group, ["check", "git", "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_check_unknown_integration_exits_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.tools.check_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.check.return_value = (
                False,
                {},
                ["Unknown integration: 'xyz_unknown'. Known: git, terraform"],
            )
            result = runner.invoke(tools_group, ["check", "xyz_unknown", "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_check_missing_name_arg_exits_2(self):
        runner = CliRunner()
        result = runner.invoke(tools_group, ["check"])
        assert result.exit_code == 2
