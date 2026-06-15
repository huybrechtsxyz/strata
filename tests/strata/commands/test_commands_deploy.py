"""Tests for the `deploy` command group."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from strata.commands.cli_deploy import deploy
from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.commands.deploy.run_deploy_command import RunDeployCommand


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
            patch("strata.validators.policies.policy_engine.PolicyEngine") as MockEngine,
        ):
            MockEngine.return_value.evaluate.return_value = [passing_result]
            result = cmd._evaluate_phase_policies("deploy", MagicMock(), _make_deployer_mock())

        assert result is True

    def test_returns_false_when_deny_policy_fails(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        cfg_svc = MagicMock()
        policy_model = _make_policy_model("blocker", "deploy", enforcement="deny")
        cfg_svc.model.spec.policies = [policy_model]
        cmd._configuration_service = cfg_svc

        failing_result = _make_policy_result("blocker", passed=False, enforcement="deny")

        with patch("strata.validators.policies.policy_engine.PolicyEngine") as MockEngine:
            MockEngine.return_value.evaluate.return_value = [failing_result]
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

        with patch("strata.validators.policies.policy_engine.PolicyEngine") as MockEngine:
            MockEngine.return_value.evaluate.return_value = [warn_result]
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

        with patch("strata.validators.policies.policy_engine.PolicyEngine") as MockEngine:
            MockEngine.return_value.evaluate.return_value = [passing_result]
            cmd._evaluate_phase_policies("deploy", MagicMock(), _make_deployer_mock())

        assert len(cmd._policy_results) == 1
        assert cmd._policy_results[0].phase == "deploy"
        assert cmd._policy_results[0].policy_name == "tag_check"

    def test_disabled_policy_skipped(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        cfg_svc = MagicMock()
        cfg_svc.model.spec.policies = [_make_policy_model("off_policy", "deploy", enabled=False)]
        cmd._configuration_service = cfg_svc

        with patch("strata.validators.policies.policy_engine.PolicyEngine") as MockEngine:
            cmd._evaluate_phase_policies("deploy", MagicMock(), _make_deployer_mock())
            MockEngine.assert_not_called()

        assert not cmd._errors  # no crash, no errors

    def test_works_for_plan_phase_too(self, tmp_path):
        cmd = _make_run_command(tmp_path)
        cfg_svc = MagicMock()
        policy_model = _make_policy_model("plan_guard", "plan", enforcement="deny")
        cfg_svc.model.spec.policies = [policy_model]
        cmd._configuration_service = cfg_svc

        passing_result = _make_policy_result("plan_guard", passed=True)

        with patch("strata.validators.policies.policy_engine.PolicyEngine") as MockEngine:
            MockEngine.return_value.evaluate.return_value = [passing_result]
            result = cmd._evaluate_phase_policies("plan", MagicMock(), _make_deployer_mock())

        assert result is True
        assert cmd._policy_results[0].phase == "plan"
