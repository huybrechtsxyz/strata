"""Tests for the ``policy`` command group."""

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from strata.commands.cli_policy import policy_group
from strata.commands.policies.list_policy_command import ListPolicyCommand


class TestPolicyGroupCli:
    def test_group_help_exits_zero(self):
        runner = CliRunner()
        result = runner.invoke(policy_group, ["--help"])
        assert result.exit_code == 0

    def test_group_help_shows_list_subcommand(self):
        runner = CliRunner()
        result = runner.invoke(policy_group, ["--help"])
        assert "list" in result.output

    def test_list_help_exits_zero(self):
        runner = CliRunner()
        result = runner.invoke(policy_group, ["list", "--help"])
        assert result.exit_code == 0

    def test_list_help_shows_file_option(self):
        runner = CliRunner()
        result = runner.invoke(policy_group, ["list", "--help"])
        assert "-f" in result.output or "--file" in result.output

    def test_list_help_shows_output_option(self):
        runner = CliRunner()
        result = runner.invoke(policy_group, ["list", "--help"])
        assert "--output" in result.output


class TestPolicyListCommand:
    def test_exits_zero_when_execute_succeeds(self, tmp_path):
        runner = CliRunner()
        with patch.object(ListPolicyCommand, "execute", return_value=True):
            result = runner.invoke(policy_group, ["list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_exits_nonzero_when_execute_fails(self, tmp_path):
        runner = CliRunner()
        with patch.object(ListPolicyCommand, "execute", return_value=False):
            result = runner.invoke(policy_group, ["list", "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_accepts_file_option(self, tmp_path):
        runner = CliRunner()
        with patch.object(ListPolicyCommand, "execute", return_value=True):
            result = runner.invoke(policy_group, ["list", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_accepts_output_json(self, tmp_path):
        runner = CliRunner()
        with patch.object(ListPolicyCommand, "execute", return_value=True):
            result = runner.invoke(policy_group, ["list", "--output", "json", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_accepts_output_text(self, tmp_path):
        runner = CliRunner()
        with patch.object(ListPolicyCommand, "execute", return_value=True):
            result = runner.invoke(policy_group, ["list", "--output", "text", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_accepts_output_ndjson(self, tmp_path):
        runner = CliRunner()
        with patch.object(ListPolicyCommand, "execute", return_value=True):
            result = runner.invoke(policy_group, ["list", "--output", "ndjson", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_accepts_quiet_flag(self, tmp_path):
        runner = CliRunner()
        with patch.object(ListPolicyCommand, "execute", return_value=True):
            result = runner.invoke(policy_group, ["list", "--quiet", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_accepts_verbose_flag(self, tmp_path):
        runner = CliRunner()
        with patch.object(ListPolicyCommand, "execute", return_value=True):
            result = runner.invoke(policy_group, ["list", "--verbose", "--work-path", str(tmp_path)])
        assert result.exit_code == 0


class TestListPolicyCommandOutputData:
    """Exercise _after_execute rendering by controlling _output_data directly."""

    _SAMPLE_OUTPUT_DATA = {
        "source": "profile:dev",
        "deployment": None,
        "policy_count": 2,
        "enabled_count": 1,
        "phases_triggered": None,
        "policies": [
            {
                "name": "no-public-zones",
                "type": "tenant_zone",
                "phase": "deploy",
                "enforcement": "deny",
                "enabled": True,
                "description": "Block public zone deployments",
                "configuration": {"allowed_zones": ["eu-west"]},
            },
            {
                "name": "name-check",
                "type": "naming_pattern",
                "phase": "validate",
                "enforcement": "warn",
                "enabled": False,
                "description": None,
                "configuration": {"pattern": "^[a-z]+$"},
            },
        ],
    }

    def _run_with_data(self, output_format, tmp_path):
        from datetime import datetime, timezone

        sample = self._SAMPLE_OUTPUT_DATA

        def fake_initialize(self_cmd, show_header: bool = True):
            self_cmd._start_time = datetime.now(timezone.utc)
            return True

        def fake_run_execution(self_cmd):
            self_cmd._output_data = dict(sample)
            self_cmd._source_label = sample["source"]
            self_cmd._deployment_phases = []
            # Build lightweight mock policies so _render_console can iterate them
            policies = []
            for p_data in sample["policies"]:
                p = MagicMock()
                p.name = p_data["name"]
                p.type = p_data["type"]
                p.phase = p_data["phase"]
                p.enforcement = p_data["enforcement"]
                p.enabled = p_data["enabled"]
                policies.append(p)
            self_cmd._policies = policies
            return True

        runner = CliRunner()
        with patch.object(ListPolicyCommand, "_execute", fake_run_execution):
            with patch.object(ListPolicyCommand, "_initialize", fake_initialize):
                with patch.object(ListPolicyCommand, "_before_execute", return_value=True):
                    result = runner.invoke(
                        policy_group,
                        ["list", "--output", output_format, "--work-path", str(tmp_path)],
                    )
        return result

    def test_json_output_is_valid_json(self, tmp_path):
        result = self._run_with_data("json", tmp_path)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)

    def test_json_output_contains_policies_key(self, tmp_path):
        result = self._run_with_data("json", tmp_path)
        data = json.loads(result.output)
        assert "policies" in data["data"]

    def test_json_output_contains_policy_count(self, tmp_path):
        result = self._run_with_data("json", tmp_path)
        data = json.loads(result.output)
        assert data["data"]["policy_count"] == 2

    def test_json_output_contains_enabled_count(self, tmp_path):
        result = self._run_with_data("json", tmp_path)
        data = json.loads(result.output)
        assert data["data"]["enabled_count"] == 1

    def test_console_output_contains_policy_name(self, tmp_path):
        result = self._run_with_data("console", tmp_path)
        assert result.exit_code == 0
        assert "no-public-zones" in result.output

    def test_console_output_contains_enforcement_type(self, tmp_path):
        result = self._run_with_data("console", tmp_path)
        assert "deny" in result.output

    def test_console_output_shows_count_summary(self, tmp_path):
        result = self._run_with_data("console", tmp_path)
        # Should mention count totals
        assert "2" in result.output
