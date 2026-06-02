"""Unit tests for ComposeDeployer."""

from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

from strata.controllers.value_controller import ResolvedValues
from strata.deployers.base_deployer import (
    STEP_APPLY,
    STEP_CHECK,
    STEP_DESTROY,
    STEP_OUTPUT,
    STEP_PLAN,
    STEP_PLAN_DESTROY,
    STEP_SETUP,
    STEP_SHOW_PLAN,
)
from strata.deployers.compose_deployer import ComposeDeployer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_deployer(
    tmp_path: Optional[Path] = None,
    force: bool = False,
    verbose: bool = False,
    resolved_values: Optional[ResolvedValues] = None,
) -> ComposeDeployer:
    """Build a ComposeDeployer backed by mock services."""
    stage = MagicMock()
    deployment_service = MagicMock()
    configuration_service = MagicMock()
    build_path = (tmp_path / "build") if tmp_path else Path("/build")
    work_path = tmp_path or Path("/work")
    return ComposeDeployer(
        stage=stage,
        deployment_service=deployment_service,
        configuration_service=configuration_service,
        build_path=build_path,
        work_path=work_path,
        verbose=verbose,
        force=force,
        resolved_values=resolved_values,
    )


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


class TestComposeDeployerMetadata:
    def test_deployer_name_is_compose(self):
        d = _make_deployer()
        assert d.get_deployer_name() == "compose"

    def test_get_supported_steps_contains_all_eight(self):
        d = _make_deployer()
        steps = d.get_supported_steps()
        assert STEP_SETUP in steps
        assert STEP_CHECK in steps
        assert STEP_PLAN in steps
        assert STEP_APPLY in steps
        assert STEP_DESTROY in steps
        assert STEP_PLAN_DESTROY in steps
        assert STEP_SHOW_PLAN in steps
        assert STEP_OUTPUT in steps


# ---------------------------------------------------------------------------
# validate_environment
# ---------------------------------------------------------------------------


class TestComposeDeployerValidateEnvironment:
    def test_success_sets_docker_instance(self):
        d = _make_deployer()
        with patch("strata.deployers.compose_deployer.DockerIntegration") as mock_int:
            instance = MagicMock()
            instance.ensure_available.return_value = (True, "")
            instance.get_version.return_value = "24.0.0"
            mock_int.return_value = instance
            ok, msgs = d.validate_environment()
        assert ok is True
        assert d._docker is instance
        assert any("24.0.0" in m for m in msgs)

    def test_unavailable_returns_false(self):
        d = _make_deployer()
        with patch("strata.deployers.compose_deployer.DockerIntegration") as mock_int:
            instance = MagicMock()
            instance.ensure_available.return_value = (False, "docker not in PATH")
            mock_int.return_value = instance
            ok, msgs = d.validate_environment()
        assert ok is False
        assert any("docker not in PATH" in m for m in msgs)


# ---------------------------------------------------------------------------
# validate_workspace
# ---------------------------------------------------------------------------


class TestComposeDeployerValidateWorkspace:
    def test_no_namespaces_returns_true_with_message(self):
        d = _make_deployer()
        d.deployment_service.get_build_path.return_value = Path("/build/deploy")
        d.deployment_service.get_namespace_services.return_value = {}
        ok, msgs = d.validate_workspace()
        assert ok is True
        assert any("No docker-compose.yml" in m for m in msgs)

    def test_unvalidated_namespace_skipped(self):
        d = _make_deployer()
        d.deployment_service.get_build_path.return_value = Path("/build/deploy")
        ns_service = MagicMock()
        ns_service.is_validated.return_value = False
        d.deployment_service.get_namespace_services.return_value = {"prod": ns_service}
        ok, msgs = d.validate_workspace()
        assert ok is True
        assert "prod" not in d._compose_files

    def test_missing_compose_file_not_included(self, tmp_path):
        d = _make_deployer(tmp_path=tmp_path)
        build_root = tmp_path / "build" / "deploy"
        build_root.mkdir(parents=True)
        d.deployment_service.get_build_path.return_value = build_root
        ns_service = MagicMock()
        ns_service.is_validated.return_value = True
        ns_service.model = MagicMock()
        d.deployment_service.get_namespace_services.return_value = {"prod": ns_service}
        # docker-compose.yml does NOT exist on disk
        ok, msgs = d.validate_workspace()
        assert ok is True
        assert "prod" not in d._compose_files

    def test_found_compose_file_populates_state(self, tmp_path):
        d = _make_deployer(tmp_path=tmp_path)
        build_root = tmp_path / "build"
        compose_dir = build_root / "prod"
        compose_dir.mkdir(parents=True)
        compose_file = compose_dir / "docker-compose.yml"
        compose_file.write_text("version: '3'\nservices:\n  web:\n    image: nginx\n")
        d.deployment_service.get_build_path.return_value = build_root
        ns_service = MagicMock()
        ns_service.is_validated.return_value = True
        ns_service.model = MagicMock()
        d.deployment_service.get_namespace_services.return_value = {"prod": ns_service}
        ok, msgs = d.validate_workspace()
        assert ok is True
        assert "prod" in d._compose_files
        assert d._compose_files["prod"] == compose_file


