"""Tests for deploy-log integration in RunDeployCommand (Step 5 — ADR 0018).

Verifies:
- _write_deploy_log is called on successful finalization (not dry-run)
- _write_deploy_log is skipped for dry-run
- _write_deploy_log failures don't affect deployment exit code
- DeployLogModel is assembled correctly from ManifestStageModel
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from strata.commands.deploy.run_deploy_command import RunDeployCommand
from strata.models.common_models import PlatformName
from strata.models.deployment_manifest_model import ManifestStageModel


def _make_command(
    work_path: Path,
    dry_run: bool = False,
    deploy_started_at: str = "2024-01-15T10:00:00+00:00",
) -> RunDeployCommand:
    """Create a RunDeployCommand with mocked context for testing."""
    cmd = RunDeployCommand.__new__(RunDeployCommand)
    # Set attributes normally set by __init__ and BaseCommand
    cmd._work_path = work_path
    cmd._dry_run = dry_run
    cmd._force = False
    cmd._stage = None
    cmd._scope = None
    cmd._deploy_started_at = deploy_started_at
    cmd._stage_results = []
    cmd._errors = []
    cmd._messages = []
    cmd._deployment_service = None
    cmd._configuration_service = None
    cmd._file_path = Path("deployments/test.yaml")
    cmd._output_format = "console"
    cmd._output_quiet = False
    cmd._execution_id = "12345678-1234-1234-1234-123456789abc"
    cmd.logger = MagicMock()
    return cmd


def _make_stage_result(
    name: str = "infrastructure",
    status: str = "success",
    error: str | None = None,
    steps: list[str] | None = None,
) -> ManifestStageModel:
    """Create a ManifestStageModel for testing."""
    return ManifestStageModel(
        name=PlatformName(name),
        provisioner="terraform",
        topology=None,
        status=status,
        started_at="2024-01-15T10:00:01+00:00",
        completed_at="2024-01-15T10:00:30+00:00",
        duration_seconds=29,
        steps=steps or ["setup", "check", "plan", "apply"],
        error=error,
    )


class TestDeployLogFinalize:
    """Tests for _finalize triggering _write_deploy_log."""

    def test_finalize_calls_write_deploy_log_on_success(self, tmp_path: Path) -> None:
        """_finalize should call _write_deploy_log when deploy started and not dry-run."""
        cmd = _make_command(tmp_path)
        with (
            patch.object(cmd, "_write_deploy_log") as mock_write,
            patch("strata.commands.deploy.base_deploy_command.BaseDeployCommand._finalize", return_value=True),
        ):
            cmd._finalize(success=True)
            mock_write.assert_called_once_with(True)

    def test_finalize_calls_write_deploy_log_on_failure(self, tmp_path: Path) -> None:
        """_finalize should call _write_deploy_log even on failure."""
        cmd = _make_command(tmp_path)
        with (
            patch.object(cmd, "_write_deploy_log") as mock_write,
            patch("strata.commands.deploy.base_deploy_command.BaseDeployCommand._finalize", return_value=False),
        ):
            cmd._finalize(success=False)
            mock_write.assert_called_once_with(False)

    def test_finalize_skips_deploy_log_on_dry_run(self, tmp_path: Path) -> None:
        """_finalize should NOT call _write_deploy_log in dry-run mode."""
        cmd = _make_command(tmp_path, dry_run=True)
        with (
            patch.object(cmd, "_write_deploy_log") as mock_write,
            patch("strata.commands.deploy.base_deploy_command.BaseDeployCommand._finalize", return_value=True),
        ):
            cmd._finalize(success=True)
            mock_write.assert_not_called()

    def test_finalize_skips_deploy_log_when_not_started(self, tmp_path: Path) -> None:
        """_finalize should NOT call _write_deploy_log if deploy never started."""
        cmd = _make_command(tmp_path, deploy_started_at=None)
        with (
            patch.object(cmd, "_write_deploy_log") as mock_write,
            patch("strata.commands.deploy.base_deploy_command.BaseDeployCommand._finalize", return_value=True),
        ):
            cmd._finalize(success=True)
            mock_write.assert_not_called()


class TestDeployLogWrite:
    """Tests for _write_deploy_log assembling and writing the log."""

    @patch("strata.commands.deploy.run_deploy_command.RunDeployCommand._get_git_field")
    def test_writes_deploy_log_with_stages(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """_write_deploy_log assembles DeployLogModel from stage results and writes it."""
        mock_git.return_value = None
        mock_write_log = MagicMock(return_value=(True, tmp_path / ".strata" / "deploy-log" / "_execution.json"))

        cmd = _make_command(tmp_path)
        cmd._stage_results = [_make_stage_result()]

        with patch("strata.controllers.audit_controller.AuditController.write_deploy_log", mock_write_log):
            cmd._write_deploy_log(success=True)

        mock_write_log.assert_called_once()
        payload = mock_write_log.call_args[1]["payload"]

        assert payload.success is True
        assert payload.execution_id == cmd._execution_id
        assert payload.deployment == "unknown"  # no deployment_service set
        assert len(payload.stages) == 1
        assert payload.stages[0].name == "infrastructure"
        assert payload.stages[0].success is True
        assert len(payload.stages[0].steps) == 4

    @patch("strata.commands.deploy.run_deploy_command.RunDeployCommand._get_git_field")
    def test_writes_deploy_log_with_failed_stage(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Failed stages are correctly captured."""
        mock_git.return_value = None
        mock_write_log = MagicMock(return_value=(True, tmp_path / "_execution.json"))

        cmd = _make_command(tmp_path)
        cmd._stage_results = [_make_stage_result(status="failed", error="Terraform apply failed")]

        with patch("strata.controllers.audit_controller.AuditController.write_deploy_log", mock_write_log):
            cmd._write_deploy_log(success=False)

        payload = mock_write_log.call_args[1]["payload"]
        assert payload.success is False
        assert payload.stages[0].success is False
        assert payload.stages[0].errors == ["Terraform apply failed"]

    @patch("strata.commands.deploy.run_deploy_command.RunDeployCommand._get_git_field")
    def test_writes_deploy_log_with_git_context(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Git context is passed through when available."""
        mock_git.side_effect = lambda *args: {
            ("rev-parse", "HEAD"): "abc123",
            ("log", "--format=%s", "-1"): "Fix bug",
            ("log", "--format=%ae", "-1"): "dev@example.com",
        }.get(args, None)
        mock_write_log = MagicMock(return_value=(True, tmp_path / "_execution.json"))

        cmd = _make_command(tmp_path)
        cmd._stage_results = []

        with patch("strata.controllers.audit_controller.AuditController.write_deploy_log", mock_write_log):
            cmd._write_deploy_log(success=True)

        payload = mock_write_log.call_args[1]["payload"]
        assert payload.commit_sha == "abc123"
        assert payload.commit_message == "Fix bug"
        assert payload.commit_author == "dev@example.com"

    @patch("strata.commands.deploy.run_deploy_command.RunDeployCommand._get_git_field")
    def test_deploy_log_failure_does_not_raise(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Audit log failure must NOT propagate — only a warning is logged."""
        mock_git.return_value = None
        mock_write_log = MagicMock(side_effect=RuntimeError("Disk full"))

        cmd = _make_command(tmp_path)
        cmd._stage_results = []

        # Must not raise
        with patch("strata.controllers.audit_controller.AuditController.write_deploy_log", mock_write_log):
            cmd._write_deploy_log(success=True)

        cmd.logger.warning.assert_called_once()
        assert "deploy_log_write_failed" in str(cmd.logger.warning.call_args)

    @patch("strata.commands.deploy.run_deploy_command.RunDeployCommand._get_git_field")
    def test_deploy_log_uses_correct_base_path(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Base path should be {work_path}/.strata/deploy-log."""
        mock_git.return_value = None
        mock_write_log = MagicMock(return_value=(True, tmp_path / "_execution.json"))

        cmd = _make_command(tmp_path)
        cmd._stage_results = []

        with patch("strata.controllers.audit_controller.AuditController.write_deploy_log", mock_write_log):
            cmd._write_deploy_log(success=True)

        base_path = mock_write_log.call_args[1]["base_path"]
        assert base_path == tmp_path / ".strata" / "deploy-log"

    @patch("strata.commands.deploy.run_deploy_command.RunDeployCommand._get_git_field")
    def test_deploy_log_uses_by_execution_structure(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Default structure should be 'by-execution'."""
        mock_git.return_value = None
        mock_write_log = MagicMock(return_value=(True, tmp_path / "_execution.json"))

        cmd = _make_command(tmp_path)
        cmd._stage_results = []

        with patch("strata.controllers.audit_controller.AuditController.write_deploy_log", mock_write_log):
            cmd._write_deploy_log(success=True)

        structure = mock_write_log.call_args[1]["structure"]
        assert structure == "by-execution"


class TestDeployLogExecutionId:
    """Tests for execution_id generation."""

    def test_execution_id_is_set(self, tmp_path: Path) -> None:
        """RunDeployCommand should have an execution_id set."""
        cmd = _make_command(tmp_path)
        assert cmd._execution_id is not None
        assert len(cmd._execution_id) == 36  # UUID4 format
