"""Tests for the `status` command."""

import json
from unittest.mock import patch

from click.testing import CliRunner

from strata.commands.cli_status import status_command


class TestStatusCommand:
    def test_basic_invocation_mocked(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.status.show_status_command.StatusCommand.execute", return_value=True):
            result = runner.invoke(status_command, ["--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_json_output_flag_accepted(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.status.show_status_command.StatusCommand.execute", return_value=True):
            result = runner.invoke(status_command, ["--work-path", str(tmp_path), "--output", "json"])
        assert result.exit_code == 0

    def test_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.status.show_status_command.StatusCommand.execute", return_value=False):
            result = runner.invoke(status_command, ["--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_json_output_contains_readiness_block(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(status_command, ["--work-path", str(tmp_path), "--output", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "data" in data
        assert "readiness" in data["data"]
        readiness = data["data"]["readiness"]
        assert "phases_complete" in readiness
        assert "phases_total" in readiness
        assert "complete" in readiness
        assert "checklist" in readiness
        assert "next_step" in readiness

    def test_readiness_checklist_has_phases(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(status_command, ["--work-path", str(tmp_path), "--output", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        checklist = data["data"]["readiness"]["checklist"]
        assert isinstance(checklist, list)
        assert len(checklist) > 0
        for item in checklist:
            assert "phase" in item
            assert "label" in item
            assert "status" in item


class TestSlnStatusCommand:
    def test_sln_status_basic(self, tmp_path):
        from strata.commands.cli_sln import sln_group

        runner = CliRunner()
        with patch("strata.commands.status.show_status_command.StatusCommand.execute", return_value=True):
            result = runner.invoke(sln_group, ["status", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_sln_status_json_output_flag_accepted(self, tmp_path):
        from strata.commands.cli_sln import sln_group

        runner = CliRunner()
        with patch("strata.commands.status.show_status_command.StatusCommand.execute", return_value=True):
            result = runner.invoke(sln_group, ["status", "--work-path", str(tmp_path), "--output", "json"])
        assert result.exit_code == 0

    def test_sln_status_execute_false_returns_nonzero(self, tmp_path):
        from strata.commands.cli_sln import sln_group

        runner = CliRunner()
        with patch("strata.commands.status.show_status_command.StatusCommand.execute", return_value=False):
            result = runner.invoke(sln_group, ["status", "--work-path", str(tmp_path)])
        assert result.exit_code != 0
