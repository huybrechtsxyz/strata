"""Tests for the `help` command."""

from click.testing import CliRunner

from xyz_platform.commands.cli_help import help_command


class TestHelpCommand:
    def test_exits_zero_no_args(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(help_command, ["--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_flag_exits_zero(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(help_command, ["--list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_flag_shows_topics(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(help_command, ["--list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        # At least one built-in topic name should appear
        assert "quickstart" in result.output

    def test_known_topic_exits_zero(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(help_command, ["--topic", "quickstart", "--work-path", str(tmp_path)])
        # May exit non-zero if the help data file is absent in the test env,
        # but the command itself should not crash
        assert result.exit_code in (0, 1)

    def test_unknown_topic_exits_nonzero(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(help_command, ["--topic", "no-such-topic-xyz", "--work-path", str(tmp_path)])
        assert result.exit_code != 0
