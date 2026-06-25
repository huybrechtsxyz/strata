"""Tests for strata audit changes CLI command (Step 9 — ADR 0018)."""

import json
from pathlib import Path

from click.testing import CliRunner

from strata.cli import main


def _write_execution_json(base_path: Path, subdir: str, data: dict) -> None:
    """Write a test _execution.json file."""
    target = base_path / subdir
    target.mkdir(parents=True, exist_ok=True)
    (target / "_execution.json").write_text(json.dumps(data), encoding="utf-8")


def _sample_entry(
    execution_id: str = "aaaa-bbbb",
    timestamp: str = "2024-01-15T10:00:00+00:00",
    deployment: str = "test_deploy",
    success: bool = True,
    duration_seconds: float = 42.5,
) -> dict:
    return {
        "execution_id": execution_id,
        "timestamp": timestamp,
        "version": "1.0.0",
        "deployment": deployment,
        "file": "deployments/test.yaml",
        "success": success,
        "duration_seconds": duration_seconds,
        "stages": [
            {
                "name": "infrastructure",
                "success": True,
                "started_at": timestamp,
                "completed_at": "2024-01-15T10:00:30+00:00",
                "duration_seconds": 30.0,
            }
        ],
    }


class TestAuditChanges:
    """Tests for strata audit changes command."""

    def test_no_entries_found(self, tmp_path: Path) -> None:
        """Returns zero entries when no deploy-log exists."""
        (tmp_path / ".strata").mkdir()
        runner = CliRunner()
        result = runner.invoke(main, ["audit", "changes", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "No deploy-log entries found" in result.output

    def test_shows_entries_console(self, tmp_path: Path) -> None:
        """Shows entries in console table format."""
        log_dir = tmp_path / ".strata" / "deploy-log"
        _write_execution_json(log_dir, "exec1", _sample_entry())
        runner = CliRunner()
        result = runner.invoke(main, ["audit", "changes", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "test_deploy" in result.output
        assert "✓" in result.output
        assert "1 entries shown" in result.output

    def test_shows_entries_json(self, tmp_path: Path) -> None:
        """Shows entries as JSON array."""
        log_dir = tmp_path / ".strata" / "deploy-log"
        _write_execution_json(log_dir, "exec1", _sample_entry())
        runner = CliRunner()
        result = runner.invoke(main, ["audit", "changes", "--work-path", str(tmp_path), "--output", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["deployment"] == "test_deploy"

    def test_last_limits_results(self, tmp_path: Path) -> None:
        """--last limits the number of results."""
        log_dir = tmp_path / ".strata" / "deploy-log"
        _write_execution_json(log_dir, "exec1", _sample_entry(execution_id="1", timestamp="2024-01-15T10:00:00+00:00"))
        _write_execution_json(log_dir, "exec2", _sample_entry(execution_id="2", timestamp="2024-01-16T10:00:00+00:00"))
        _write_execution_json(log_dir, "exec3", _sample_entry(execution_id="3", timestamp="2024-01-17T10:00:00+00:00"))
        runner = CliRunner()
        result = runner.invoke(
            main, ["audit", "changes", "--work-path", str(tmp_path), "--last", "2", "--output", "json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2

    def test_since_filters_results(self, tmp_path: Path) -> None:
        """--since filters by timestamp."""
        log_dir = tmp_path / ".strata" / "deploy-log"
        _write_execution_json(log_dir, "old", _sample_entry(execution_id="1", timestamp="2024-01-10T00:00:00+00:00"))
        _write_execution_json(log_dir, "new", _sample_entry(execution_id="2", timestamp="2024-01-20T00:00:00+00:00"))
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "audit",
                "changes",
                "--work-path",
                str(tmp_path),
                "--since",
                "2024-01-15T00:00:00+00:00",
                "--output",
                "json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["execution_id"] == "2"

    def test_stage_filters_results(self, tmp_path: Path) -> None:
        """--stage filters to entries containing that stage."""
        log_dir = tmp_path / ".strata" / "deploy-log"
        _write_execution_json(log_dir, "exec1", _sample_entry())
        runner = CliRunner()
        # Filter by existing stage
        result = runner.invoke(
            main,
            ["audit", "changes", "--work-path", str(tmp_path), "--stage", "infrastructure", "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1

        # Filter by non-existing stage
        result = runner.invoke(
            main,
            ["audit", "changes", "--work-path", str(tmp_path), "--stage", "nonexistent", "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 0

    def test_failed_entry_shows_cross(self, tmp_path: Path) -> None:
        """Failed deployments show ✗ in console."""
        log_dir = tmp_path / ".strata" / "deploy-log"
        _write_execution_json(log_dir, "exec1", _sample_entry(success=False))
        runner = CliRunner()
        result = runner.invoke(main, ["audit", "changes", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "✗" in result.output


class TestAuditExport:
    """Tests for strata audit export command."""

    def test_export_json_to_stdout(self, tmp_path: Path) -> None:
        """Export JSON to stdout."""
        log_dir = tmp_path / ".strata" / "deploy-log"
        _write_execution_json(log_dir, "exec1", _sample_entry())
        runner = CliRunner()
        result = runner.invoke(main, ["audit", "export", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["deployment"] == "test_deploy"

    def test_export_ndjson_to_stdout(self, tmp_path: Path) -> None:
        """Export NDJSON to stdout."""
        log_dir = tmp_path / ".strata" / "deploy-log"
        _write_execution_json(log_dir, "exec1", _sample_entry())
        runner = CliRunner()
        result = runner.invoke(main, ["audit", "export", "--work-path", str(tmp_path), "--format", "ndjson"])
        assert result.exit_code == 0
        lines = [l for l in result.output.strip().split("\n") if l]
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["deployment"] == "test_deploy"

    def test_export_to_file(self, tmp_path: Path) -> None:
        """Export to a file."""
        log_dir = tmp_path / ".strata" / "deploy-log"
        _write_execution_json(log_dir, "exec1", _sample_entry())
        out_file = tmp_path / "export.json"
        runner = CliRunner()
        result = runner.invoke(main, ["audit", "export", "--work-path", str(tmp_path), "--out", str(out_file)])
        assert result.exit_code == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert len(data) == 1

    def test_export_empty(self, tmp_path: Path) -> None:
        """Export with no entries produces empty array."""
        (tmp_path / ".strata").mkdir()
        runner = CliRunner()
        result = runner.invoke(main, ["audit", "export", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == []

    def test_export_last_limits(self, tmp_path: Path) -> None:
        """--last limits exported entries."""
        log_dir = tmp_path / ".strata" / "deploy-log"
        _write_execution_json(log_dir, "e1", _sample_entry(execution_id="1", timestamp="2024-01-15T10:00:00+00:00"))
        _write_execution_json(log_dir, "e2", _sample_entry(execution_id="2", timestamp="2024-01-16T10:00:00+00:00"))
        runner = CliRunner()
        result = runner.invoke(main, ["audit", "export", "--work-path", str(tmp_path), "--last", "1"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
