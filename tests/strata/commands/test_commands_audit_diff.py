"""Tests for strata audit diff command (DiffAuditCommand)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from strata.commands.audit.diff_audit_command import DiffAuditCommand


def _make_entry(
    execution_id: str,
    commit_sha: str = "abc123def456",
    timestamp: str = "2026-07-20T10:00:00+00:00",
    deployment: str = "my-platform",
    yaml_file: str = "deploy/deploy-prd.yaml",
    pull_request=None,
) -> MagicMock:
    entry = MagicMock()
    entry.execution_id = execution_id
    entry.commit_sha = commit_sha
    entry.timestamp = timestamp
    entry.deployment = deployment
    entry.file = yaml_file
    entry.pull_request = pull_request
    return entry


def _make_command(tmp_path: Path, from_id: str = "aaa", to_id: str = "bbb") -> DiffAuditCommand:
    cmd = DiffAuditCommand.__new__(DiffAuditCommand)
    cmd._from_id = from_id
    cmd._to_id = to_id
    cmd._work_path = tmp_path
    cmd._output_format = "console"
    cmd._output_quiet = False
    cmd._errors = []
    cmd._messages = []
    cmd._output_data = {}
    cmd._diff_lines = []
    cmd._from_entry = None
    cmd._to_entry = None
    cmd.logger = MagicMock()
    return cmd


class TestDiffAuditCommandExecute:
    def test_returns_false_when_from_id_not_found(self, tmp_path: Path) -> None:
        cmd = _make_command(tmp_path, from_id="missing-1", to_id="missing-2")

        with patch(
            "strata.controllers.audit_controller.AuditController.query_deploy_logs",
            return_value=[],
        ):
            result = cmd._execute()

        assert result is False
        assert any("missing-1" in e for e in cmd._errors)

    def test_returns_false_when_to_id_not_found(self, tmp_path: Path) -> None:
        entry = _make_entry("aaa")
        cmd = _make_command(tmp_path, from_id="aaa", to_id="missing-2")

        with patch(
            "strata.controllers.audit_controller.AuditController.query_deploy_logs",
            return_value=[entry],
        ):
            result = cmd._execute()

        assert result is False
        assert any("missing-2" in e for e in cmd._errors)

    def test_no_diff_when_same_commit(self, tmp_path: Path) -> None:
        entry_a = _make_entry("aaa", commit_sha="same123")
        entry_b = _make_entry("bbb", commit_sha="same123")
        cmd = _make_command(tmp_path, from_id="aaa", to_id="bbb")

        with patch(
            "strata.controllers.audit_controller.AuditController.query_deploy_logs",
            return_value=[entry_a, entry_b],
        ):
            result = cmd._execute()

        assert result is True
        assert cmd._diff_lines == []
        assert cmd._output_data["has_changes"] is False

    def test_diff_lines_populated_when_changes_exist(self, tmp_path: Path) -> None:
        entry_a = _make_entry("aaa", commit_sha="sha_before")
        entry_b = _make_entry("bbb", commit_sha="sha_after")
        cmd = _make_command(tmp_path, from_id="aaa", to_id="bbb")

        diff_output = "+replicas: 4\n-replicas: 2\n"

        with (
            patch(
                "strata.controllers.audit_controller.AuditController.query_deploy_logs",
                return_value=[entry_a, entry_b],
            ),
            patch.object(cmd, "_run_git_diff", return_value=diff_output),
        ):
            result = cmd._execute()

        assert result is True
        assert cmd._diff_lines == ["+replicas: 4", "-replicas: 2"]
        assert cmd._output_data["has_changes"] is True
        assert cmd._output_data["diff"] == diff_output

    def test_returns_false_when_git_diff_fails(self, tmp_path: Path) -> None:
        entry_a = _make_entry("aaa", commit_sha="sha_a")
        entry_b = _make_entry("bbb", commit_sha="sha_b")
        cmd = _make_command(tmp_path, from_id="aaa", to_id="bbb")

        with (
            patch(
                "strata.controllers.audit_controller.AuditController.query_deploy_logs",
                return_value=[entry_a, entry_b],
            ),
            patch.object(cmd, "_run_git_diff", return_value=None),
        ):
            result = cmd._execute()

        assert result is False

    def test_returns_false_when_from_has_no_commit_sha(self, tmp_path: Path) -> None:
        entry_a = _make_entry("aaa", commit_sha="")
        entry_b = _make_entry("bbb", commit_sha="sha_b")
        cmd = _make_command(tmp_path, from_id="aaa", to_id="bbb")

        # Override commit_sha to None for this test
        entry_a.commit_sha = None

        with patch(
            "strata.controllers.audit_controller.AuditController.query_deploy_logs",
            return_value=[entry_a, entry_b],
        ):
            result = cmd._execute()

        assert result is False
        assert any("no commit SHA" in e for e in cmd._errors)


class TestDiffAuditCommandValidationErrors:
    def test_has_validation_errors_true_when_diff_exists(self, tmp_path: Path) -> None:
        cmd = _make_command(tmp_path)
        cmd._diff_lines = ["+line"]
        assert cmd.has_validation_errors() is True

    def test_has_validation_errors_false_when_no_diff(self, tmp_path: Path) -> None:
        cmd = _make_command(tmp_path)
        cmd._diff_lines = []
        assert cmd.has_validation_errors() is False


class TestDiffAuditCommandOutputData:
    def test_output_data_contains_from_to_metadata(self, tmp_path: Path) -> None:
        entry_a = _make_entry("aaa", commit_sha="sha_before", timestamp="2026-07-15T10:00:00+00:00")
        entry_b = _make_entry("bbb", commit_sha="sha_after", timestamp="2026-07-20T10:00:00+00:00")
        cmd = _make_command(tmp_path, from_id="aaa", to_id="bbb")

        with (
            patch(
                "strata.controllers.audit_controller.AuditController.query_deploy_logs",
                return_value=[entry_a, entry_b],
            ),
            patch.object(cmd, "_run_git_diff", return_value=""),
        ):
            cmd._execute()

        assert cmd._output_data["from"]["execution_id"] == "aaa"
        assert cmd._output_data["from"]["commit_sha"] == "sha_before"
        assert cmd._output_data["to"]["execution_id"] == "bbb"
        assert cmd._output_data["to"]["commit_sha"] == "sha_after"
        assert cmd._output_data["file"] == "deploy/deploy-prd.yaml"
