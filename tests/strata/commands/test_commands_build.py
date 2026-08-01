"""Tests for the `build` command group."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from strata.commands.builders.base_build_command import BaseBuildCommand
from strata.commands.builders.clean_build_command import CleanBuildCommand
from strata.commands.builders.plan_build_command import PlanBuildCommand
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


# ---------------------------------------------------------------------------
# PlanBuildCommand._build_value_status_rows() unit tests
# ---------------------------------------------------------------------------


def _make_plan_cmd(tmp_path):
    """Return a PlanBuildCommand with minimal state for unit testing."""
    from unittest.mock import MagicMock

    cmd = PlanBuildCommand.__new__(PlanBuildCommand)
    cmd._work_path = tmp_path
    cmd._build_path = tmp_path / "build"
    cmd._file_path = tmp_path / "deploy.yaml"
    cmd._stage = None
    cmd._artifacts_only = False
    cmd._ai = False
    cmd._strict_ai_review = None
    cmd._errors = []
    cmd._messages = []
    cmd._output_data = {}
    cmd._output_format = "console"
    cmd._output_quiet = False
    cmd._deployment_service = None
    cmd._configuration_service = None
    cmd._solution_controller = MagicMock()
    cmd.logger = MagicMock()
    return cmd


def _make_variable(key, store, default=None):
    from unittest.mock import MagicMock

    item = MagicMock()
    item.key = key
    item.store = MagicMock()
    item.store.value = store
    item.default = default
    return item


def _make_secret(key, store, generate=None, rotate=None):
    from unittest.mock import MagicMock

    item = MagicMock()
    item.key = key
    item.store = MagicMock()
    item.store.value = store
    item.generate = generate
    item.rotate = rotate
    return item


def _make_feature(key, store, default=None):
    from unittest.mock import MagicMock

    item = MagicMock()
    item.key = key
    item.store = MagicMock()
    item.store.value = store
    item.default = default
    return item


class TestPlanBuildValueStatus:
    """Unit tests for PlanBuildCommand._build_value_status_rows()."""

    def _cmd_with_env(self, tmp_path, variables=(), secrets=(), features=()):
        from unittest.mock import MagicMock

        cmd = _make_plan_cmd(tmp_path)
        env_svc = MagicMock()
        env_svc.get_variables.return_value = list(variables)
        env_svc.get_secrets.return_value = list(secrets)
        env_svc.get_features.return_value = list(features)
        svc = MagicMock()
        svc.get_environment_service.return_value = env_svc
        cmd._deployment_service = svc
        return cmd

    def test_returns_empty_when_no_deployment_service(self, tmp_path):
        cmd = _make_plan_cmd(tmp_path)
        assert cmd._build_value_status_rows() == []

    def test_returns_empty_when_no_env_service(self, tmp_path):
        from unittest.mock import MagicMock

        cmd = _make_plan_cmd(tmp_path)
        svc = MagicMock()
        svc.get_environment_service.return_value = None
        cmd._deployment_service = svc
        assert cmd._build_value_status_rows() == []

    def test_constant_variable_is_ok(self, tmp_path):
        cmd = self._cmd_with_env(tmp_path, variables=[_make_variable("WORKSPACE", "constant")])
        rows = cmd._build_value_status_rows()
        assert len(rows) == 1
        assert rows[0] == {"type": "variable", "key": "WORKSPACE", "store": "constant", "status": "ok", "detail": None}

    def test_environment_variable_is_ok(self, tmp_path):
        cmd = self._cmd_with_env(tmp_path, variables=[_make_variable("LOG_LEVEL", "environment")])
        rows = cmd._build_value_status_rows()
        assert rows[0]["status"] == "ok"

    def test_integration_variable_with_default_is_seeded(self, tmp_path):
        cmd = self._cmd_with_env(tmp_path, variables=[_make_variable("LOG_LEVEL", "azure-appconfig", default="info")])
        rows = cmd._build_value_status_rows()
        assert rows[0]["status"] == "seeded"
        assert rows[0]["detail"] == "default: info"

    def test_integration_variable_without_default_is_required(self, tmp_path):
        cmd = self._cmd_with_env(tmp_path, variables=[_make_variable("API_URL", "consul")])
        rows = cmd._build_value_status_rows()
        assert rows[0]["status"] == "required"
        assert rows[0]["detail"] is None

    def test_github_secret_is_ok(self, tmp_path):
        cmd = self._cmd_with_env(tmp_path, secrets=[_make_secret("TOKEN", "github")])
        rows = cmd._build_value_status_rows()
        assert rows[0]["status"] == "ok"

    def test_secret_with_generate_spec_is_generated(self, tmp_path):
        from unittest.mock import MagicMock

        gen = MagicMock()
        gen.type = MagicMock()
        gen.type.value = "password"
        gen.length = 32
        cmd = self._cmd_with_env(tmp_path, secrets=[_make_secret("DB_PASSWORD", "azure-keyvault", generate=gen)])
        rows = cmd._build_value_status_rows()
        assert rows[0]["status"] == "generated"
        assert rows[0]["detail"] == "password/32"

    def test_integration_secret_without_generate_is_required(self, tmp_path):
        cmd = self._cmd_with_env(tmp_path, secrets=[_make_secret("API_KEY", "vault")])
        rows = cmd._build_value_status_rows()
        assert rows[0]["status"] == "required"

    def test_feature_with_default_is_seeded(self, tmp_path):
        cmd = self._cmd_with_env(tmp_path, features=[_make_feature("DARK_MODE", "flagsmith", default="false")])
        rows = cmd._build_value_status_rows()
        assert rows[0]["status"] == "seeded"
        assert rows[0]["detail"] == "default: false"

    def test_feature_constant_is_ok(self, tmp_path):
        cmd = self._cmd_with_env(tmp_path, features=[_make_feature("FLAG", "constant")])
        rows = cmd._build_value_status_rows()
        assert rows[0]["status"] == "ok"

    def test_mixed_types_all_present(self, tmp_path):
        cmd = self._cmd_with_env(
            tmp_path,
            variables=[_make_variable("WORKSPACE", "constant"), _make_variable("LOG_LEVEL", "consul", default="info")],
            secrets=[_make_secret("DB_PASS", "vault")],
            features=[_make_feature("BETA", "flagsmith", default="true")],
        )
        rows = cmd._build_value_status_rows()
        assert len(rows) == 4
        assert [r["type"] for r in rows] == ["variable", "variable", "secret", "feature"]

    def test_values_key_in_output_data(self, tmp_path):
        from unittest.mock import MagicMock, patch

        cmd = _make_plan_cmd(tmp_path)
        env_svc = MagicMock()
        env_svc.get_variables.return_value = [_make_variable("X", "constant")]
        env_svc.get_secrets.return_value = []
        env_svc.get_features.return_value = []
        svc = MagicMock()
        svc.model.meta.name = "test-deploy"
        svc.get_build_path.return_value = tmp_path / "build"
        svc.get_environment_service.return_value = env_svc
        cmd._deployment_service = svc
        cmd._artifacts_only = True

        with (
            patch.object(cmd, "_build_to_temp", return_value=True),
            patch.object(cmd, "_compute_artifact_diff", return_value=[]),
            patch.object(cmd, "_print_console"),
        ):
            cmd._run_plan()

        assert "values" in cmd._output_data
        assert len(cmd._output_data["values"]) == 1


# ---------------------------------------------------------------------------
# TestBuildRunNdjsonStreaming — NDJSON stage events emitted per build phase
# ---------------------------------------------------------------------------


class TestBuildRunNdjsonStreaming:
    """stage_start / stage_end events emitted for each build phase in --output ndjson."""

    _PHASES = [
        "platform",
        "terraform",
        "ansible",
        "compose",
        "helm",
        "sync",
        "sbom",
    ]

    def _make_cmd(self, tmp_path: Path):
        from strata.commands.builders.run_build_command import RunBuildCommand

        cmd = RunBuildCommand(work_path=str(tmp_path), dry_run=False)
        cmd._work_path = tmp_path
        cmd._build_path = tmp_path / "build"
        cmd._output_format = "ndjson"
        cmd._dry_run = False
        cmd._require_lock = False
        cmd._audit = False
        cmd._deployment_service = MagicMock()
        cmd._deployment_service.check_require_lock_mode.return_value = None
        cmd._configuration_service = MagicMock()
        cmd._configuration_service.model = None
        cmd._solution_controller = MagicMock()
        cmd._solution_controller.get_repo_map.return_value = {}
        cmd._file_path = tmp_path / "deploy.yaml"
        return cmd

    def test_stage_start_and_end_emitted_for_all_phases(self, tmp_path):
        import contextlib

        cmd = self._make_cmd(tmp_path)
        cmd.emit_ndjson = MagicMock()

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(cmd, "_initialize", return_value=True))
            stack.enter_context(patch.object(cmd, "_before_execute", return_value=True))
            stack.enter_context(patch.object(cmd, "_after_execute", return_value=True))
            stack.enter_context(patch.object(cmd, "_finalize"))
            stack.enter_context(patch.object(cmd, "_run_lifecycle_phase", return_value=True))
            stack.enter_context(patch.object(cmd, "_evaluate_build_policies", return_value=True))
            stack.enter_context(patch.object(cmd, "_write_build_manifest", return_value=None))
            for phase in self._PHASES:
                stack.enter_context(patch.object(cmd, f"_execute_{phase}_build", return_value=True))
            cmd.execute()

        emitted = [call.args[0] for call in cmd.emit_ndjson.call_args_list]
        stage_starts = [e["stage"] for e in emitted if e.get("event") == "stage_start"]
        stage_ends = [e["stage"] for e in emitted if e.get("event") == "stage_end"]

        for phase in self._PHASES:
            assert f"{phase}_build" in stage_starts, f"Missing stage_start for {phase}"
            assert f"{phase}_build" in stage_ends, f"Missing stage_end for {phase}"

    def test_stage_end_success_false_on_phase_failure(self, tmp_path):
        cmd = self._make_cmd(tmp_path)
        cmd.emit_ndjson = MagicMock()

        with (
            patch.object(cmd, "_initialize", return_value=True),
            patch.object(cmd, "_before_execute", return_value=True),
            patch.object(cmd, "_after_execute", return_value=True),
            patch.object(cmd, "_finalize"),
            patch.object(cmd, "_run_lifecycle_phase", return_value=True),
            patch.object(cmd, "_execute_platform_build", return_value=False),
        ):
            cmd.execute()

        emitted = [call.args[0] for call in cmd.emit_ndjson.call_args_list]
        platform_ends = [e for e in emitted if e.get("event") == "stage_end" and e.get("stage") == "platform_build"]
        assert len(platform_ends) == 1
        assert platform_ends[0]["success"] is False

    def test_no_events_when_not_ndjson(self, tmp_path):
        import contextlib

        from strata.commands.builders.run_build_command import RunBuildCommand

        cmd = RunBuildCommand(work_path=str(tmp_path), dry_run=False)
        cmd._work_path = tmp_path
        cmd._build_path = tmp_path / "build"
        cmd._output_format = "console"
        cmd._dry_run = False
        cmd._require_lock = False
        cmd._audit = False
        cmd._deployment_service = MagicMock()
        cmd._deployment_service.check_require_lock_mode.return_value = None
        cmd._configuration_service = MagicMock()
        cmd._configuration_service.model = None
        cmd._solution_controller = MagicMock()
        cmd._solution_controller.get_repo_map.return_value = {}
        cmd._file_path = tmp_path / "deploy.yaml"
        cmd.emit_ndjson = MagicMock()

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(cmd, "_initialize", return_value=True))
            stack.enter_context(patch.object(cmd, "_before_execute", return_value=True))
            stack.enter_context(patch.object(cmd, "_after_execute", return_value=True))
            stack.enter_context(patch.object(cmd, "_finalize"))
            stack.enter_context(patch.object(cmd, "_run_lifecycle_phase", return_value=True))
            stack.enter_context(patch.object(cmd, "_evaluate_build_policies", return_value=True))
            stack.enter_context(patch.object(cmd, "_write_build_manifest", return_value=None))
            for phase in self._PHASES:
                stack.enter_context(patch.object(cmd, f"_execute_{phase}_build", return_value=True))
            cmd.execute()

        cmd.emit_ndjson.assert_not_called()


class TestWarmModelCache:
    """ADR-0026: RunBuildCommand._warm_model_cache() is best-effort and never raises."""

    def _make_cmd(self, tmp_path: Path):
        from strata.commands.builders.run_build_command import RunBuildCommand

        cmd = RunBuildCommand(work_path=str(tmp_path), dry_run=False)
        cmd._work_path = tmp_path
        cmd._file_path = tmp_path / "deploy.yaml"
        cmd._deployment_service = MagicMock()
        cmd._deployment_service.model.meta.name = "demo"
        cmd._platform_model = MagicMock()
        cmd._platform_model.model_dump.return_value = {"meta": {"name": "demo"}}
        return cmd

    def test_noop_when_no_platform_model(self, tmp_path):
        cmd = self._make_cmd(tmp_path)
        cmd._platform_model = None
        with patch("strata.controllers.cache_controller.CacheController") as mock_controller_cls:
            cmd._warm_model_cache()
        mock_controller_cls.assert_not_called()

    def test_warms_cache_with_built_model(self, tmp_path):
        cmd = self._make_cmd(tmp_path)
        mock_controller = MagicMock()
        mock_controller.collect_input_paths.return_value = ["/a/deploy.yaml"]
        mock_controller.cache.compute_cache_key.return_value = "abc123"

        with patch("strata.controllers.cache_controller.CacheController", return_value=mock_controller):
            cmd._warm_model_cache()

        mock_controller.cache.warm.assert_called_once_with(
            "demo", "deployment", "abc123", {"meta": {"name": "demo"}}, ["/a/deploy.yaml"]
        )

    def test_never_raises_when_cache_controller_fails(self, tmp_path):
        cmd = self._make_cmd(tmp_path)
        with patch("strata.controllers.cache_controller.CacheController", side_effect=RuntimeError("boom")):
            cmd._warm_model_cache()  # must not raise

    def test_skipped_when_cache_key_cannot_be_computed(self, tmp_path):
        cmd = self._make_cmd(tmp_path)
        mock_controller = MagicMock()
        mock_controller.collect_input_paths.return_value = ["/missing.yaml"]
        mock_controller.cache.compute_cache_key.return_value = None

        with patch("strata.controllers.cache_controller.CacheController", return_value=mock_controller):
            cmd._warm_model_cache()

        mock_controller.cache.warm.assert_not_called()
