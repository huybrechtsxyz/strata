"""Tests for the `version` command."""

import json

from click.testing import CliRunner

from strata.commands.cli_version import version_command


class TestVersionCommand:
    def test_exits_zero(self):
        runner = CliRunner()
        result = runner.invoke(version_command, [])
        assert result.exit_code == 0

    def test_outputs_version_string(self):
        runner = CliRunner()
        result = runner.invoke(version_command, [])
        assert result.output.strip() != ""

    def test_json_output_structure(self):
        runner = CliRunner()
        result = runner.invoke(version_command, ["--output", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "version" in data
        assert isinstance(data["version"], str)
        assert len(data["version"]) > 0

    def test_text_output_is_plain_string(self):
        runner = CliRunner()
        result = runner.invoke(version_command, ["--output", "text"])
        assert result.exit_code == 0
        # text output is just the version on one line — no JSON envelope
        assert "{" not in result.output