# ---------------------------------------------------------------------------
# Steps require init (_docker must be set)
# ---------------------------------------------------------------------------


class TestComposeDeployerStepsRequireInit:
    """All steps guard via _ready() — they must fail when _docker is None."""

    def test_setup_requires_init(self):
        d = _make_deployer()
        ok, msgs = d.setup()
        assert ok is False
        assert any("not initialized" in m for m in msgs)

    def test_check_requires_init(self):
        d = _make_deployer()
        ok, msgs = d.check()
        assert ok is False
        assert any("not initialized" in m for m in msgs)

    def test_plan_requires_init(self):
        d = _make_deployer()
        ok, msgs = d.plan()
        assert ok is False
        assert any("not initialized" in m for m in msgs)

    def test_apply_requires_init(self):
        d = _make_deployer()
        ok, msgs = d.apply()
        assert ok is False
        assert any("not initialized" in m for m in msgs)

    def test_destroy_requires_init(self):
        d = _make_deployer()
        ok, msgs = d.destroy()
        assert ok is False
        assert any("not initialized" in m for m in msgs)

    def test_plan_destroy_requires_init(self):
        d = _make_deployer()
        ok, msgs = d.plan_destroy()
        assert ok is False
        assert any("not initialized" in m for m in msgs)

    def test_output_requires_init(self):
        d = _make_deployer()
        ok, data, msgs = d.output()
        assert ok is False
        assert any("not initialized" in m for m in msgs)


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------


class TestComposeDeployerSetup:
    def test_success_returns_daemon_reachable(self):
        d = _make_deployer()
        d._docker = MagicMock()
        d._docker._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ok, msgs = d.setup()
        assert ok is True
        assert any("reachable" in m for m in msgs)

    def test_failure_returns_false_with_message(self):
        d = _make_deployer()
        d._docker = MagicMock()
        d._docker._run_integration.return_value = MagicMock(returncode=1, stdout="", stderr="Cannot connect to daemon")
        ok, msgs = d.setup()
        assert ok is False
        assert any("not reachable" in m for m in msgs)

    def test_calls_docker_info(self):
        d = _make_deployer()
        d._docker = MagicMock()
        d._docker._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        d.setup()
        args = d._docker._run_integration.call_args[0][0]
        assert "info" in args


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


class TestComposeDeployerCheck:
    def test_no_files_returns_true(self):
        d = _make_deployer()
        d._docker = MagicMock()
        d._compose_files = {}
        ok, msgs = d.check()
        assert ok is True

    def test_existing_file_returns_true(self, tmp_path):
        d = _make_deployer(tmp_path=tmp_path)
        d._docker = MagicMock()
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("version: '3'\n")
        d._compose_files = {"prod": compose_file}
        ok, msgs = d.check()
        assert ok is True
        assert any("prod" in m for m in msgs)

    def test_missing_file_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path=tmp_path)
        d._docker = MagicMock()
        missing = tmp_path / "nonexistent" / "docker-compose.yml"
        d._compose_files = {"prod": missing}
        ok, msgs = d.check()
        assert ok is False
        assert any("MISSING" in m for m in msgs)


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


class TestComposeDeployerPlan:
    def test_no_files_returns_true(self):
        d = _make_deployer()
        d._docker = MagicMock()
        d._compose_files = {}
        ok, msgs = d.plan()
        assert ok is True

    def test_counts_services_from_yaml(self, tmp_path):
        d = _make_deployer(tmp_path=tmp_path)
        d._docker = MagicMock()
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text(
            "version: '3'\nservices:\n  web:\n    image: nginx\n"
            "  db:\n    image: postgres\n  cache:\n    image: redis\n"
        )
        d._compose_files = {"prod": compose_file}
        ok, msgs = d.plan()
        assert ok is True
        assert any("3 service(s)" in m for m in msgs)

    def test_parse_error_logged_not_raised(self, tmp_path):
        d = _make_deployer(tmp_path=tmp_path)
        d._docker = MagicMock()
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text(":\tinvalid: yaml: [{{\n")
        d._compose_files = {"prod": compose_file}
        ok, msgs = d.plan()
        assert ok is True
        assert any("could not parse" in m for m in msgs)


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


