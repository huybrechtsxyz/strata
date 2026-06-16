"""Tests for the `deploy` command group."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from strata.commands.cli_deploy import deploy
from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.commands.deploy.run_deploy_command import RunDeployCommand
from strata.integrations.lock.base_lock_backend import LockBackendError, LockTimeoutError


class TestDeployRun:
    def test_run_basic(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.run_deploy_command.RunDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["run", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_run_with_file(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.run_deploy_command.RunDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["run", "--file", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_run_stage_option(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.run_deploy_command.RunDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["run", "--stage", "production", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_run_dry_run_flag(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.run_deploy_command.RunDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["run", "--dry-run", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_run_force_flag(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.run_deploy_command.RunDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["run", "--force", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_run_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.run_deploy_command.RunDeployCommand.execute", return_value=False):
            result = runner.invoke(deploy, ["run", "--work-path", str(tmp_path)])
        assert result.exit_code != 0


class TestDeployDestroy:
    def test_destroy_dry_run(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.destroy_deploy_command.DestroyDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["destroy", "--dry-run", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_destroy_force_flag(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.destroy_deploy_command.DestroyDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["destroy", "--force", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_destroy_stage_option(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.destroy_deploy_command.DestroyDeployCommand.execute", return_value=True):
            result = runner.invoke(
                deploy, ["destroy", "--stage", "production", "--dry-run", "--work-path", str(tmp_path)]
            )
        assert result.exit_code == 0

    def test_destroy_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.destroy_deploy_command.DestroyDeployCommand.execute", return_value=False):
            result = runner.invoke(deploy, ["destroy", "--dry-run", "--work-path", str(tmp_path)])
        assert result.exit_code != 0


class TestDeployStatus:
    def test_status_basic(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.status_deploy_command.StatusDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["status", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_status_show_plan_flag(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.status_deploy_command.StatusDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["status", "--plan", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_status_stage_option(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.status_deploy_command.StatusDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["status", "--stage", "production", "--work-path", str(tmp_path)])
        assert result.exit_code == 0


class TestDeployHistory:
    def test_history_basic(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.history_deploy_command.HistoryDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["history", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_history_lines_option(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.history_deploy_command.HistoryDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["history", "--lines", "10", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_history_operation_filter(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.history_deploy_command.HistoryDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["history", "--operation", "run", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_history_invalid_operation_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(deploy, ["history", "--operation", "badop", "--work-path", str(tmp_path)])
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# _evaluate_phase_policies unit tests
# ---------------------------------------------------------------------------


def _make_deployer_mock() -> MagicMock:
    """Return a deployer mock whose show_plan() returns a proper 3-tuple."""
    deployer = MagicMock()
    deployer.show_plan.return_value = (True, None, [])
    return deployer


def _make_run_command(tmp_path: Path) -> RunDeployCommand:
    with patch.object(BaseDeployCommand, "_initialize", return_value=None):
        cmd = RunDeployCommand(work_path=str(tmp_path), file="deploy.yaml")
    cmd._work_path = tmp_path
    cmd._build_path = tmp_path / "build"
    cmd._configuration_service = None
    cmd._deployment_service = None
    return cmd


def _make_policy_model(name: str, phase: str, enforcement: str = "deny", enabled: bool = True):
    m = MagicMock()
    m.phase = phase
    m.enabled = enabled
    m.type = "script"
    m.name = name
    return m


def _make_policy_result(name: str, passed: bool, enforcement: str = "deny", violations=None):
    r = MagicMock()
    r.policy_name = name
    r.passed = passed
    r.enforcement = enforcement
    r.violations = violations or ([] if passed else ["violation message"])
    return r


class TestEvaluatePhasePolices:
    def test_returns_true_when_no_configuration_service(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        cmd._configuration_service = None
        deployer = MagicMock()
        assert cmd._evaluate_phase_policies("deploy", MagicMock(), _make_deployer_mock()) is True

    def test_returns_true_when_no_matching_phase_policies(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        cfg_svc = MagicMock()
        cfg_svc.model.spec.policies = [_make_policy_model("p1", "plan")]
        cmd._configuration_service = cfg_svc
        assert cmd._evaluate_phase_policies("deploy", MagicMock(), _make_deployer_mock()) is True

    def test_returns_true_when_policy_passes(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        cfg_svc = MagicMock()
        policy_model = _make_policy_model("allow_all", "deploy", enforcement="deny")
        cfg_svc.model.spec.policies = [policy_model]
        cmd._configuration_service = cfg_svc

        passing_result = _make_policy_result("allow_all", passed=True)

        with (
            patch("strata.validators.policies.policy_engine.PolicyEngine") as mock_engine,
        ):
            mock_engine.return_value.evaluate.return_value = [passing_result]
            result = cmd._evaluate_phase_policies("deploy", MagicMock(), _make_deployer_mock())

        assert result is True

    def test_returns_false_when_deny_policy_fails(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        cfg_svc = MagicMock()
        policy_model = _make_policy_model("blocker", "deploy", enforcement="deny")
        cfg_svc.model.spec.policies = [policy_model]
        cmd._configuration_service = cfg_svc

        failing_result = _make_policy_result("blocker", passed=False, enforcement="deny")

        with patch("strata.validators.policies.policy_engine.PolicyEngine") as mock_engine:
            mock_engine.return_value.evaluate.return_value = [failing_result]
            result = cmd._evaluate_phase_policies("deploy", MagicMock(), _make_deployer_mock())

        assert result is False
        assert any("blocker" in e for e in cmd._errors)

    def test_returns_true_when_warn_policy_fails(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        cfg_svc = MagicMock()
        policy_model = _make_policy_model("advisory", "deploy", enforcement="warn")
        cfg_svc.model.spec.policies = [policy_model]
        cmd._configuration_service = cfg_svc

        warn_result = _make_policy_result("advisory", passed=False, enforcement="warn")

        with patch("strata.validators.policies.policy_engine.PolicyEngine") as mock_engine:
            mock_engine.return_value.evaluate.return_value = [warn_result]
            result = cmd._evaluate_phase_policies("deploy", MagicMock(), _make_deployer_mock())

        assert result is True
        assert not cmd._errors

    def test_policy_result_recorded_in_manifest(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        cfg_svc = MagicMock()
        policy_model = _make_policy_model("tag_check", "deploy", enforcement="deny")
        cfg_svc.model.spec.policies = [policy_model]
        cmd._configuration_service = cfg_svc

        passing_result = _make_policy_result("tag_check", passed=True, enforcement="deny")

        with patch("strata.validators.policies.policy_engine.PolicyEngine") as mock_engine:
            mock_engine.return_value.evaluate.return_value = [passing_result]
            cmd._evaluate_phase_policies("deploy", MagicMock(), _make_deployer_mock())

        assert len(cmd._policy_results) == 1
        assert cmd._policy_results[0].phase == "deploy"
        assert cmd._policy_results[0].policy_name == "tag_check"

    def test_disabled_policy_skipped(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        cfg_svc = MagicMock()
        cfg_svc.model.spec.policies = [_make_policy_model("off_policy", "deploy", enabled=False)]
        cmd._configuration_service = cfg_svc

        with patch("strata.validators.policies.policy_engine.PolicyEngine") as mock_engine:
            cmd._evaluate_phase_policies("deploy", MagicMock(), _make_deployer_mock())
            mock_engine.assert_not_called()

        assert not cmd._errors  # no crash, no errors

    def test_works_for_plan_phase_too(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        cfg_svc = MagicMock()
        policy_model = _make_policy_model("plan_guard", "plan", enforcement="deny")
        cfg_svc.model.spec.policies = [policy_model]
        cmd._configuration_service = cfg_svc

        passing_result = _make_policy_result("plan_guard", passed=True)

        with patch("strata.validators.policies.policy_engine.PolicyEngine") as mock_engine:
            mock_engine.return_value.evaluate.return_value = [passing_result]
            result = cmd._evaluate_phase_policies("plan", MagicMock(), _make_deployer_mock())

        assert result is True
        assert cmd._policy_results[0].phase == "plan"


# ---------------------------------------------------------------------------
# Helpers shared by TestLockingWiring
# ---------------------------------------------------------------------------


def _make_locking_spec(enabled: bool = True, wait_timeout: str = "5m") -> MagicMock:
    locking = MagicMock()
    locking.enabled = enabled
    locking.wait_timeout = wait_timeout
    spec = MagicMock()
    spec.locking = locking
    spec.approvals = None
    return spec


def _make_stage(name: str = "production") -> MagicMock:
    stage = MagicMock()
    stage.name = name
    stage.provisioner = None
    stage.topology = None
    stage.scope = None
    stage.on_failure = "stop"
    stage.approval = None
    return stage


class TestLockingWiring:
    """Unit-tests for _should_lock, _resolve_lock_backend, _acquire_lock, _release_lock,
    and the lock wrapping inside _execute_provisioning."""

    # ------------------------------------------------------------------
    # _should_lock
    # ------------------------------------------------------------------

    def test_should_lock_false_when_dry_run(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        cmd._dry_run = True
        svc = MagicMock()
        svc.model.spec = _make_locking_spec(enabled=True)
        cmd._deployment_service = svc
        assert cmd._should_lock() is False

    def test_should_lock_false_when_locking_disabled(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        cmd._dry_run = False
        svc = MagicMock()
        svc.model.spec = _make_locking_spec(enabled=False)
        cmd._deployment_service = svc
        assert cmd._should_lock() is False

    def test_should_lock_false_when_no_deployment_service(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        cmd._dry_run = False
        cmd._deployment_service = None
        assert cmd._should_lock() is False

    def test_should_lock_true_when_enabled_and_not_dry_run(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        cmd._dry_run = False
        svc = MagicMock()
        svc.model.spec = _make_locking_spec(enabled=True)
        cmd._deployment_service = svc
        assert cmd._should_lock() is True

    def test_should_lock_false_when_strategy_is_delegate(self, tmp_path):
        """delegate strategy defers locking to the backend — strata must not take a lock."""
        cmd = _make_run_command(tmp_path)
        cmd._dry_run = False
        svc = MagicMock()
        spec = _make_locking_spec(enabled=True)
        spec.locking.strategy = "delegate"
        svc.model.spec = spec
        cmd._deployment_service = svc
        assert cmd._should_lock() is False

    # ------------------------------------------------------------------
    # _acquire_lock / _release_lock
    # ------------------------------------------------------------------

    def test_acquire_lock_returns_handle_and_sets_lock_ref(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        svc = MagicMock()
        svc.model.spec = _make_locking_spec(enabled=True, wait_timeout="1m")
        svc.model.meta.name = "my-deploy"
        cmd._deployment_service = svc

        handle = MagicMock()
        handle.lock_id = "abc-123"
        handle.backend_type = "local"
        handle.acquired_at = "2024-01-01T00:00:00Z"

        backend = MagicMock()
        backend.acquire.return_value = handle

        result = cmd._acquire_lock(backend)

        assert result is handle
        assert cmd._lock_ref is not None
        assert cmd._lock_ref.lock_id == "abc-123"
        assert cmd._lock_ref.backend == "local"

    def test_acquire_lock_returns_none_on_timeout(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        svc = MagicMock()
        svc.model.spec = _make_locking_spec(enabled=True)
        svc.model.meta.name = "my-deploy"
        cmd._deployment_service = svc

        backend = MagicMock()
        exc = LockTimeoutError(deployment_name="my-deploy", timeout_seconds=60, holder="ci-bot")
        backend.acquire.side_effect = exc

        result = cmd._acquire_lock(backend)

        assert result is None
        assert len(cmd._errors) == 1

    def test_acquire_lock_returns_none_on_backend_error(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        svc = MagicMock()
        svc.model.spec = _make_locking_spec(enabled=True)
        svc.model.meta.name = "my-deploy"
        cmd._deployment_service = svc

        backend = MagicMock()
        backend.acquire.side_effect = LockBackendError("disk full")

        result = cmd._acquire_lock(backend)

        assert result is None
        assert len(cmd._errors) == 1

    def test_release_lock_updates_released_at(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        from strata.models.deployment_manifest_model import ManifestLockReferenceModel

        cmd._lock_ref = ManifestLockReferenceModel(
            lock_id="abc-123",
            backend="local",
            acquired_at="2024-01-01T00:00:00Z",
            holder="me",
            hostname="host",
        )

        handle = MagicMock()
        handle.lock_id = "abc-123"
        handle.backend_type = "local"

        backend = MagicMock()
        cmd._release_lock(backend, handle)

        backend.release.assert_called_once_with(handle)
        assert cmd._lock_ref.released_at is not None

    def test_release_lock_swallows_exceptions(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        handle = MagicMock()
        handle.lock_id = "abc-123"
        handle.backend_type = "local"

        backend = MagicMock()
        backend.release.side_effect = RuntimeError("disk gone")

        # Must not raise
        cmd._release_lock(backend, handle)

    # ------------------------------------------------------------------
    # Stage loop wrapping — acquire called before stages, release in finally
    # ------------------------------------------------------------------

    def test_acquire_called_before_stages_when_locking_enabled(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        cmd._dry_run = False
        cmd._output_format = "json"  # suppress emoji echoes on Windows terminals
        svc = MagicMock()
        svc.model.spec = _make_locking_spec(enabled=True)
        svc.model.meta.name = "my-deploy"
        cmd._deployment_service = svc

        handle = MagicMock()
        handle.lock_id = "h1"
        handle.backend_type = "local"
        handle.acquired_at = "2024-01-01T00:00:00Z"

        backend_mock = MagicMock()
        backend_mock.acquire.return_value = handle

        with (
            patch.object(cmd, "_should_lock", return_value=True),
            patch.object(cmd, "_resolve_lock_backend", return_value=backend_mock),
            patch.object(cmd, "_execute_stage_provisioning", return_value=True),
            patch.object(cmd, "_check_approvals", return_value=True),
        ):
            stage = _make_stage()
            # Call directly via the helper
            result = cmd._execute_provisioning()  # type: ignore[call-arg]

        # acquire was called
        backend_mock.acquire.assert_called_once()
        assert result is True

    def test_release_called_in_finally_on_success(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        cmd._dry_run = False
        cmd._output_format = "json"
        svc = MagicMock()
        svc.model.spec = _make_locking_spec(enabled=True)
        svc.model.meta.name = "my-deploy"
        cmd._deployment_service = svc

        handle = MagicMock()
        handle.lock_id = "h1"
        handle.backend_type = "local"
        handle.acquired_at = "2024-01-01T00:00:00Z"

        backend_mock = MagicMock()
        backend_mock.acquire.return_value = handle

        with (
            patch.object(cmd, "_should_lock", return_value=True),
            patch.object(cmd, "_resolve_lock_backend", return_value=backend_mock),
            patch.object(cmd, "_execute_stage_provisioning", return_value=True),
            patch.object(cmd, "_check_approvals", return_value=True),
        ):
            cmd._execute_provisioning()  # type: ignore[call-arg]

        backend_mock.release.assert_called_once_with(handle)

    def test_release_called_in_finally_on_stage_failure(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        cmd._dry_run = False
        cmd._output_format = "json"
        stage = _make_stage()
        spec = _make_locking_spec(enabled=True)
        spec.stages = [stage]
        svc = MagicMock()
        svc.model.spec = spec
        svc.model.meta.name = "my-deploy"
        cmd._deployment_service = svc

        handle = MagicMock()
        handle.lock_id = "h1"
        handle.backend_type = "local"
        handle.acquired_at = "2024-01-01T00:00:00Z"

        backend_mock = MagicMock()
        backend_mock.acquire.return_value = handle

        with (
            patch.object(cmd, "_should_lock", return_value=True),
            patch.object(cmd, "_resolve_lock_backend", return_value=backend_mock),
            patch.object(cmd, "_execute_stage_provisioning", return_value=False),
            patch.object(cmd, "_check_approvals", return_value=True),
        ):
            result = cmd._execute_provisioning()  # type: ignore[call-arg]

        assert result is False
        backend_mock.release.assert_called_once_with(handle)

    def test_dry_run_skips_lock(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        cmd._dry_run = True
        cmd._output_format = "json"

        backend_mock = MagicMock()

        with (
            patch.object(cmd, "_resolve_lock_backend", return_value=backend_mock),
            patch.object(cmd, "_execute_stage_provisioning", return_value=True),
            patch.object(cmd, "_check_approvals", return_value=True),
        ):
            cmd._execute_provisioning()  # type: ignore[call-arg]

        backend_mock.acquire.assert_not_called()

    def test_lock_timeout_makes_execute_provisioning_return_false(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        cmd._dry_run = False
        cmd._output_format = "json"
        svc = MagicMock()
        svc.model.spec = _make_locking_spec(enabled=True)
        svc.model.meta.name = "my-deploy"
        cmd._deployment_service = svc

        exc = LockTimeoutError(deployment_name="my-deploy", timeout_seconds=60, holder="ci-bot")
        backend_mock = MagicMock()
        backend_mock.acquire.side_effect = exc

        with (
            patch.object(cmd, "_should_lock", return_value=True),
            patch.object(cmd, "_resolve_lock_backend", return_value=backend_mock),
            patch.object(cmd, "_check_approvals", return_value=True),
        ):
            result = cmd._execute_provisioning()  # type: ignore[call-arg]

        assert result is False
        assert len(cmd._errors) > 0
