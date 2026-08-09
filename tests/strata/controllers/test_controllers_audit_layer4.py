"""Tests for AuditController Layer 4 — push_to_remote, enrich_with_pr_data, forward, resend."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from strata.controllers.audit_controller import AuditController
from strata.integrations.base_integration import BaseIntegration
from strata.integrations.siem.webhook_siem_integration import WebhookSiemIntegration
from strata.models.audit_config_model import AuditConfigModel, AuditSinkModel
from strata.models.deploy_log_model import DeployLogModel
from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel


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


def _mock_integration_service(integrations: dict):
    """integrations: {name: mock_instance_or_None}"""
    svc = MagicMock()
    svc.is_initialized.return_value = True
    svc.get_integration.side_effect = lambda name: integrations.get(name)
    return svc


def _make_siem_integration(name: str = "webhook") -> WebhookSiemIntegration:
    """A real, lightweight ISiemSink instance — a bare MagicMock does not satisfy
    the runtime_checkable ISiemSink protocol's isinstance check even with matching
    attributes, so tests use a real (cheap) integration instead (matches the existing
    convention in test_commands_audit_siem.py).
    """
    BaseIntegration._instances.clear()
    return WebhookSiemIntegration(
        IntegrationModel(
            name=name,
            type="webhook",
            endpoints=IntegrationEndpointsSpecModel(address="https://example.com/hook"),
        )
    )


class TestForward:
    """Tests for AuditController.forward() (ADR-0066) — journal, then sink fan-out."""

    @pytest.fixture(autouse=True)
    def _mock_actor(self):
        """forward() calls resolve_actor() to populate the envelope's ECS user.name —
        mocked here so tests stay fast and deterministic rather than exercising the full
        cloud-CLI/CI-env/OS-login precedence chain on every call.
        """
        with patch("strata.controllers.actor_controller.resolve_actor", return_value="test-user"):
            yield

    def test_noop_when_no_config(self, tmp_path: Path) -> None:
        controller = AuditController(work_path=tmp_path)
        # Should not raise
        controller.forward("deployment.completed", {"a": 1}, audit_config=None)

    def test_noop_when_no_sinks(self, tmp_path: Path) -> None:
        config = AuditConfigModel(sinks=[])
        controller = AuditController(work_path=tmp_path)
        controller.forward("deployment.completed", {"a": 1}, audit_config=config)

    def test_writes_to_journal(self, tmp_path: Path) -> None:
        with patch("strata.logger.audit") as mock_journal:
            controller = AuditController(work_path=tmp_path)
            controller.forward("deployment.completed", {"a": 1}, audit_config=None)
        mock_journal.assert_called_once()
        args, kwargs = mock_journal.call_args
        assert args[0] == "deployment.completed"
        assert kwargs["outcome"] == "success"
        envelope = kwargs["detail"]
        assert envelope["type"] == "xyz.huybrechts.strata.deployment.completed"
        assert envelope["data"]["strata"] == {"a": 1}
        assert envelope["data"]["user"] == {"name": "test-user"}

    def test_id_is_independent_of_execution_id(self, tmp_path: Path) -> None:
        """CloudEvents' (source, id) must uniquely identify *this* event — id is a fresh
        UUID per call, not the correlation key. Two events sharing execution_id and
        source (e.g. workitem.created and deployment.completed from the same deploy
        run) must not collide on (source, id)."""
        controller = AuditController(work_path=tmp_path)
        payload = {"execution_id": "same-execution-id", "workspace": "ws", "deployment": "dep"}

        envelope_1 = controller._build_envelope("workitem.created", payload)
        envelope_2 = controller._build_envelope("deployment.completed", payload)

        assert envelope_1["data"]["labels"]["execution_id"] == "same-execution-id"
        assert envelope_2["data"]["labels"]["execution_id"] == "same-execution-id"
        assert envelope_1["source"] == envelope_2["source"]
        assert envelope_1["id"] != envelope_2["id"]
        assert envelope_1["id"] != "same-execution-id"

    def test_sends_to_enabled_sink(self, tmp_path: Path) -> None:
        sink_integration = _make_siem_integration()
        config = AuditConfigModel(sinks=[AuditSinkModel(name="splunk", integration="splunk-prod")])
        svc = _mock_integration_service({"splunk-prod": sink_integration})

        with (
            patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc),
            patch.object(sink_integration, "send_event") as mock_send,
        ):
            controller = AuditController(work_path=tmp_path)
            controller.forward("deployment.completed", {"a": 1}, audit_config=config)

        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        assert args[0] == "deployment.completed"
        assert args[1]["data"]["strata"] == {"a": 1}

    def test_disabled_sink_skipped(self, tmp_path: Path) -> None:
        sink_integration = _make_siem_integration()
        config = AuditConfigModel(sinks=[AuditSinkModel(name="splunk", integration="splunk-prod", enabled=False)])
        svc = _mock_integration_service({"splunk-prod": sink_integration})

        with (
            patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc),
            patch.object(sink_integration, "send_event") as mock_send,
        ):
            controller = AuditController(work_path=tmp_path)
            controller.forward("deployment.completed", {"a": 1}, audit_config=config)

        mock_send.assert_not_called()

    def test_event_filter_skips_non_matching(self, tmp_path: Path) -> None:
        sink_integration = _make_siem_integration()
        config = AuditConfigModel(
            sinks=[AuditSinkModel(name="splunk", integration="splunk-prod", events=["policy.violated"])]
        )
        svc = _mock_integration_service({"splunk-prod": sink_integration})

        with (
            patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc),
            patch.object(sink_integration, "send_event") as mock_send,
        ):
            controller = AuditController(work_path=tmp_path)
            controller.forward("deployment.completed", {"a": 1}, audit_config=config)

        mock_send.assert_not_called()

    def test_event_filter_none_admits_everything(self, tmp_path: Path) -> None:
        sink_integration = _make_siem_integration()
        config = AuditConfigModel(sinks=[AuditSinkModel(name="splunk", integration="splunk-prod", events=None)])
        svc = _mock_integration_service({"splunk-prod": sink_integration})

        with (
            patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc),
            patch.object(sink_integration, "send_event") as mock_send,
        ):
            controller = AuditController(work_path=tmp_path)
            controller.forward("deployment.completed", {"a": 1}, audit_config=config)

        mock_send.assert_called_once()

    def test_missing_integration_is_skipped_without_raising(self, tmp_path: Path) -> None:
        config = AuditConfigModel(sinks=[AuditSinkModel(name="splunk", integration="does-not-exist")])
        svc = _mock_integration_service({})

        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            controller = AuditController(work_path=tmp_path)
            controller.forward("deployment.completed", {"a": 1}, audit_config=config)  # must not raise

    def test_sink_send_failure_does_not_raise(self, tmp_path: Path) -> None:
        sink_integration = _make_siem_integration()
        config = AuditConfigModel(sinks=[AuditSinkModel(name="splunk", integration="splunk-prod")])
        svc = _mock_integration_service({"splunk-prod": sink_integration})

        with (
            patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc),
            patch.object(sink_integration, "send_event", side_effect=RuntimeError("network down")),
        ):
            controller = AuditController(work_path=tmp_path)
            controller.forward("deployment.completed", {"a": 1}, audit_config=config)  # must not raise

    def test_initializes_integrations_if_not_already(self, tmp_path: Path) -> None:
        config = AuditConfigModel(sinks=[AuditSinkModel(name="splunk", integration="splunk-prod")])
        svc = _mock_integration_service({})
        svc.is_initialized.return_value = False

        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            controller = AuditController(work_path=tmp_path)
            controller.forward("deployment.completed", {"a": 1}, audit_config=config)

        svc.initialize_integrations.assert_called()

    def test_gate_blocks_disabled_event_type(self, tmp_path: Path) -> None:
        """ADR-0066 problem 1: policy.events is now consulted, not dead configuration."""
        from strata.models.audit_config_model import AuditPolicyModel

        sink_integration = _make_siem_integration()
        config = AuditConfigModel(
            policy=AuditPolicyModel(),  # build.completed defaults to False
            sinks=[AuditSinkModel(name="splunk", integration="splunk-prod")],
        )
        svc = _mock_integration_service({"splunk-prod": sink_integration})

        with (
            patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc),
            patch("strata.logger.audit") as mock_journal,
            patch.object(sink_integration, "send_event") as mock_send,
        ):
            controller = AuditController(work_path=tmp_path)
            controller.forward("build.completed", {"a": 1}, audit_config=config)

        mock_journal.assert_not_called()
        mock_send.assert_not_called()

    def test_gate_admits_enabled_event_type(self, tmp_path: Path) -> None:
        from strata.models.audit_config_model import AuditPolicyModel

        sink_integration = _make_siem_integration()
        config = AuditConfigModel(
            policy=AuditPolicyModel(events={"build.completed": True}),
            sinks=[AuditSinkModel(name="splunk", integration="splunk-prod")],
        )
        svc = _mock_integration_service({"splunk-prod": sink_integration})

        with (
            patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc),
            patch("strata.logger.audit") as mock_journal,
            patch.object(sink_integration, "send_event") as mock_send,
        ):
            controller = AuditController(work_path=tmp_path)
            controller.forward("build.completed", {"a": 1}, audit_config=config)

        mock_journal.assert_called_once()
        mock_send.assert_called_once()


class TestResend:
    """Tests for AuditController.resend()."""

    def test_resend_calls_forward_per_record(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        exec_dir = log_dir / "exec1"
        exec_dir.mkdir()
        data = _sample_payload().model_dump(exclude_none=True)
        (exec_dir / "_execution.json").write_text(json.dumps(data, default=str))

        config = AuditConfigModel(sinks=[AuditSinkModel(name="resend_sink", integration="splunk-prod")])
        controller = AuditController(work_path=tmp_path)

        with patch.object(controller, "forward") as mock_forward:
            sent, failed = controller.resend(base_path=log_dir, audit_config=config)

        assert sent == 1
        assert failed == 0
        mock_forward.assert_called_once()
        args = mock_forward.call_args[0]
        assert args[0] == "deployment.completed"
        assert args[1]["deployment"] == "my_deploy"

    def test_resend_empty_dir(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        config = AuditConfigModel(sinks=[])
        controller = AuditController(work_path=tmp_path)
        sent, failed = controller.resend(base_path=log_dir, audit_config=config)
        assert sent == 0
        assert failed == 0

    def test_resend_counts_forward_failures(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        exec_dir = log_dir / "exec1"
        exec_dir.mkdir()
        data = _sample_payload().model_dump(exclude_none=True)
        (exec_dir / "_execution.json").write_text(json.dumps(data, default=str))

        config = AuditConfigModel(sinks=[])
        controller = AuditController(work_path=tmp_path)

        with patch.object(controller, "forward", side_effect=RuntimeError("boom")):
            sent, failed = controller.resend(base_path=log_dir, audit_config=config)

        assert sent == 0
        assert failed == 1