class TestComposeDeployerApply:
    def test_no_files_returns_true(self):
        d = _make_deployer()
        d._docker = MagicMock()
        d._compose_files = {}
        ok, msgs = d.apply()
        assert ok is True
        assert any("no action required" in m.lower() for m in msgs)

    def test_stack_deploy_command_constructed(self, tmp_path):
        d = _make_deployer(tmp_path=tmp_path)
        d._docker = MagicMock()
        d._docker._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("version: '3'\n")
        d._compose_files = {"prod": compose_file}
        ok, msgs = d.apply()
        assert ok is True
        args = d._docker._run_integration.call_args[0][0]
        assert "stack" in args
        assert "deploy" in args
        assert "--with-registry-auth" in args
        assert "-c" in args

    def test_env_file_written_alongside_compose(self, tmp_path):
        rv = ResolvedValues(variables={"DB_HOST": "localhost"})
        d = _make_deployer(tmp_path=tmp_path, resolved_values=rv)
        d._docker = MagicMock()
        d._docker._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("version: '3'\n")
        d._compose_files = {"prod": compose_file}
        ok, msgs = d.apply()
        assert ok is True
        env_file = tmp_path / ".env"
        assert env_file.exists()
        assert "DB_HOST=localhost" in env_file.read_text()

    def test_env_file_written_even_when_empty(self, tmp_path):
        d = _make_deployer(tmp_path=tmp_path)  # no resolved_values
        d._docker = MagicMock()
        d._docker._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("version: '3'\n")
        d._compose_files = {"prod": compose_file}
        ok, msgs = d.apply()
        assert ok is True
        env_file = tmp_path / ".env"
        assert env_file.exists()
        assert env_file.read_text() == ""

    def test_injection_counts_logged(self, tmp_path):
        rv = ResolvedValues(
            variables={"DB_HOST": "localhost", "PORT": "5432"},
            features={"ENABLE_METRICS": True},
            secrets={"DB_PASS": "secret", "API_KEY": "key"},
        )
        d = _make_deployer(tmp_path=tmp_path, resolved_values=rv)
        d._docker = MagicMock()
        d._docker._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("version: '3'\n")
        d._compose_files = {"prod": compose_file}
        ok, msgs = d.apply()
        assert ok is True
        injection_msg = next((m for m in msgs if "Injecting" in m), None)
        assert injection_msg is not None
        assert "2 variable(s)" in injection_msg
        assert "1 feature(s)" in injection_msg
        assert "2 secret(s)" in injection_msg

    def test_no_injection_log_when_no_resolved_values(self, tmp_path):
        d = _make_deployer(tmp_path=tmp_path)  # no resolved_values
        d._docker = MagicMock()
        d._docker._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("version: '3'\n")
        d._compose_files = {"prod": compose_file}
        ok, msgs = d.apply()
        assert ok is True
        assert not any("Injecting" in m for m in msgs)

    def test_secrets_injected_into_env_during_deploy(self, tmp_path):
        """Secrets from resolved_values must be present in os.environ during the subprocess call."""
        import os

        captured_env: dict = {}

        rv = ResolvedValues(secrets={"MY_SECRET": "super-secret"})
        d = _make_deployer(tmp_path=tmp_path, resolved_values=rv)
        d._docker = MagicMock()

        def capture_and_succeed(args, **kwargs):
            captured_env["MY_SECRET"] = os.environ.get("MY_SECRET")
            return MagicMock(returncode=0, stdout="", stderr="")

        d._docker._run_integration.side_effect = capture_and_succeed
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("version: '3'\n")
        d._compose_files = {"prod": compose_file}
        ok, msgs = d.apply()
        assert ok is True
        assert captured_env["MY_SECRET"] == "super-secret"
        # Cleaned up after context exits
        assert os.environ.get("MY_SECRET") is None

    def test_failure_aborts_loop(self, tmp_path):
        d = _make_deployer(tmp_path=tmp_path)
        d._docker = MagicMock()
        d._docker._run_integration.return_value = MagicMock(returncode=1, stdout="", stderr="deploy failed")
        compose_file1 = tmp_path / "a-compose.yml"
        compose_file1.write_text("version: '3'\n")
        compose_file2 = tmp_path / "b-compose.yml"
        compose_file2.write_text("version: '3'\n")
        d._compose_files = {"ns1": compose_file1, "ns2": compose_file2}
        ok, msgs = d.apply()
        assert ok is False
        # Only one call should have been made — loop aborted after first failure
        assert d._docker._run_integration.call_count == 1


