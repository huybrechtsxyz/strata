"""Tests for AuditController Layer 4 — push_to_remote, enrich_with_pr_data, forward_to_siem, resend."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from strata.controllers.audit_controller import AuditController
from strata.models.audit_config_model import AuditConfigModel, AuditSinkModel
from strata.models.deploy_log_model import DeployLogModel


def _sample_payload(**overrides) -> DeployLogModel:
    defaults = {
        "execution_id": "test-exec-001",
        "timestamp": "2024-01-15T10:00:00+00:00",
        "version": "1.0.0",
        "deployment": "my_deploy",
        "file": "deployments/test.yaml",
        "success": True,
        "duration_seconds": 42.0,
        "stages": [],
    }
    defaults.update(overrides)
    return DeployLogModel(**defaults)


class TestPushToRemote:
    """Tests for AuditController.push_to_remote()."""

    def test_returns_false_when_no_paths(self, tmp_path: Path) -> None:
        controller = AuditController(work_path=tmp_path)
        assert controller.push_to_remote(paths=[]) is False

    @patch("strata.integrations.git.GitIntegration.push")
    @patch("strata.integrations.git.GitIntegration.commit")
    @patch("strata.integrations.git.GitIntegration.add")
    @patch("strata.integrations.git.GitIntegration.ensure_available")
    @patch("strata.integrations.factory.IntegrationFactory.create_by_type")
    def test_push_succeeds(self, mock_factory, mock_avail, mock_add, mock_commit, mock_push, tmp_path: Path) -> None:
        git_mock = MagicMock()
        git_mock.ensure_available.return_value = (True, "")
        git_mock.add.return_value = MagicMock(returncode=0)
        git_mock.commit.return_value = MagicMock(returncode=0)
        git_mock.push.return_value = MagicMock(returncode=0)
        mock_factory.return_value = git_mock

        controller = AuditController(work_path=tmp_path)
        result = controller.push_to_remote(paths=[tmp_path / "file.json"])

        assert result is True
        git_mock.add.assert_called_once()
        git_mock.commit.assert_called_once()
        git_mock.push.assert_called_once()

    @patch("strata.integrations.factory.IntegrationFactory.create_by_type")
    def test_returns_false_when_git_unavailable(self, mock_factory, tmp_path: Path) -> None:
        git_mock = MagicMock()
        git_mock.ensure_available.return_value = (False, "git not found")
        mock_factory.return_value = git_mock

        controller = AuditController(work_path=tmp_path)
        result = controller.push_to_remote(paths=[tmp_path / "file.json"])
        assert result is False

    @patch("strata.integrations.factory.IntegrationFactory.create_by_type")
    def test_returns_true_when_nothing_to_commit(self, mock_factory, tmp_path: Path) -> None:
        git_mock = MagicMock()
        git_mock.ensure_available.return_value = (True, "")
        git_mock.add.return_value = MagicMock(returncode=0)
        git_mock.commit.return_value = MagicMock(returncode=1, stdout="nothing to commit", stderr="")
        mock_factory.return_value = git_mock

        controller = AuditController(work_path=tmp_path)
        result = controller.push_to_remote(paths=[tmp_path / "file.json"])
        assert result is True

    @patch("strata.integrations.factory.IntegrationFactory.create_by_type")
    def test_returns_false_when_add_fails(self, mock_factory, tmp_path: Path) -> None:
        git_mock = MagicMock()
        git_mock.ensure_available.return_value = (True, "")
        git_mock.add.return_value = MagicMock(returncode=1, stderr="fatal: not a git repo")
        mock_factory.return_value = git_mock

        controller = AuditController(work_path=tmp_path)
        result = controller.push_to_remote(paths=[tmp_path / "file.json"])
        assert result is False


class TestEnrichWithPrData:
    """Tests for AuditController.enrich_with_pr_data()."""

    def test_returns_unchanged_when_no_commit_sha(self, tmp_path: Path) -> None:
        controller = AuditController(work_path=tmp_path)
        payload = _sample_payload(commit_sha=None)
        result = controller.enrich_with_pr_data(payload)
        assert result.pull_request is None

    @patch("strata.utils.system.run_command")
    def test_enriches_with_pr_data(self, mock_run, tmp_path: Path) -> None:
        pr_json = json.dumps(
            [
                {
                    "number": 42,
                    "title": "Fix auth",
                    "url": "https://github.com/org/repo/pull/42",
                    "author": {"login": "dev1"},
                    "mergedBy": {"login": "reviewer1"},
                    "mergedAt": "2024-01-15T09:55:00Z",
                    "labels": [{"name": "bugfix"}],
                    "files": [{"path": "src/auth.py"}],
                }
            ]
        )
        mock_run.return_value = MagicMock(returncode=0, stdout=pr_json)

        controller = AuditController(work_path=tmp_path)
        payload = _sample_payload(commit_sha="abc123")
        result = controller.enrich_with_pr_data(payload)

        assert result.pull_request is not None
        assert result.pull_request.number == 42
        assert result.pull_request.title == "Fix auth"
        assert result.pull_request.author == "dev1"
        assert result.pull_request.merged_by == "reviewer1"
        assert result.pull_request.labels == ["bugfix"]
        assert result.pull_request.files_changed == ["src/auth.py"]

    @patch("strata.utils.system.run_command")
    def test_returns_unchanged_on_gh_failure(self, mock_run, tmp_path: Path) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        controller = AuditController(work_path=tmp_path)
        payload = _sample_payload(commit_sha="abc123")
        result = controller.enrich_with_pr_data(payload)
        assert result.pull_request is None

    @patch("strata.utils.system.run_command")
    def test_returns_unchanged_when_no_pr_found(self, mock_run, tmp_path: Path) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="[]")
        controller = AuditController(work_path=tmp_path)
        payload = _sample_payload(commit_sha="abc123")
        result = controller.enrich_with_pr_data(payload)
        assert result.pull_request is None


class TestForwardToSiem:
    """Tests for AuditController.forward_to_siem()."""

    def test_noop_when_no_config(self, tmp_path: Path) -> None:
        controller = AuditController(work_path=tmp_path)
        payload = _sample_payload()
        # Should not raise
        controller.forward_to_siem(payload, audit_config=None)

    def test_noop_when_no_sinks(self, tmp_path: Path) -> None:
        config = AuditConfigModel(sinks=[])
        controller = AuditController(work_path=tmp_path)
        controller.forward_to_siem(_sample_payload(), audit_config=config)

    def test_ndjson_sink_writes_file(self, tmp_path: Path) -> None:
        ndjson_file = tmp_path / "audit.ndjson"
        config = AuditConfigModel(sinks=[AuditSinkModel(name="log", type="ndjson", path=str(ndjson_file))])
        controller = AuditController(work_path=tmp_path)
        controller.forward_to_siem(_sample_payload(), audit_config=config)

        assert ndjson_file.exists()
        lines = ndjson_file.read_text().strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["deployment"] == "my_deploy"

    def test_ndjson_sink_appends(self, tmp_path: Path) -> None:
        ndjson_file = tmp_path / "audit.ndjson"
        config = AuditConfigModel(sinks=[AuditSinkModel(name="log", type="ndjson", path=str(ndjson_file))])
        controller = AuditController(work_path=tmp_path)
        controller.forward_to_siem(_sample_payload(execution_id="1"), audit_config=config)
        controller.forward_to_siem(_sample_payload(execution_id="2"), audit_config=config)

        lines = ndjson_file.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_disabled_sink_skipped(self, tmp_path: Path) -> None:
        ndjson_file = tmp_path / "audit.ndjson"
        config = AuditConfigModel(
            sinks=[AuditSinkModel(name="log", type="ndjson", path=str(ndjson_file), enabled=False)]
        )
        controller = AuditController(work_path=tmp_path)
        controller.forward_to_siem(_sample_payload(), audit_config=config)
        assert not ndjson_file.exists()

    def test_event_filter_skips_non_matching(self, tmp_path: Path) -> None:
        ndjson_file = tmp_path / "audit.ndjson"
        config = AuditConfigModel(
            sinks=[AuditSinkModel(name="log", type="ndjson", path=str(ndjson_file), events=["cli_action"])]
        )
        controller = AuditController(work_path=tmp_path)
        controller.forward_to_siem(_sample_payload(), audit_config=config)
        assert not ndjson_file.exists()

    @patch("urllib.request.urlopen")
    def test_webhook_sink_sends_post(self, mock_urlopen, tmp_path: Path) -> None:
        config = AuditConfigModel(sinks=[AuditSinkModel(name="hook", type="webhook", url="https://example.com/hook")])
        mock_urlopen.return_value.__enter__ = MagicMock()
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        controller = AuditController(work_path=tmp_path)
        controller.forward_to_siem(_sample_payload(), audit_config=config)

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "https://example.com/hook"
        assert req.get_method() == "POST"


class TestResend:
    """Tests for AuditController.resend()."""

    def test_resend_returns_counts(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        # Write a sample entry
        exec_dir = log_dir / "exec1"
        exec_dir.mkdir()
        data = _sample_payload().model_dump(exclude_none=True)
        (exec_dir / "_execution.json").write_text(json.dumps(data, default=str))

        ndjson_file = tmp_path / "resend.ndjson"
        config = AuditConfigModel(sinks=[AuditSinkModel(name="resend_sink", type="ndjson", path=str(ndjson_file))])

        controller = AuditController(work_path=tmp_path)
        sent, failed = controller.resend(base_path=log_dir, audit_config=config)

        assert sent == 1
        assert failed == 0
        assert ndjson_file.exists()

    def test_resend_empty_dir(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        config = AuditConfigModel(sinks=[])
        controller = AuditController(work_path=tmp_path)
        sent, failed = controller.resend(base_path=log_dir, audit_config=config)
        assert sent == 0
        assert failed == 0
