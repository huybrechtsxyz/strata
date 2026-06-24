"""Tests for the console command."""

import json
from pathlib import Path
from typing import Optional

import pytest

try:
    from strata.commands.cli_console import console_command

    IMPL_MISSING = False
except ImportError:
    console_command = None  # type: ignore[assignment]
    IMPL_MISSING = True

from click.testing import CliRunner

pytestmark = pytest.mark.skipif(IMPL_MISSING, reason="console command not yet implemented")


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _runner() -> CliRunner:
    return CliRunner()


def _make_workspace(tmp_path: Path, solution: Optional[dict] = None) -> Path:
    strata_dir = tmp_path / ".strata"
    strata_dir.mkdir(parents=True, exist_ok=True)
    if solution is not None:
        (strata_dir / "solution.json").write_text(json.dumps(solution))
    return tmp_path


def _make_solution_json(name: str = "my-platform") -> dict:
    return {
        "apiVersion": "strata.huybrechts.xyz/v1",
        "kind": "solution",
        "meta": {"name": name},
        "spec": {
            "solution_id": "abc-00001",
            "repositories": [],
            "profiles": [],
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConsoleCommand:
    def test_help_option(self):
        result = _runner().invoke(console_command, ["--help"])
        assert result.exit_code == 0
        assert "Interactive workspace session" in result.output

    def test_exit_immediately(self, tmp_path):
        """Sending 'quit' as stdin exits the REPL with code 0."""
        _make_workspace(tmp_path)
        result = _runner().invoke(
            console_command,
            ["--work-path", str(tmp_path)],
            input="quit\n",
        )
        assert result.exit_code == 0
        assert "Bye!" in result.output

    def test_help_command_in_repl(self, tmp_path):
        """Typing '?' shows the help table."""
        _make_workspace(tmp_path)
        result = _runner().invoke(
            console_command,
            ["--work-path", str(tmp_path)],
            input="?\nquit\n",
        )
        assert result.exit_code == 0
        assert "status" in result.output
        assert "next" in result.output

    def test_status_shows_checklist(self, tmp_path):
        """The 'status' command shows the checklist."""
        solution = _make_solution_json()
        _make_workspace(tmp_path, solution=solution)
        result = _runner().invoke(
            console_command,
            ["--work-path", str(tmp_path)],
            input="status\nquit\n",
        )
        assert result.exit_code == 0
        assert "Workspace initialized" in result.output

    def test_next_shows_hint(self, tmp_path):
        """The 'next' command shows the next step hint."""
        _make_workspace(tmp_path)
        result = _runner().invoke(
            console_command,
            ["--work-path", str(tmp_path)],
            input="next\nquit\n",
        )
        assert result.exit_code == 0
        assert "strata sln init" in result.output

    def test_unknown_command(self, tmp_path):
        """Unknown commands show error message."""
        _make_workspace(tmp_path)
        result = _runner().invoke(
            console_command,
            ["--work-path", str(tmp_path)],
            input="foobar\nquit\n",
        )
        assert result.exit_code == 0
        assert "Unknown command" in result.output

    def test_uninitialized_workspace(self, tmp_path):
        """Console still works when workspace is not initialized."""
        _make_workspace(tmp_path)
        result = _runner().invoke(
            console_command,
            ["--work-path", str(tmp_path)],
            input="quit\n",
        )
        assert result.exit_code == 0
        assert "uninitialized" in result.output

    def test_no_color_flag(self, tmp_path):
        """--no-color flag is accepted without error."""
        _make_workspace(tmp_path)
        result = _runner().invoke(
            console_command,
            ["--work-path", str(tmp_path), "--no-color"],
            input="quit\n",
        )
        assert result.exit_code == 0
