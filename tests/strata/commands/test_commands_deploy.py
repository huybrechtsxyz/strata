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


class TestDeployPlan:
    def test_plan_basic(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.plan_deploy_command.PlanDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["plan", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_plan_with_file(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.plan_deploy_command.PlanDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["plan", "--file", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_plan_stage_option(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.plan_deploy_command.PlanDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["plan", "--stage", "production", "--work-path", str(tmp_path)])
        assert result.exit_code == 0


class TestDeployShow:
    def test_show_basic(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.show_deploy_command.ShowDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["show", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_show_with_file(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.show_deploy_command.ShowDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["show", "--file", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_show_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.show_deploy_command.ShowDeployCommand.execute", return_value=False):
            result = runner.invoke(deploy, ["show", "--work-path", str(tmp_path)])
        assert result.exit_code != 0


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
            patch.object(cmd, "_evaluate_deployment_gates", return_value=None),
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
            patch.object(cmd, "_evaluate_deployment_gates", return_value=None),
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
            patch.object(cmd, "_preflight_check_provisioners", return_value=[]),
            patch.object(cmd, "_execute_stage_provisioning", return_value=False),
            patch.object(cmd, "_evaluate_deployment_gates", return_value=None),
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
            patch.object(cmd, "_evaluate_deployment_gates", return_value=None),
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
            patch.object(cmd, "_evaluate_deployment_gates", return_value=None),
        ):
            result = cmd._execute_provisioning()  # type: ignore[call-arg]

        assert result is False
        assert len(cmd._errors) > 0


# ---------------------------------------------------------------------------
# Pre-flight provisioner validation — every stage's tool/auth must be checked
# BEFORE any stage runs (and before the deployment lock is acquired), so a
# later stage's missing tool can't be discovered only after an earlier stage
# already made real infrastructure changes.
# ---------------------------------------------------------------------------


class TestPreflightCheckProvisioners:
    def _deployer_mock(self, workspace_ok=True, workspace_msgs=None, env_ok=True, env_msgs=None) -> MagicMock:
        deployer = MagicMock()
        deployer.validate_workspace.return_value = (workspace_ok, workspace_msgs or [])
        deployer.validate_environment.return_value = (env_ok, env_msgs or [])
        return deployer

    def test_no_errors_when_all_stages_pass(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        stage = _make_stage("infra")
        deployer = self._deployer_mock()

        with patch.object(cmd, "_create_deployer", return_value=deployer):
            errors = cmd._preflight_check_provisioners([stage])

        assert errors == []
        deployer.validate_workspace.assert_called_once()
        deployer.validate_environment.assert_called_once()

    def test_stop_stage_failure_is_fatal(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        stage = _make_stage("infra")
        stage.on_failure = "stop"
        deployer = self._deployer_mock(env_ok=False, env_msgs=["terraform binary not found on PATH"])

        with patch.object(cmd, "_create_deployer", return_value=deployer):
            errors = cmd._preflight_check_provisioners([stage])

        assert len(errors) == 1
        assert "infra" in errors[0]
        assert "terraform binary not found on PATH" in errors[0]

    def test_continue_stage_failure_is_downgraded_to_warning(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        stage = _make_stage("optional-stage")
        stage.on_failure = "continue"
        deployer = self._deployer_mock(env_ok=False, env_msgs=["ansible not found"])

        with patch.object(cmd, "_create_deployer", return_value=deployer):
            errors = cmd._preflight_check_provisioners([stage])

        assert errors == []
        assert any("ansible not found" in m for m in cmd._messages)

    def test_rollback_stage_failure_is_fatal_like_stop(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        stage = _make_stage("infra")
        stage.on_failure = "rollback"
        deployer = self._deployer_mock(workspace_ok=False, workspace_msgs=["no *.tf files found"])

        with patch.object(cmd, "_create_deployer", return_value=deployer):
            errors = cmd._preflight_check_provisioners([stage])

        assert len(errors) == 1
        assert "no *.tf files found" in errors[0]

    def test_deployer_creation_failure_is_fatal_for_stop_stage(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        stage = _make_stage("infra")
        stage.on_failure = "stop"

        def _fail_create(_stage):
            cmd._errors.append("Stage 'infra': no provisioner or topology declared.")
            return None

        with patch.object(cmd, "_create_deployer", side_effect=_fail_create):
            errors = cmd._preflight_check_provisioners([stage])

        assert len(errors) == 1
        assert "no provisioner or topology declared" in errors[0]
        # The error reclaimed from _create_deployer must not leak into
        # self._errors directly — only into the returned fatal_errors list.
        assert cmd._errors == []

    def test_deployer_creation_failure_is_warning_for_continue_stage(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        stage = _make_stage("optional-stage")
        stage.on_failure = "continue"

        def _fail_create(_stage):
            cmd._errors.append("Stage 'optional-stage': no provisioner or topology declared.")
            return None

        with patch.object(cmd, "_create_deployer", side_effect=_fail_create):
            errors = cmd._preflight_check_provisioners([stage])

        assert errors == []
        assert cmd._errors == []
        assert any("no provisioner or topology declared" in m for m in cmd._messages)

    def test_only_first_failing_check_recorded_per_stage(self, tmp_path):
        """workspace check fails -> environment check is skipped for that stage."""
        cmd = _make_run_command(tmp_path)
        stage = _make_stage("infra")
        stage.on_failure = "stop"
        deployer = self._deployer_mock(workspace_ok=False, workspace_msgs=["workspace broken"])

        with patch.object(cmd, "_create_deployer", return_value=deployer):
            errors = cmd._preflight_check_provisioners([stage])

        assert len(errors) == 1
        assert "workspace broken" in errors[0]
        deployer.validate_environment.assert_not_called()

    def test_multiple_stages_all_checked_and_errors_aggregated(self, tmp_path):
        stage1 = _make_stage("infra")
        stage1.on_failure = "stop"
        stage2 = _make_stage("configure")
        stage2.on_failure = "stop"

        cmd = _make_run_command(tmp_path)
        good_deployer = self._deployer_mock()
        bad_deployer = self._deployer_mock(env_ok=False, env_msgs=["ansible not found"])

        def _create(stage):
            return good_deployer if stage.name == "infra" else bad_deployer

        with patch.object(cmd, "_create_deployer", side_effect=_create):
            errors = cmd._preflight_check_provisioners([stage1, stage2])

        assert len(errors) == 1
        assert "configure" in errors[0]
        assert "ansible not found" in errors[0]

    def test_execute_provisioning_aborts_before_lock_on_preflight_failure(self, tmp_path):
        """End-to-end: a failing stop-stage aborts _execute_provisioning() before
        the deployment lock is ever acquired."""
        cmd = _make_run_command(tmp_path)
        cmd._dry_run = False
        cmd._output_format = "json"
        stage = _make_stage("infra")
        stage.on_failure = "stop"
        spec = _make_locking_spec(enabled=True)
        spec.stages = [stage]
        svc = MagicMock()
        svc.model.spec = spec
        svc.model.meta.name = "my-deploy"
        cmd._deployment_service = svc

        backend_mock = MagicMock()

        with (
            patch.object(cmd, "_should_lock", return_value=True),
            patch.object(cmd, "_resolve_lock_backend", return_value=backend_mock),
            patch.object(cmd, "_preflight_check_provisioners", return_value=["Stage 'infra': terraform not found"]),
        ):
            result = cmd._execute_provisioning()  # type: ignore[call-arg]

        assert result is False
        backend_mock.acquire.assert_not_called()
        assert any("terraform not found" in e for e in cmd._errors)


class TestDeployList:
    """Tests for `strata deploy list`."""

    def _write_deployment(self, path: Path, name: str, layers: dict | None = None, tenant: str | None = None) -> Path:
        """Write a minimal deployment YAML to *path/<name>.yaml*."""
        layers_yaml = ""
        if layers:
            lines = "\n".join(f"    {k}: {v}" for k, v in layers.items())
            layers_yaml = f"  layers:\n{lines}\n"
        tenant_yaml = f"  tenant: {tenant}\n" if tenant else ""
        content = (
            f"apiVersion: strata.huybrechts.xyz/v1\n"
            f"kind: deployment\n"
            f"meta:\n"
            f"  name: {name}\n"
            f"spec:\n"
            f"{layers_yaml}"
            f"{tenant_yaml}"
            f"  workspace:\n"
            f"    name: ws_{name}\n"
            f"  environments:\n"
            f"    - env.yaml\n"
        )
        out = path / f"{name}.yaml"
        out.write_text(content, encoding="utf-8")
        return out

    def test_list_basic(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.list_deploy_command.ListDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_with_path(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.list_deploy_command.ListDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["list", "--path", str(tmp_path), "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.list_deploy_command.ListDeployCommand.execute", return_value=False):
            result = runner.invoke(deploy, ["list", "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_list_finds_deployment_manifests(self, tmp_path):
        """Real scan: deployment YAMLs are discovered; non-deployment files ignored."""
        self._write_deployment(tmp_path, "acme_prd", layers={"environment": "prd"}, tenant="acme")
        self._write_deployment(tmp_path, "globex_dev", layers={"environment": "dev", "zone": "eu"})
        (tmp_path / "not-a-deployment.yaml").write_text("kind: workspace\nmeta:\n  name: ws\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(deploy, ["list", "--path", str(tmp_path), "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "acme_prd" in result.output
        assert "globex_dev" in result.output
        assert "not-a-deployment" not in result.output

    def test_list_json_output(self, tmp_path):
        """--output json emits a JSON array of deployment entries."""
        import json

        self._write_deployment(tmp_path, "acme_prd", layers={"environment": "prd", "zone": "eu"}, tenant="acme")

        runner = CliRunner()
        result = runner.invoke(
            deploy,
            ["list", "--path", str(tmp_path), "--output", "json", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert isinstance(data, dict)
        deployments = data["data"]["deployments"]
        assert len(deployments) == 1
        entry = deployments[0]
        assert entry["name"] == "acme_prd"
        assert entry["environment"] == "prd"
        assert entry["zone"] == "eu"
        assert entry["tenant"] == "acme"
        assert entry["workspace"] == "ws_acme_prd"
        assert "file" in entry

    def test_list_layers_promoted_to_top_level(self, tmp_path):
        """All spec.layers keys appear as top-level fields in each entry."""
        import json

        self._write_deployment(tmp_path, "d1", layers={"env": "prd", "region": "northeurope", "tier": "premium"})

        runner = CliRunner()
        result = runner.invoke(
            deploy,
            ["list", "--path", str(tmp_path), "--output", "json", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        entry = json.loads(result.output)["data"]["deployments"][0]
        assert entry["env"] == "prd"
        assert entry["region"] == "northeurope"
        assert entry["tier"] == "premium"

    def test_list_recursive_scan(self, tmp_path):
        """Manifests in subdirectories are discovered."""
        subdir = tmp_path / "tenants" / "acme"
        subdir.mkdir(parents=True)
        self._write_deployment(subdir, "acme_prd", layers={"environment": "prd"}, tenant="acme")

        runner = CliRunner()
        result = runner.invoke(deploy, ["list", "--path", str(tmp_path), "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "acme_prd" in result.output

    def test_list_empty_directory(self, tmp_path):
        """Empty directory exits 0 with an appropriate message."""
        runner = CliRunner()
        result = runner.invoke(deploy, ["list", "--path", str(tmp_path), "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "no deployment" in result.output.lower()

    def test_list_invalid_path_exits_nonzero(self, tmp_path):
        """Non-existent scan path exits non-zero."""
        runner = CliRunner()
        result = runner.invoke(
            deploy,
            ["list", "--path", str(tmp_path / "does_not_exist"), "--work-path", str(tmp_path)],
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# _run_hierarchy_lifecycle_phase
# ---------------------------------------------------------------------------


def _make_service_mock(name: str) -> MagicMock:
    svc = MagicMock()
    svc.__class__.__name__ = name
    return svc


class TestRunHierarchyLifecyclePhase:
    """Unit tests for BaseDeployCommand._run_hierarchy_lifecycle_phase."""

    def _make_command(self, tmp_path) -> RunDeployCommand:
        cmd = RunDeployCommand.__new__(RunDeployCommand)
        cmd._work_path = tmp_path
        cmd._errors = []
        cmd._messages = []
        cmd._deployment_service = None
        cmd._output_format = "json"
        return cmd

    def _make_deployment_service(self, namespaces=None, providers=None, resources=None, modules=None):
        ws = _make_service_mock("WorkspaceService")
        ds = MagicMock()
        ds.get_workspace_service.return_value = ws
        ds.get_namespace_services.return_value = namespaces or {}
        ds.get_provider_services.return_value = providers or {}
        ds.get_resource_services.return_value = resources or {}
        ds.get_module_services.return_value = modules or {}
        return ds, ws

    def test_no_deployment_service_returns_true(self, tmp_path):
        cmd = self._make_command(tmp_path)
        with patch(
            "strata.controllers.lifecycle_controller.LifecycleController.execute_configuration_phase",
            return_value=True,
        ):
            assert cmd._run_hierarchy_lifecycle_phase("deploy_stage_before") is True

    def test_all_levels_called_in_order(self, tmp_path: Path) -> None:
        cmd = self._make_command(tmp_path)
        ns_svc = _make_service_mock("NamespaceService")
        prov_svc = _make_service_mock("ProviderService")
        res_svc = _make_service_mock("ResourceService")
        mod_svc = _make_service_mock("ModuleService")
        ds, ws_svc = self._make_deployment_service(
            namespaces={"ns1": ns_svc},
            providers={"prov1": prov_svc},
            resources={"res1": res_svc},
            modules={"res1:mod1": mod_svc},
        )
        cmd._deployment_service = ds

        call_order: list[str] = []

        def config_phase(*args, **kwargs):
            call_order.append("config")
            return True

        def workspace_phase(base_service, *args, **kwargs):
            call_order.append(f"workspace:{base_service.__class__.__name__}")
            return True

        with (
            patch(
                "strata.controllers.lifecycle_controller.LifecycleController.execute_configuration_phase",
                side_effect=config_phase,
            ),
            patch(
                "strata.controllers.lifecycle_controller.LifecycleController.execute_workspace_phase",
                side_effect=workspace_phase,
            ),
        ):
            result = cmd._run_hierarchy_lifecycle_phase("deploy_stage_before")

        assert result is True
        assert call_order[0] == "config"
        # workspace level fires for workspace + each ns + prov + res + module
        assert "workspace:WorkspaceService" in call_order
        assert any("NamespaceService" in e for e in call_order)
        assert any("ProviderService" in e for e in call_order)
        assert any("ResourceService" in e for e in call_order)
        assert any("ModuleService" in e for e in call_order)

    def test_config_failure_stops_traversal(self, tmp_path):
        cmd = self._make_command(tmp_path)
        ds, _ = self._make_deployment_service()
        cmd._deployment_service = ds

        lc = MagicMock()
        lc.execute_configuration_phase.return_value = False
        lc.get_errors.return_value = ["config script exited 1"]
        lc.execute_workspace_phase.return_value = True

        with patch("strata.controllers.lifecycle_controller.LifecycleController", return_value=lc):
            result = cmd._run_hierarchy_lifecycle_phase("deploy_stage_before")

        assert result is False
        lc.execute_workspace_phase.assert_not_called()
        assert any("config" in e for e in cmd._errors)

    def test_workspace_failure_stops_traversal(self, tmp_path):
        cmd = self._make_command(tmp_path)
        ns_svc = _make_service_mock("NamespaceService")
        ds, ws_svc = self._make_deployment_service(namespaces={"ns1": ns_svc})
        cmd._deployment_service = ds

        call_count = {"workspace": 0}

        def workspace_phase(base_service, *args, **kwargs):
            call_count["workspace"] += 1
            return False  # fail on workspace

        lc = MagicMock()
        lc.execute_configuration_phase.return_value = True
        lc.get_errors.return_value = ["workspace hook failed"]
        lc.execute_workspace_phase.side_effect = workspace_phase

        with patch("strata.controllers.lifecycle_controller.LifecycleController", return_value=lc):
            result = cmd._run_hierarchy_lifecycle_phase("deploy_stage_before")

        assert result is False
        # workspace level was called once (for the workspace itself)
        assert lc.execute_workspace_phase.call_count == 1

    def test_context_enriched_per_level(self, tmp_path: Path) -> None:
        cmd = self._make_command(tmp_path)
        ns_svc = _make_service_mock("NamespaceService")
        ds, ws_svc = self._make_deployment_service(namespaces={"production": ns_svc})
        cmd._deployment_service = ds

        captured_contexts: list[dict] = []

        def workspace_phase(base_service, phase_name, work_path, context=None, **kwargs):
            captured_contexts.append(dict(context or {}))
            return True

        with (
            patch(
                "strata.controllers.lifecycle_controller.LifecycleController.execute_configuration_phase",
                return_value=True,
            ),
            patch(
                "strata.controllers.lifecycle_controller.LifecycleController.execute_workspace_phase",
                side_effect=workspace_phase,
            ),
        ):
            cmd._run_hierarchy_lifecycle_phase("deploy_stage_before", context={"stage": "infra"})

        # namespace call should inject "namespace" key
        ns_ctx = next((c for c in captured_contexts if "namespace" in c), None)
        assert ns_ctx is not None
        assert ns_ctx["namespace"] == "production"
        assert ns_ctx["stage"] == "infra"


# ---------------------------------------------------------------------------
# --force-lock CLI wiring
# ---------------------------------------------------------------------------


class TestForceLockFlag:
    """--force-lock is parsed and forwarded to the underlying command."""

    def test_run_force_lock_flag(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.run_deploy_command.RunDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["run", "--force-lock", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_destroy_force_lock_flag(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.destroy_deploy_command.DestroyDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["destroy", "--force-lock", "--dry-run", "--work-path", str(tmp_path)])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# _acquire_lock — force_lock behaviour
# ---------------------------------------------------------------------------


class TestForceLockAcquire:
    """Tests for the --force-lock path inside _acquire_lock (base class)."""

    def test_force_lock_releases_held_lock_before_acquire(self, tmp_path):
        """When force_lock=True and a lock is held, force_release is called before acquire."""
        cmd = _make_run_command(tmp_path)
        cmd._force_lock = True
        svc = MagicMock()
        svc.model.spec = _make_locking_spec(enabled=True, wait_timeout="1m")
        svc.model.meta.name = "my-deploy"
        cmd._deployment_service = svc

        existing = MagicMock()
        existing.holder = "old-ci-bot"
        existing.hostname = "old-host"
        existing.lock_id = "old-lock-id"

        handle = MagicMock()
        handle.lock_id = "new-lock-id"
        handle.backend_type = "local"
        handle.acquired_at = "2024-01-01T00:00:00Z"

        backend = MagicMock()
        backend.status.return_value = existing
        backend.acquire.return_value = handle

        result = cmd._acquire_lock(backend)

        backend.force_release.assert_called_once_with("my-deploy")
        assert result is handle

    def test_force_lock_no_held_lock_skips_force_release(self, tmp_path):
        """When force_lock=True but no lock is held, force_release is not called."""
        cmd = _make_run_command(tmp_path)
        cmd._force_lock = True
        svc = MagicMock()
        svc.model.spec = _make_locking_spec(enabled=True, wait_timeout="1m")
        svc.model.meta.name = "my-deploy"
        cmd._deployment_service = svc

        handle = MagicMock()
        handle.lock_id = "new-lock"
        handle.backend_type = "local"
        handle.acquired_at = "2024-01-01T00:00:00Z"

        backend = MagicMock()
        backend.status.return_value = None  # nothing held
        backend.acquire.return_value = handle

        result = cmd._acquire_lock(backend)

        backend.force_release.assert_not_called()
        assert result is handle

    def test_force_lock_false_skips_status_check(self, tmp_path):
        """Default (force_lock=False): status() is never called before acquire."""
        cmd = _make_run_command(tmp_path)
        cmd._force_lock = False
        svc = MagicMock()
        svc.model.spec = _make_locking_spec(enabled=True, wait_timeout="1m")
        svc.model.meta.name = "my-deploy"
        cmd._deployment_service = svc

        handle = MagicMock()
        handle.lock_id = "lock-id"
        handle.backend_type = "local"
        handle.acquired_at = "2024-01-01T00:00:00Z"

        backend = MagicMock()
        backend.acquire.return_value = handle

        cmd._acquire_lock(backend)

        backend.status.assert_not_called()

    def test_force_release_backend_error_returns_none(self, tmp_path):
        """When force_release raises LockBackendError, _acquire_lock returns None."""
        cmd = _make_run_command(tmp_path)
        cmd._force_lock = True
        svc = MagicMock()
        svc.model.spec = _make_locking_spec(enabled=True, wait_timeout="1m")
        svc.model.meta.name = "my-deploy"
        cmd._deployment_service = svc

        existing = MagicMock()
        existing.holder = "old-ci"
        existing.hostname = "old-host"
        existing.lock_id = "old-lock"

        backend = MagicMock()
        backend.status.return_value = existing
        backend.force_release.side_effect = LockBackendError("storage unavailable")

        result = cmd._acquire_lock(backend)

        assert result is None
        assert len(cmd._errors) == 1
        assert "Force-lock" in cmd._errors[0]


# ---------------------------------------------------------------------------
# TestDestroyLocking — locking enforcement in DestroyDeployCommand
# ---------------------------------------------------------------------------


def _make_destroy_command(tmp_path: Path):
    from strata.commands.deploy.destroy_deploy_command import DestroyDeployCommand

    cmd = DestroyDeployCommand(work_path=str(tmp_path), force=True, dry_run=False)
    cmd._work_path = tmp_path
    cmd._build_path = tmp_path / "build"
    cmd._output_format = "json"
    return cmd


class TestDestroyLocking:
    """State locking enforcement for deploy destroy mirrors deploy run."""

    def test_should_lock_true_when_enabled(self, tmp_path):
        cmd = _make_destroy_command(tmp_path)
        cmd._dry_run = False
        svc = MagicMock()
        svc.model.spec = _make_locking_spec(enabled=True)
        cmd._deployment_service = svc
        assert cmd._should_lock() is True

    def test_should_lock_false_when_dry_run(self, tmp_path):
        cmd = _make_destroy_command(tmp_path)
        cmd._dry_run = True
        svc = MagicMock()
        svc.model.spec = _make_locking_spec(enabled=True)
        cmd._deployment_service = svc
        assert cmd._should_lock() is False

    def test_should_lock_false_when_disabled(self, tmp_path):
        cmd = _make_destroy_command(tmp_path)
        cmd._dry_run = False
        svc = MagicMock()
        svc.model.spec = _make_locking_spec(enabled=False)
        cmd._deployment_service = svc
        assert cmd._should_lock() is False

    def test_acquire_called_before_stages(self, tmp_path):
        """Lock is acquired before the stage loop runs."""
        cmd = _make_destroy_command(tmp_path)
        svc = MagicMock()
        svc.model.spec = _make_locking_spec(enabled=True)
        svc.model.spec.stages = [_make_stage()]
        svc.model.meta.name = "prod"
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
            patch.object(cmd, "_execute_stage_destroy", return_value=True),
        ):
            result = cmd._execute_provisioning()

        backend_mock.acquire.assert_called_once()
        assert result is True

    def test_release_called_in_finally_on_success(self, tmp_path):
        cmd = _make_destroy_command(tmp_path)
        svc = MagicMock()
        svc.model.spec = _make_locking_spec(enabled=True)
        svc.model.spec.stages = [_make_stage()]
        svc.model.meta.name = "prod"
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
            patch.object(cmd, "_execute_stage_destroy", return_value=True),
        ):
            cmd._execute_provisioning()

        backend_mock.release.assert_called_once_with(handle)

    def test_release_called_in_finally_on_failure(self, tmp_path):
        cmd = _make_destroy_command(tmp_path)
        svc = MagicMock()
        svc.model.spec = _make_locking_spec(enabled=True)
        svc.model.spec.stages = [_make_stage()]
        svc.model.meta.name = "prod"
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
            patch.object(cmd, "_execute_stage_destroy", return_value=False),
        ):
            result = cmd._execute_provisioning()

        assert result is False
        backend_mock.release.assert_called_once_with(handle)

    def test_lock_timeout_returns_false(self, tmp_path):
        cmd = _make_destroy_command(tmp_path)
        svc = MagicMock()
        svc.model.spec = _make_locking_spec(enabled=True)
        svc.model.spec.stages = [_make_stage()]
        svc.model.meta.name = "prod"
        cmd._deployment_service = svc

        backend_mock = MagicMock()
        backend_mock.acquire.side_effect = LockTimeoutError(
            deployment_name="prod", timeout_seconds=60, holder="other-ci"
        )

        with (
            patch.object(cmd, "_should_lock", return_value=True),
            patch.object(cmd, "_resolve_lock_backend", return_value=backend_mock),
        ):
            result = cmd._execute_provisioning()

        assert result is False
        assert len(cmd._errors) > 0


# ---------------------------------------------------------------------------
# deploy drift CLI routing tests
# ---------------------------------------------------------------------------


class TestDeployDrift:
    """CLI routing tests for `strata deploy drift` subgroup."""

    def test_drift_run_basic(self, tmp_path):
        """drift run with --file routes to DriftDeployCommand and exits 0."""
        runner = CliRunner()
        with patch("strata.commands.deploy.drift_deploy_command.DriftDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["drift", "run", "--file", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_drift_run_stage_option(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.drift_deploy_command.DriftDeployCommand.execute", return_value=True):
            result = runner.invoke(
                deploy,
                ["drift", "run", "--file", "deploy.yaml", "--stage", "networking", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_drift_run_severity_option(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.drift_deploy_command.DriftDeployCommand.execute", return_value=True):
            result = runner.invoke(
                deploy,
                ["drift", "run", "--file", "deploy.yaml", "--severity", "high", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_drift_run_baseline_flag(self, tmp_path):
        """--baseline flag is accepted."""
        runner = CliRunner()
        with patch("strata.commands.deploy.drift_deploy_command.DriftDeployCommand.execute", return_value=True):
            result = runner.invoke(
                deploy,
                ["drift", "run", "--file", "deploy.yaml", "--baseline", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_drift_run_invalid_severity_exits_2(self, tmp_path):
        """Passing an invalid severity value should return Click usage error (exit 2)."""
        runner = CliRunner()
        result = runner.invoke(
            deploy,
            ["drift", "run", "--file", "deploy.yaml", "--severity", "badvalue", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 2

    def test_drift_run_execute_false_returns_nonzero(self, tmp_path):
        """When DriftDeployCommand.execute() returns False the CLI exits non-zero."""
        runner = CliRunner()
        with patch("strata.commands.deploy.drift_deploy_command.DriftDeployCommand.execute", return_value=False):
            result = runner.invoke(deploy, ["drift", "run", "--file", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_drift_run_file_required(self, tmp_path):
        """Omitting --file should return Click usage error (exit 2)."""
        runner = CliRunner()
        result = runner.invoke(deploy, ["drift", "run", "--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_drift_acknowledge_basic(self, tmp_path):
        """drift acknowledge routes to AcknowledgeDriftDeployCommand."""
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.acknowledge_drift_deploy_command.AcknowledgeDriftDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                deploy,
                [
                    "drift",
                    "acknowledge",
                    "--file",
                    "deploy.yaml",
                    "--address",
                    "azurerm_autoscale_setting.web",
                    "--reason",
                    "auto-scaler",
                    "--work-path",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0

    def test_drift_acknowledge_remove_flag(self, tmp_path):
        """--remove flag is accepted."""
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.acknowledge_drift_deploy_command.AcknowledgeDriftDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                deploy,
                [
                    "drift",
                    "acknowledge",
                    "--file",
                    "deploy.yaml",
                    "--address",
                    "azurerm_autoscale_setting.web",
                    "--remove",
                    "--work-path",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0

    def test_drift_acknowledge_address_required(self, tmp_path):
        """Omitting --address should return Click usage error (exit 2)."""
        runner = CliRunner()
        result = runner.invoke(deploy, ["drift", "acknowledge", "--file", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_drift_history_basic(self, tmp_path):
        """drift history routes to DriftHistoryDeployCommand."""
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.drift_history_deploy_command.DriftHistoryDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                deploy,
                ["drift", "history", "--file", "deploy.yaml", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_drift_history_last_option(self, tmp_path):
        """--last N is accepted."""
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.drift_history_deploy_command.DriftHistoryDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                deploy,
                ["drift", "history", "--file", "deploy.yaml", "--last", "5", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# RunDeployCommand._resolve_values() seeded/generated note output tests
# ---------------------------------------------------------------------------


def _make_run_cmd(tmp_path):
    from unittest.mock import MagicMock

    from strata.commands.deploy.run_deploy_command import RunDeployCommand

    cmd = RunDeployCommand.__new__(RunDeployCommand)
    cmd._work_path = tmp_path
    cmd._file_path = tmp_path / "deploy.yaml"
    cmd._stage = None
    cmd._dry_run = False
    cmd._force = False
    cmd._errors = []
    cmd._messages = []
    cmd._output_data = {}
    cmd._output_format = "console"
    cmd._output_quiet = False
    cmd._output_verbose = False
    cmd._resolved_values = None
    cmd._deployment_service = None
    cmd._configuration_service = None
    cmd._solution_controller = MagicMock()
    cmd.logger = MagicMock()
    return cmd


class TestDeployRunSeedNotes:
    """Unit tests for seeded/generated note output in RunDeployCommand._resolve_values()."""

    def _run_resolve(
        self,
        tmp_path,
        variable_notes=None,
        secret_notes=None,
        feature_notes=None,
        variables=None,
        secrets=None,
        features=None,
    ):
        from unittest.mock import MagicMock, patch

        from strata.utils.resolved_values import ResolvedValues

        cmd = _make_run_cmd(tmp_path)
        resolved = ResolvedValues(
            variables=variables or {"X": "1"},
            secrets=secrets or {},
            features=features or {},
            variable_notes=variable_notes or {},
            secret_notes=secret_notes or {},
            feature_notes=feature_notes or {},
        )
        value_ctrl = MagicMock()
        value_ctrl.resolve_values.return_value = (True, resolved, [])
        with patch("strata.commands.deploy.run_deploy_command.ValueController", return_value=value_ctrl):
            output = []
            with patch("click.echo", side_effect=output.append):
                cmd._resolve_values()
        return output

    def test_seeded_variable_shown_in_output(self, tmp_path):
        output = self._run_resolve(
            tmp_path,
            variables={"LOG_LEVEL": "info"},
            variable_notes={"LOG_LEVEL": "default: info"},
        )
        seeded_lines = [line for line in output if "Seeded on first run" in str(line)]
        assert len(seeded_lines) == 1
        assert "LOG_LEVEL=info" in seeded_lines[0]

    def test_seeded_feature_shown_in_output(self, tmp_path):
        output = self._run_resolve(
            tmp_path,
            variables={"X": "1"},
            features={"DARK_MODE": False},
            feature_notes={"DARK_MODE": "default: false"},
        )
        seeded_lines = [line for line in output if "Seeded on first run" in str(line)]
        assert len(seeded_lines) == 1
        assert "DARK_MODE=false" in seeded_lines[0]

    def test_generated_secret_shown_in_output(self, tmp_path):
        output = self._run_resolve(
            tmp_path,
            variables={"X": "1"},
            secrets={"DB_PASSWORD": "s3cr3t"},
            secret_notes={"DB_PASSWORD": "generated"},
        )
        gen_lines = [line for line in output if "Generated on first run" in str(line)]
        assert len(gen_lines) == 1
        assert "DB_PASSWORD" in gen_lines[0]

    def test_no_extra_lines_when_notes_empty(self, tmp_path):
        output = self._run_resolve(tmp_path)
        assert not any("Seeded on first run" in str(line) for line in output)
        assert not any("Generated on first run" in str(line) for line in output)

    def test_non_default_variable_note_not_shown(self, tmp_path):
        output = self._run_resolve(
            tmp_path,
            variables={"X": "1"},
            variable_notes={"X": "some-other-note"},
        )
        assert not any("Seeded on first run" in str(line) for line in output)


# ---------------------------------------------------------------------------
# TestResolveValuesStoreUnavailable — bug fix: RunDeployCommand._resolve_values()
# must abort the deploy (return False) when a store is unreachable/unauthenticated,
# even in non-strict mode, instead of silently continuing.
# ---------------------------------------------------------------------------


class TestResolveValuesStoreUnavailable:
    def _run_resolve(self, tmp_path, store_unavailable_errors, ok=False, errors=None):
        from unittest.mock import MagicMock, patch

        from strata.utils.resolved_values import ResolvedValues

        cmd = _make_run_cmd(tmp_path)
        resolved = ResolvedValues(store_unavailable_errors=list(store_unavailable_errors))
        value_ctrl = MagicMock()
        value_ctrl.resolve_values.return_value = (ok, resolved, errors or [])
        with patch("strata.commands.deploy.run_deploy_command.ValueController", return_value=value_ctrl):
            with patch("click.echo"):
                result = cmd._resolve_values()
        return cmd, result

    def test_returns_false_when_store_unavailable(self, tmp_path):
        cmd, result = self._run_resolve(tmp_path, ["Secret 'DB_PASSWORD': Store 'infisical' unavailable: auth failed"])
        assert result is False

    def test_store_unavailable_errors_added_to_command_errors(self, tmp_path):
        msg = "Secret 'DB_PASSWORD': Store 'infisical' unavailable: auth failed"
        cmd, result = self._run_resolve(tmp_path, [msg])
        assert msg in cmd._errors

    def test_no_store_unavailable_errors_returns_true(self, tmp_path):
        cmd, result = self._run_resolve(tmp_path, [], ok=True)
        assert result is True
        assert cmd._errors == []


# ---------------------------------------------------------------------------
# TestDestroyNdjsonStreaming — NDJSON event emission in _execute_stage_destroy
# ---------------------------------------------------------------------------


def _make_destroy_stage(name: str = "infra"):
    stage = MagicMock()
    stage.name = name
    stage.provisioner = "tf_main"
    stage.topology = None
    return stage


# ---------------------------------------------------------------------------
# _run_cost_diff_for_stage — gated on CostController.is_auto_diff_enabled()
# (i.e. a cost estimator declared in spec.integrations), not just on whether
# an estimator binary happens to be installed.
# ---------------------------------------------------------------------------


class TestRunCostDiffForStage:
    def _make_stage(self, provisioner: str = "terraform"):
        stage = MagicMock()
        stage.name = "infra"
        stage.provisioner = provisioner
        return stage

    def test_skips_when_auto_diff_not_enabled(self, tmp_path):
        """Not declared in spec.integrations -> never even checks is_available()
        or attempts a diff, regardless of whether the binary is installed."""
        cmd = _make_run_command(tmp_path)
        cmd._deployment_service = MagicMock()
        stage = self._make_stage()

        mock_controller = MagicMock()
        mock_controller.is_auto_diff_enabled.return_value = False

        with patch("strata.controllers.cost_controller.CostController", return_value=mock_controller):
            cmd._run_cost_diff_for_stage(stage, tmp_path / "plan.json")

        mock_controller.is_auto_diff_enabled.assert_called_once()
        mock_controller.is_available.assert_not_called()
        mock_controller.diff.assert_not_called()

    def test_proceeds_when_auto_diff_enabled_and_available(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        cmd._deployment_service = MagicMock()
        cmd._output_format = "json"  # suppress console rendering
        stage = self._make_stage()

        mock_controller = MagicMock()
        mock_controller.is_auto_diff_enabled.return_value = True
        mock_controller.is_available.return_value = True
        mock_controller.diff.return_value = (True, {"diff": {}, "totalMonthlyCost": "0", "pastTotalMonthlyCost": "0"})
        mock_controller.get_messages.return_value = []
        mock_controller.get_errors.return_value = []

        with patch("strata.controllers.cost_controller.CostController", return_value=mock_controller):
            cmd._run_cost_diff_for_stage(stage, tmp_path / "plan.json")

        mock_controller.is_auto_diff_enabled.assert_called_once()
        mock_controller.is_available.assert_called_once()
        mock_controller.diff.assert_called_once()

    def test_skips_when_enabled_but_binary_not_available(self, tmp_path):
        """Declared in spec.integrations but the binary itself isn't installed
        -> still skips silently (non-fatal), same as before this change."""
        cmd = _make_run_command(tmp_path)
        cmd._deployment_service = MagicMock()
        stage = self._make_stage()

        mock_controller = MagicMock()
        mock_controller.is_auto_diff_enabled.return_value = True
        mock_controller.is_available.return_value = False

        with patch("strata.controllers.cost_controller.CostController", return_value=mock_controller):
            cmd._run_cost_diff_for_stage(stage, tmp_path / "plan.json")

        mock_controller.diff.assert_not_called()


def _make_mock_deployer(step_names=("setup", "destroy")):
    deployer = MagicMock()
    deployer.get_deployer_name.return_value = "terraform"
    deployer.get_supported_steps.return_value = list(step_names)
    deployer.validate_workspace.return_value = (True, [])
    deployer.validate_environment.return_value = (True, [])
    for step in step_names:
        getattr(deployer, step).return_value = (True, [])
    return deployer


class TestDestroyNdjsonStreaming:
    """NDJSON stage/step events emitted by _execute_stage_destroy."""

    def _make_cmd(self, tmp_path: Path, force: bool = True):
        from strata.commands.deploy.destroy_deploy_command import DestroyDeployCommand

        cmd = DestroyDeployCommand(work_path=str(tmp_path), force=force, dry_run=False)
        cmd._work_path = tmp_path
        cmd._build_path = tmp_path / "build"
        cmd._output_format = "ndjson"
        cmd._dry_run = False
        cmd._force = force
        cmd._deployment_service = MagicMock()
        cmd._configuration_service = MagicMock()
        cmd._solution_controller = MagicMock()
        return cmd

    def test_stage_start_end_emitted(self, tmp_path):
        cmd = self._make_cmd(tmp_path)
        deployer = _make_mock_deployer()
        with patch.object(cmd, "_create_deployer", return_value=deployer):
            cmd.emit_ndjson = MagicMock()
            result = cmd._execute_stage_destroy(_make_destroy_stage())

        assert result is True
        events = [call.args[0]["event"] for call in cmd.emit_ndjson.call_args_list]
        assert events[0] == "stage_start"
        assert events[-1] == "stage_end"

    def test_step_start_end_emitted_per_step(self, tmp_path):
        cmd = self._make_cmd(tmp_path)
        deployer = _make_mock_deployer(step_names=("setup", "destroy"))
        with patch.object(cmd, "_create_deployer", return_value=deployer):
            cmd.emit_ndjson = MagicMock()
            cmd._execute_stage_destroy(_make_destroy_stage())

        events = [call.args[0]["event"] for call in cmd.emit_ndjson.call_args_list]
        assert events.count("step_start") == 2
        assert events.count("step_end") == 2

    def test_step_end_failure_emitted_on_step_failure(self, tmp_path):
        cmd = self._make_cmd(tmp_path)
        deployer = _make_mock_deployer()
        deployer.setup.return_value = (True, [])
        deployer.destroy.return_value = (False, ["destroy failed"])
        with patch.object(cmd, "_create_deployer", return_value=deployer):
            cmd.emit_ndjson = MagicMock()
            result = cmd._execute_stage_destroy(_make_destroy_stage())

        assert result is False
        step_ends = [call.args[0] for call in cmd.emit_ndjson.call_args_list if call.args[0].get("event") == "step_end"]
        failed = [e for e in step_ends if not e.get("success")]
        assert len(failed) == 1
        assert failed[0]["step"] == "destroy"

    def test_stage_end_not_emitted_on_failure(self, tmp_path):
        cmd = self._make_cmd(tmp_path)
        deployer = _make_mock_deployer()
        deployer.destroy.return_value = (False, ["fail"])
        with patch.object(cmd, "_create_deployer", return_value=deployer):
            cmd.emit_ndjson = MagicMock()
            cmd._execute_stage_destroy(_make_destroy_stage())

        events = [call.args[0]["event"] for call in cmd.emit_ndjson.call_args_list]
        assert "stage_end" not in events

    def test_no_events_when_not_ndjson(self, tmp_path):
        from strata.commands.deploy.destroy_deploy_command import DestroyDeployCommand

        cmd = DestroyDeployCommand(work_path=str(tmp_path), force=True, dry_run=False)
        cmd._work_path = tmp_path
        cmd._build_path = tmp_path / "build"
        cmd._output_format = "console"
        cmd._dry_run = False
        cmd._force = True
        cmd._deployment_service = MagicMock()
        cmd._configuration_service = MagicMock()
        cmd._solution_controller = MagicMock()

        deployer = _make_mock_deployer()
        with patch.object(cmd, "_create_deployer", return_value=deployer):
            cmd.emit_ndjson = MagicMock()
            cmd._execute_stage_destroy(_make_destroy_stage())

        cmd.emit_ndjson.assert_not_called()