# ---------------------------------------------------------------------------
# destroy
# ---------------------------------------------------------------------------


class TestComposeDeployerDestroy:
    def test_destroy_requires_force(self):
        d = _make_deployer(force=False)
        d._docker = MagicMock()
        d._compose_files = {"prod": Path("/build/prod/docker-compose.yml")}
        ok, msgs = d.destroy()
        assert ok is False
        assert any("--force" in m for m in msgs)

    def test_no_files_returns_true(self):
        d = _make_deployer(force=True)
        d._docker = MagicMock()
        d._compose_files = {}
        ok, msgs = d.destroy()
        assert ok is True

    def test_stack_rm_command_constructed(self, tmp_path):
        d = _make_deployer(tmp_path=tmp_path, force=True)
        d._docker = MagicMock()
        d._docker._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        d._compose_files = {"prod": tmp_path / "docker-compose.yml"}
        ok, msgs = d.destroy()
        assert ok is True
        args = d._docker._run_integration.call_args[0][0]
        assert "stack" in args
        assert "rm" in args
        assert "prod" in args

    def test_failure_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path=tmp_path, force=True)
        d._docker = MagicMock()
        d._docker._run_integration.return_value = MagicMock(returncode=1, stdout="", stderr="stack not found")
        d._compose_files = {"prod": tmp_path / "docker-compose.yml"}
        ok, msgs = d.destroy()
        assert ok is False


# ---------------------------------------------------------------------------
# plan_destroy
# ---------------------------------------------------------------------------


class TestComposeDeployerPlanDestroy:
    def test_no_files_returns_true(self):
        d = _make_deployer()
        d._docker = MagicMock()
        d._compose_files = {}
        ok, msgs = d.plan_destroy()
        assert ok is True

    def test_running_stack_found(self, tmp_path):
        d = _make_deployer(tmp_path=tmp_path)
        d._docker = MagicMock()
        d._docker._run_integration.return_value = MagicMock(returncode=0, stdout="prod\nstaging\n", stderr="")
        d._compose_files = {"prod": tmp_path / "docker-compose.yml"}
        ok, msgs = d.plan_destroy()
        assert ok is True
        assert any("RUNNING" in m for m in msgs)

    def test_stack_ls_failure_treated_as_info(self, tmp_path):
        d = _make_deployer(tmp_path=tmp_path)
        d._docker = MagicMock()
        d._docker._run_integration.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        d._compose_files = {"prod": tmp_path / "docker-compose.yml"}
        ok, msgs = d.plan_destroy()
        assert ok is True


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------


class TestComposeDeployerOutput:
    def test_no_files_returns_empty_dict(self):
        d = _make_deployer()
        d._docker = MagicMock()
        d._compose_files = {}
        ok, data, msgs = d.output()
        assert ok is True
        assert data == {}

    def test_parses_tab_separated_output(self, tmp_path):
        d = _make_deployer(tmp_path=tmp_path)
        d._docker = MagicMock()
        d._docker._run_integration.return_value = MagicMock(
            returncode=0,
            stdout="nginx\t2/2\tnginx:latest\n",
            stderr="",
        )
        d._compose_files = {"prod": tmp_path / "docker-compose.yml"}
        ok, data, msgs = d.output()
        assert ok is True
        assert "prod" in data
        services = data["prod"]["services"]
        assert len(services) == 1
        assert services[0]["name"] == "nginx"
        assert services[0]["replicas"] == "2/2"
        assert services[0]["image"] == "nginx:latest"

    def test_failed_get_services_returns_empty_for_namespace(self, tmp_path):
        d = _make_deployer(tmp_path=tmp_path)
        d._docker = MagicMock()
        d._docker._run_integration.return_value = MagicMock(returncode=1, stdout="", stderr="stack not found")
        d._compose_files = {"prod": tmp_path / "docker-compose.yml"}
        ok, data, msgs = d.output()
        assert ok is True
        assert "prod" in data
        assert data["prod"]["services"] == []


# ---------------------------------------------------------------------------
# show_plan
# ---------------------------------------------------------------------------


class TestComposeDeployerShowPlan:
    def test_returns_ok_with_empty_dict(self):
        d = _make_deployer()
        ok, data, msgs = d.show_plan()
        assert ok is True
        assert data == {}

    def test_returns_informational_message(self):
        d = _make_deployer()
        ok, data, msgs = d.show_plan()
        assert len(msgs) > 0
        assert any("plan" in m.lower() for m in msgs)
