"""Tests for the ``strata policy check`` command."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from strata.commands.cli_policy import policy_group
from strata.commands.policies.check_policy_command import CheckPolicyCommand

# ---------------------------------------------------------------------------
# CLI wiring tests
# ---------------------------------------------------------------------------


class TestPolicyCheckCli:
    def test_group_help_shows_check_subcommand(self):
        runner = CliRunner()
        result = runner.invoke(policy_group, ["--help"])
        assert "check" in result.output

    def test_check_help_exits_zero(self):
        runner = CliRunner()
        result = runner.invoke(policy_group, ["check", "--help"])
        assert result.exit_code == 0

    def test_check_help_shows_file_option(self):
        runner = CliRunner()
        result = runner.invoke(policy_group, ["check", "--help"])
        assert "--file" in result.output or "-f" in result.output

    def test_check_help_shows_phase_option(self):
        runner = CliRunner()
        result = runner.invoke(policy_group, ["check", "--help"])
        assert "--phase" in result.output

    def test_check_help_shows_plan_file_option(self):
        runner = CliRunner()
        result = runner.invoke(policy_group, ["check", "--help"])
        assert "--plan-file" in result.output

    def test_file_required_without_it_fails(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(policy_group, ["check", "--work-path", str(tmp_path)])
        assert result.exit_code != 0
        assert "Missing option" in result.output or "file" in result.output.lower()

    def test_exits_zero_when_execute_succeeds(self, tmp_path):
        runner = CliRunner()
        with patch.object(CheckPolicyCommand, "execute", return_value=True):
            result = runner.invoke(policy_group, ["check", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_exits_nonzero_when_execute_fails(self, tmp_path):
        runner = CliRunner()
        with patch.object(CheckPolicyCommand, "execute", return_value=False):
            result = runner.invoke(policy_group, ["check", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_accepts_phase_option(self, tmp_path):
        runner = CliRunner()
        with patch.object(CheckPolicyCommand, "execute", return_value=True):
            result = runner.invoke(
                policy_group,
                ["check", "-f", "deploy.yaml", "-p", "validate", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_accepts_multiple_phase_options(self, tmp_path):
        runner = CliRunner()
        with patch.object(CheckPolicyCommand, "execute", return_value=True):
            result = runner.invoke(
                policy_group,
                ["check", "-f", "deploy.yaml", "-p", "validate", "-p", "plan", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_accepts_plan_file_option(self, tmp_path):
        runner = CliRunner()
        with patch.object(CheckPolicyCommand, "execute", return_value=True):
            result = runner.invoke(
                policy_group,
                ["check", "-f", "deploy.yaml", "--plan-file", "infra.tfplan.json", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_invalid_phase_value_rejected(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            policy_group,
            ["check", "-f", "deploy.yaml", "-p", "notaphase", "--work-path", str(tmp_path)],
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Helpers for unit tests
# ---------------------------------------------------------------------------


def _make_command(tmp_path: Path, **kwargs) -> CheckPolicyCommand:
    with patch.object(CheckPolicyCommand, "_initialize", return_value=None):
        cmd = CheckPolicyCommand(
            file=kwargs.get("file", "deploy.yaml"),
            phase=kwargs.get("phase", None),
            plan_file=kwargs.get("plan_file", None),
            work_path=str(tmp_path),
        )
    cmd._work_path = tmp_path
    cmd._configuration_service = None
    cmd._deployment_service = None
    return cmd


def _stub_deployment_service(name: str = "prod", version: str = "1.0.0") -> MagicMock:
    svc = MagicMock()
    svc.model.meta.name = name
    svc.model.meta.labels = {"version": version}
    svc.get_build_path.return_value = Path("/nonexistent/build/prod-1.0.0")
    return svc


def _make_policy_model(name: str, phase: str, enforcement: str = "deny", type_: str = "naming_pattern"):
    from strata.models.policy_model import PolicyModel

    return PolicyModel.model_validate(
        {
            "name": name,
            "type": type_,
            "phase": phase,
            "enforcement": enforcement,
            "enabled": True,
        }
    )


def _make_policy_result(name: str, enforcement: str, passed: bool, violations=None):
    from strata.validators.policies.base_policy import PolicyResult

    return PolicyResult(
        passed=passed,
        policy_name=name,
        enforcement=enforcement,
        violations=violations or [],
    )


# ---------------------------------------------------------------------------
# _load_platform_artifact tests
# ---------------------------------------------------------------------------


class TestLoadPlatformArtifact:
    def test_returns_note_when_build_dir_absent(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._deployment_service = _stub_deployment_service()
        artifact, note = cmd._load_platform_artifact(tmp_path / "build")
        assert artifact is None
        assert note is not None
        assert "strata build run" in note

    def test_returns_graceful_note_when_platform_json_invalid(self, tmp_path):
        """Invalid platform.json (parse error) returns (None, note) — no exception."""
        build_dir = tmp_path / "build" / "prod-1.0.0"
        build_dir.mkdir(parents=True)
        (build_dir / "platform.json").write_text("not valid json{{")

        svc = MagicMock()
        svc.get_build_path.return_value = build_dir

        cmd = _make_command(tmp_path)
        cmd._deployment_service = svc

        artifact, note = cmd._load_platform_artifact(tmp_path / "build")
        assert artifact is None
        assert note is not None
        assert "skipped" in note

    def test_returns_none_when_deployment_service_none(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._deployment_service = None
        artifact, note = cmd._load_platform_artifact(tmp_path / "build")
        assert artifact is None
        assert note is None


# ---------------------------------------------------------------------------
# _load_plan_data tests
# ---------------------------------------------------------------------------


class TestLoadPlanData:
    def test_returns_note_when_no_plan_file_and_no_build_dir(self, tmp_path):
        cmd = _make_command(tmp_path)
        svc = MagicMock()
        svc.get_build_path.return_value = tmp_path / "nonexistent"
        cmd._deployment_service = svc
        data, note = cmd._load_plan_data(tmp_path / "build")
        assert data is None
        assert note is not None
        assert "strata deploy run --dry-run" in note

    def test_explicit_plan_file_not_found_returns_note(self, tmp_path):
        cmd = _make_command(tmp_path, plan_file="missing.tfplan.json")
        cmd._deployment_service = _stub_deployment_service()
        data, note = cmd._load_plan_data(tmp_path / "build")
        assert data is None
        assert note is not None
        assert "Plan file not found" in note

    def test_explicit_plan_file_loaded(self, tmp_path):
        plan = {"format_version": "1.0", "resource_changes": []}
        plan_file = tmp_path / "infra.tfplan.json"
        plan_file.write_text(json.dumps(plan))

        cmd = _make_command(tmp_path, plan_file=str(plan_file))
        cmd._deployment_service = _stub_deployment_service()
        data, note = cmd._load_plan_data(tmp_path / "build")
        assert data is not None
        assert note is None
        assert data["format_version"] == "1.0"

    def test_auto_discovered_plan_file(self, tmp_path):
        build_dir = tmp_path / "build" / "prod-1.0.0"
        build_dir.mkdir(parents=True)
        plan = {"format_version": "1.0", "resource_changes": []}
        (build_dir / "network.tfplan.json").write_text(json.dumps(plan))

        svc = MagicMock()
        svc.get_build_path.return_value = build_dir
        cmd = _make_command(tmp_path)
        cmd._deployment_service = svc

        data, note = cmd._load_plan_data(tmp_path / "build")
        assert data is not None
        assert note is None

    def test_relative_plan_file_resolved_from_work_path(self, tmp_path):
        plan = {"resource_changes": []}
        plan_file = tmp_path / "myplan.tfplan.json"
        plan_file.write_text(json.dumps(plan))

        cmd = _make_command(tmp_path, plan_file="myplan.tfplan.json")
        cmd._deployment_service = _stub_deployment_service()
        data, note = cmd._load_plan_data(tmp_path / "build")
        assert data is not None
        assert note is None


# ---------------------------------------------------------------------------
# _build_output_data tests
# ---------------------------------------------------------------------------


class TestBuildOutputData:
    def test_empty_results_structure(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._file_path = tmp_path / "deploy.yaml"
        cmd._requested_phases = ("validate", "build", "plan", "deploy")
        cmd._notes = []
        data = cmd._build_output_data([])
        assert data["policies_checked"] == 0
        assert data["passed"] == 0
        assert data["failed"] == 0
        assert data["denied"] == 0
        assert data["notes"] == []
        assert data["results"] == []

    def test_counts_passed_and_failed(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._file_path = tmp_path / "deploy.yaml"
        cmd._requested_phases = ("validate",)
        cmd._notes = []
        results = [
            {
                "policy": "a",
                "type": "naming_pattern",
                "phase": "validate",
                "enforcement": "deny",
                "passed": True,
                "violations": [],
            },
            {
                "policy": "b",
                "type": "naming_pattern",
                "phase": "validate",
                "enforcement": "deny",
                "passed": False,
                "violations": ["bad"],
            },
        ]
        data = cmd._build_output_data(results)
        assert data["policies_checked"] == 2
        assert data["passed"] == 1
        assert data["failed"] == 1
        assert data["denied"] == 1

    def test_notes_included(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._file_path = tmp_path / "deploy.yaml"
        cmd._requested_phases = ("plan",)
        cmd._notes = [{"phase": "plan", "message": "No plan file found."}]
        data = cmd._build_output_data([])
        assert len(data["notes"]) == 1
        assert data["notes"][0]["phase"] == "plan"

    def test_warn_violation_does_not_count_as_denied(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._file_path = tmp_path / "deploy.yaml"
        cmd._requested_phases = ("validate",)
        cmd._notes = []
        results = [
            {
                "policy": "w",
                "type": "naming_pattern",
                "phase": "validate",
                "enforcement": "warn",
                "passed": False,
                "violations": ["soft"],
            },
        ]
        data = cmd._build_output_data(results)
        assert data["denied"] == 0
        assert data["failed"] == 1


# ---------------------------------------------------------------------------
# _run_execution integration tests (mocked services)
# ---------------------------------------------------------------------------


class TestCheckPolicyCommandRunExecution:
    def _run_with_policies(self, tmp_path, policy_models, policy_results):
        """Helper: run _run_execution with mocked services and engine."""
        cmd = _make_command(tmp_path, file="deploy.yaml")
        cmd._start_time = __import__("datetime").datetime.now()

        # Config service
        cfg_svc = MagicMock()
        cfg_svc.model.spec.policies = policy_models
        cmd._configuration_service = cfg_svc
        cmd._raw_file = "deploy.yaml"

        # Deployment service
        dep_svc = MagicMock()
        dep_svc.model.meta.name = "prod"
        dep_svc.model.meta.labels = {"version": "1.0.0"}
        dep_svc.get_build_path.return_value = tmp_path / "nonexistent"
        dep_svc.is_validated.return_value = True

        with (
            patch.object(cmd, "_load_configuration_service", return_value=cfg_svc),
            patch.object(
                cmd,
                "_load_deployment_service",
                side_effect=lambda: setattr(cmd, "_deployment_service", dep_svc) or True,
            ),
            patch("strata.validators.policies.policy_engine.PolicyEngine") as MockEngine,
        ):
            engine_instance = MagicMock()
            engine_instance.evaluate.return_value = policy_results
            MockEngine.return_value = engine_instance
            cmd._run_execution()

        return cmd

    def test_no_policies_produces_empty_results(self, tmp_path):
        cmd = self._run_with_policies(tmp_path, [], [])
        assert cmd._results == []
        assert cmd._denied is False

    def test_passed_policy_not_denied(self, tmp_path):
        pm = _make_policy_model("naming_check", "validate")
        pr = _make_policy_result("naming_check", "deny", passed=True)
        cmd = self._run_with_policies(tmp_path, [pm], [pr])
        assert len(cmd._results) == 1
        assert cmd._results[0]["passed"] is True
        assert cmd._denied is False

    def test_failed_deny_policy_sets_denied(self, tmp_path):
        pm = _make_policy_model("zone_check", "plan", enforcement="deny", type_="customer_zone")
        pr = _make_policy_result("zone_check", "deny", passed=False, violations=["region us-east-1 not allowed"])
        cmd = self._run_with_policies(tmp_path, [pm], [pr])
        assert cmd._denied is True
        assert cmd._results[0]["violations"] == ["region us-east-1 not allowed"]

    def test_failed_warn_policy_does_not_set_denied(self, tmp_path):
        pm = _make_policy_model("soft_check", "validate", enforcement="warn")
        pr = _make_policy_result("soft_check", "warn", passed=False, violations=["soft violation"])
        cmd = self._run_with_policies(tmp_path, [pm], [pr])
        assert cmd._denied is False

    def test_plan_note_added_when_no_plan_file(self, tmp_path):
        pm = _make_policy_model("zone_check", "plan", type_="customer_zone")
        pr = _make_policy_result("zone_check", "deny", passed=True)
        cmd = self._run_with_policies(tmp_path, [pm], [pr])
        plan_notes = [n for n in cmd._notes if n["phase"] == "plan"]
        assert len(plan_notes) == 1
        assert "strata deploy run --dry-run" in plan_notes[0]["message"]
