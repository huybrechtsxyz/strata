"""Tests for the `build` command group."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from strata.commands.builders.base_build_command import BaseBuildCommand
from strata.commands.builders.clean_build_command import CleanBuildCommand
from strata.commands.cli_builders import build


class TestBuildRun:
    def test_run_basic(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.builders.run_build_command.RunBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["run", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_run_with_file(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.builders.run_build_command.RunBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["run", "--file", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_run_dry_run_flag(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.builders.run_build_command.RunBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["run", "--dry-run", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_run_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.builders.run_build_command.RunBuildCommand.execute", return_value=False):
            result = runner.invoke(build, ["run", "--work-path", str(tmp_path)])
        assert result.exit_code != 0


class TestBuildClean:
    def test_clean_basic(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.builders.clean_build_command.CleanBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["clean", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_clean_dry_run_flag(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.builders.clean_build_command.CleanBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["clean", "--dry-run", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_clean_with_file(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.builders.clean_build_command.CleanBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["clean", "--file", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0


class TestBuildPlan:
    def test_plan_basic(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.builders.plan_build_command.PlanBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["plan", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_plan_with_stage(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.builders.plan_build_command.PlanBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["plan", "--stage", "production", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_plan_artifacts_only_flag(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.builders.plan_build_command.PlanBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["plan", "--artifacts-only", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_plan_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.builders.plan_build_command.PlanBuildCommand.execute", return_value=False):
            result = runner.invoke(build, ["plan", "--work-path", str(tmp_path)])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# config_fetch_before / config_fetch_after lifecycle phase wiring
# ---------------------------------------------------------------------------


def _make_base_build_cmd(tmp_path: Path) -> BaseBuildCommand:
    """Return a BaseBuildCommand-like instance with minimal state for unit testing."""
    with patch.object(BaseBuildCommand, "_initialize", return_value=None):
        # CleanBuildCommand is a concrete subclass — use it as the test vehicle.
        cmd = CleanBuildCommand.__new__(CleanBuildCommand)
    cmd._work_path = tmp_path
    cmd._build_path = tmp_path / "build"
    cmd._raw_file = "deploy.yaml"
    cmd._file_path = tmp_path / "deploy.yaml"
    cmd._errors = []
    cmd._messages = []
    cmd._configuration_service = None
    cmd._deployment_service = None
    cmd._solution_controller = MagicMock()
    cmd._solution_controller.get_repo_map.return_value = {}
    cmd._output_format = "json"
    cmd.logger = MagicMock()
    return cmd
    """config_fetch_before and config_fetch_after are fired around _load_configuration_service()."""

    def _make_cmd_with_file(self, tmp_path: Path) -> BaseBuildCommand:
        cmd = _make_base_build_cmd(tmp_path)
        # Write a minimal deploy YAML so the file-exists check passes
        (tmp_path / "deploy.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: deployment\nmeta:\n  name: d1\nspec:\n",
            encoding="utf-8",
        )
        return cmd

    def test_config_fetch_before_called_before_load(self, tmp_path):
        cmd = self._make_cmd_with_file(tmp_path)

        call_order = []

        def record_lifecycle(phase, context=None):
            call_order.append(f"lifecycle:{phase}")
            return True

        def record_load():
            call_order.append("load_config")
            return MagicMock()

        with (
            patch.object(cmd, "_run_lifecycle_phase", side_effect=record_lifecycle),
            patch.object(cmd, "_load_configuration_service", side_effect=record_load),
            patch("strata.utils.system.resolve_path", return_value=tmp_path / "deploy.yaml"),
            patch.object(BaseBuildCommand, "_before_execute", wraps=cmd._before_execute),
        ):
            # We call the parent's _before_execute which has the new lifecycle hooks
            # Patch super()._before_execute to return True
            with patch("strata.commands.base_command.BaseCommand._before_execute", return_value=True):
                cmd._before_execute()

        fetch_before_idx = next((i for i, v in enumerate(call_order) if v == "lifecycle:config_fetch_before"), None)
        load_idx = next((i for i, v in enumerate(call_order) if v == "load_config"), None)
        fetch_after_idx = next((i for i, v in enumerate(call_order) if v == "lifecycle:config_fetch_after"), None)

        assert fetch_before_idx is not None, "config_fetch_before was never called"
        assert load_idx is not None, "load_configuration_service was never called"
        assert fetch_after_idx is not None, "config_fetch_after was never called"
        assert fetch_before_idx < load_idx, "config_fetch_before must fire before load"
        assert load_idx < fetch_after_idx, "config_fetch_after must fire after load"

    def test_config_fetch_before_failure_aborts_load(self, tmp_path):
        cmd = self._make_cmd_with_file(tmp_path)

        def failing_lifecycle(phase, context=None):
            if phase == "config_fetch_before":
                return False
            return True

        with (
            patch.object(cmd, "_run_lifecycle_phase", side_effect=failing_lifecycle),
            patch.object(cmd, "_load_configuration_service") as mock_load,
            patch("strata.utils.system.resolve_path", return_value=tmp_path / "deploy.yaml"),
            patch("strata.commands.base_command.BaseCommand._before_execute", return_value=True),
        ):
            result = cmd._before_execute()

        assert result is False
        mock_load.assert_not_called()

    def test_config_fetch_after_failure_aborts_before_execute(self, tmp_path):
        cmd = self._make_cmd_with_file(tmp_path)

        def failing_lifecycle(phase, context=None):
            if phase == "config_fetch_after":
                return False
            return True

        with (
            patch.object(cmd, "_run_lifecycle_phase", side_effect=failing_lifecycle),
            patch.object(cmd, "_load_configuration_service", return_value=MagicMock()),
            patch("strata.utils.system.resolve_path", return_value=tmp_path / "deploy.yaml"),
            patch("strata.commands.base_command.BaseCommand._before_execute", return_value=True),
        ):
            result = cmd._before_execute()

        assert result is False

    def test_config_fetch_context_includes_work_path_and_file(self, tmp_path):
        cmd = self._make_cmd_with_file(tmp_path)

        captured_contexts: dict = {}

        def capture(phase, context=None):
            captured_contexts[phase] = context or {}
            return True

        with (
            patch.object(cmd, "_run_lifecycle_phase", side_effect=capture),
            patch.object(cmd, "_load_configuration_service", return_value=MagicMock()),
            patch("strata.utils.system.resolve_path", return_value=tmp_path / "deploy.yaml"),
            patch("strata.commands.base_command.BaseCommand._before_execute", return_value=True),
        ):
            cmd._before_execute()

        for phase in ("config_fetch_before", "config_fetch_after"):
            assert phase in captured_contexts, f"{phase} context not captured"
            ctx = captured_contexts[phase]
            assert "work_path" in ctx, f"{phase} context missing work_path"
            assert "file" in ctx, f"{phase} context missing file"


# ---------------------------------------------------------------------------
# config_clean_before / config_clean_after lifecycle phase wiring
# ---------------------------------------------------------------------------


def _make_clean_cmd(tmp_path: Path) -> CleanBuildCommand:
    """Return a CleanBuildCommand with state pre-set for unit testing."""
    cmd = CleanBuildCommand.__new__(CleanBuildCommand)
    cmd._work_path = tmp_path
    cmd._build_path = tmp_path / "build"
    cmd._dry_run = False
    cmd._file_path = tmp_path / "deploy.yaml"
    cmd._errors = []
    cmd._messages = []
    cmd._output_data = {}
    cmd._output_format = "json"
    cmd._configuration_service = None
    cmd.logger = MagicMock()

    svc = MagicMock()
    svc.get_build_path.return_value = tmp_path / "build" / "d1"
    cmd._deployment_service = svc
    return cmd


class TestConfigCleanLifecyclePhase:
    """config_clean_before and config_clean_after fire in CleanBuildCommand.execute()."""

    def test_config_clean_phases_called_after_build_clean(self, tmp_path):
        cmd = _make_clean_cmd(tmp_path)
        (tmp_path / "build" / "d1").mkdir(parents=True)

        call_order = []

        def record_lifecycle(phase, context=None):
            call_order.append(phase)
            return True

        with (
            patch.object(cmd, "_initialize", return_value=True),
            patch.object(cmd, "_before_execute", return_value=True),
            patch.object(cmd, "_after_execute", return_value=True),
            patch.object(cmd, "_finalize", return_value=True),
            patch.object(cmd, "_run_lifecycle_phase", side_effect=record_lifecycle),
            patch("strata.services.configuration_service.ConfigurationService.reset"),
        ):
            cmd.execute()

        assert "build_clean_before" in call_order
        assert "build_clean_after" in call_order
        assert "config_clean_before" in call_order
        assert "config_clean_after" in call_order

        # config_clean phases must come AFTER build_clean phases
        bc_after_idx = call_order.index("build_clean_after")
        cc_before_idx = call_order.index("config_clean_before")
        cc_after_idx = call_order.index("config_clean_after")
        assert bc_after_idx < cc_before_idx, "config_clean_before must fire after build_clean_after"
        assert cc_before_idx < cc_after_idx, "config_clean_after must fire after config_clean_before"

    def test_config_clean_before_failure_aborts_execute(self, tmp_path):
        cmd = _make_clean_cmd(tmp_path)

        def failing_lifecycle(phase, context=None):
            if phase == "config_clean_before":
                return False
            return True

        with (
            patch.object(cmd, "_initialize", return_value=True),
            patch.object(cmd, "_before_execute", return_value=True),
            patch.object(cmd, "_after_execute", return_value=True),
            patch.object(cmd, "_finalize", return_value=True),
            patch.object(cmd, "_run_lifecycle_phase", side_effect=failing_lifecycle),
            patch("strata.services.configuration_service.ConfigurationService.reset") as mock_reset,
        ):
            result = cmd.execute()

        assert result is False
        mock_reset.assert_not_called()

    def test_config_clean_after_failure_aborts_execute(self, tmp_path):
        cmd = _make_clean_cmd(tmp_path)

        def failing_lifecycle(phase, context=None):
            if phase == "config_clean_after":
                return False
            return True

        with (
            patch.object(cmd, "_initialize", return_value=True),
            patch.object(cmd, "_before_execute", return_value=True),
            patch.object(cmd, "_after_execute", return_value=True),
            patch.object(cmd, "_finalize", return_value=True),
            patch.object(cmd, "_run_lifecycle_phase", side_effect=failing_lifecycle),
            patch("strata.services.configuration_service.ConfigurationService.reset"),
        ):
            result = cmd.execute()

        assert result is False

    def test_config_service_reset_called_when_not_dry_run(self, tmp_path):
        cmd = _make_clean_cmd(tmp_path)
        cmd._dry_run = False

        with (
            patch.object(cmd, "_initialize", return_value=True),
            patch.object(cmd, "_before_execute", return_value=True),
            patch.object(cmd, "_after_execute", return_value=True),
            patch.object(cmd, "_finalize", return_value=True),
            patch.object(cmd, "_run_lifecycle_phase", return_value=True),
            patch("strata.services.configuration_service.ConfigurationService.reset") as mock_reset,
        ):
            cmd.execute()

        mock_reset.assert_called_once()

    def test_config_service_reset_skipped_on_dry_run(self, tmp_path):
        cmd = _make_clean_cmd(tmp_path)
        cmd._dry_run = True

        with (
            patch.object(cmd, "_initialize", return_value=True),
            patch.object(cmd, "_before_execute", return_value=True),
            patch.object(cmd, "_after_execute", return_value=True),
            patch.object(cmd, "_finalize", return_value=True),
            patch.object(cmd, "_run_lifecycle_phase", return_value=True),
            patch("strata.services.configuration_service.ConfigurationService.reset") as mock_reset,
        ):
            cmd.execute()

        mock_reset.assert_not_called()

    def test_config_clean_context_includes_work_path_and_dry_run(self, tmp_path):
        cmd = _make_clean_cmd(tmp_path)
        cmd._dry_run = True

        captured: dict = {}

        def capture(phase, context=None):
            captured[phase] = context or {}
            return True

        with (
            patch.object(cmd, "_initialize", return_value=True),
            patch.object(cmd, "_before_execute", return_value=True),
            patch.object(cmd, "_after_execute", return_value=True),
            patch.object(cmd, "_finalize", return_value=True),
            patch.object(cmd, "_run_lifecycle_phase", side_effect=capture),
            patch("strata.services.configuration_service.ConfigurationService.reset"),
        ):
            cmd.execute()

        for phase in ("config_clean_before", "config_clean_after"):
            assert phase in captured, f"{phase} context not captured"
            ctx = captured[phase]
            assert "work_path" in ctx
            assert "dry_run" in ctx

    def test_run_basic(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.builders.run_build_command.RunBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["run", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_run_with_file(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.builders.run_build_command.RunBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["run", "--file", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_run_dry_run_flag(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.builders.run_build_command.RunBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["run", "--dry-run", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_run_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.builders.run_build_command.RunBuildCommand.execute", return_value=False):
            result = runner.invoke(build, ["run", "--work-path", str(tmp_path)])
        assert result.exit_code != 0


class TestBuildClean:
    def test_clean_basic(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.builders.clean_build_command.CleanBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["clean", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_clean_dry_run_flag(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.builders.clean_build_command.CleanBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["clean", "--dry-run", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_clean_with_file(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.builders.clean_build_command.CleanBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["clean", "--file", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0


class TestBuildPlan:
    def test_plan_basic(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.builders.plan_build_command.PlanBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["plan", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_plan_with_stage(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.builders.plan_build_command.PlanBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["plan", "--stage", "production", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_plan_artifacts_only_flag(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.builders.plan_build_command.PlanBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["plan", "--artifacts-only", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_plan_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.builders.plan_build_command.PlanBuildCommand.execute", return_value=False):
            result = runner.invoke(build, ["plan", "--work-path", str(tmp_path)])
        assert result.exit_code != 0
