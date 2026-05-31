"""Tests for ``strata completion <shell>``."""

import pytest
from click.testing import CliRunner

from strata.cli import main


class TestCompletionCommand:
    """Tests for ``strata completion <shell>``."""

    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_exits_zero(self, shell: str) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["completion", shell])
        assert result.exit_code == 0, result.output

    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_outputs_non_empty_script(self, shell: str) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["completion", shell])
        assert result.exit_code == 0
        assert len(result.output.strip()) > 0

    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_output_contains_strata(self, shell: str) -> None:
        """Completion scripts reference the 'strata' prog name."""
        runner = CliRunner()
        result = runner.invoke(main, ["completion", shell])
        assert "strata" in result.output

    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_output_contains_install_hint(self, shell: str) -> None:
        """Each script contains commented install instructions."""
        runner = CliRunner()
        result = runner.invoke(main, ["completion", shell])
        # The hint comment block is always present; on Windows, Click may emit
        # a "Couldn't detect Bash version" warning before it, so check `in`.
        assert "# strata shell completion" in result.output

    def test_powershell_exits_zero_or_graceful_error(self) -> None:
        """PowerShell completion either works (Click >= 8.1) or fails cleanly."""
        runner = CliRunner()
        result = runner.invoke(main, ["completion", "powershell"])
        # Click 8.1+ returns 0; older Click returns a ClickException (exit 1)
        assert result.exit_code in (0, 1)
        if result.exit_code == 1:
            assert "8.1" in result.output or "PowerShell" in result.output

    def test_invalid_shell_exits_two(self) -> None:
        """An unknown shell name is a usage error (exit 2)."""
        runner = CliRunner()
        result = runner.invoke(main, ["completion", "nushell"])
        assert result.exit_code == 2

    def test_invalid_shell_shows_valid_choices(self) -> None:
        """Error message lists the valid shell choices."""
        runner = CliRunner()
        result = runner.invoke(main, ["completion", "nushell"])
        output = result.output.lower()
        assert "bash" in output or "invalid choice" in output

    @pytest.mark.parametrize("shell", ["BASH", "Zsh", "FISH"])
    def test_case_insensitive_shell_arg(self, shell: str) -> None:
        """Shell argument is case-insensitive."""
        runner = CliRunner()
        result = runner.invoke(main, ["completion", shell])
        assert result.exit_code == 0

    def test_appears_in_main_help(self) -> None:
        """'completion' is listed in the top-level help output."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "completion" in result.output
