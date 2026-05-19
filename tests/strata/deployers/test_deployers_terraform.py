"""Unit tests for TerraformDeployer."""

import json
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

from strata.deployers.terraform_deployer import TerraformDeployer


def _ok(stdout="", stderr="", returncode=0):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def _fail(stderr="error output"):
    return _ok(returncode=1, stderr=stderr)


def _make_deployer(
    tmp_path: Path,
    tf_files: bool = True,
    working_dir: Optional[Path] = None,
    stage_name: str = "production",
    stage_provisioner: Optional[str] = None,
    stage_topology: Optional[str] = None,
    provisioners=None,
    topology=None,
    verbose: bool = False,
    force: bool = False,
    resolved_values=None,
):
    """Build a ready-to-use TerraformDeployer with mocked internals.

    Sets _working_dir, _plan_file, and _tf so individual step tests don't
    need to call validate_workspace / validate_environment.
    """
    stage = MagicMock()
    stage.name = stage_name
    stage.provisioner = stage_provisioner
    stage.topology = stage_topology

    deployment_service = MagicMock()
    deployment_service.get_build_path.return_value = tmp_path / "build"

    workspace_service = MagicMock()
    workspace_service.model.spec.provisioners = provisioners or []
    workspace_service.model.spec.topology = topology or []
    deployment_service.get_workspace_service.return_value = workspace_service

    configuration_service = MagicMock()

    deployer = TerraformDeployer(
        stage=stage,
        deployment_service=deployment_service,
        configuration_service=configuration_service,
        build_path=tmp_path / "build",
        work_path=tmp_path / "work",
        verbose=verbose,
        force=force,
        resolved_values=resolved_values,
    )

    # Pre-wire the fields normally set by validate_workspace/validate_environment
    wd = working_dir or (tmp_path / "tf")
    wd.mkdir(parents=True, exist_ok=True)
    if tf_files:
        (wd / "main.tf").write_text("# placeholder")

    deployer._working_dir = wd
    deployer._plan_file = wd / f"{stage_name}.tfplan"
    deployer._tf = MagicMock()
    deployer._iac_model = MagicMock()
    deployer._iac_model.name = "default"

    return deployer


class TestTerraformDeployerMetadata:
    def test_deployer_name(self, tmp_path):
        d = _make_deployer(tmp_path)
        assert d.get_deployer_name() == "terraform"

    def test_supported_steps(self, tmp_path):
        d = _make_deployer(tmp_path)
        steps = d.get_supported_steps()
        for step in ("setup", "check", "plan", "apply", "destroy", "plan_destroy", "show_plan", "output"):
            assert step in steps


class TestTerraformDeployerValidateWorkspace:
    def test_no_workspace_service_returns_false(self, tmp_path):
        stage = MagicMock()
        stage.name = "prod"
        stage.provisioner = None
        stage.topology = None

        svc = MagicMock()
        svc.get_workspace_service.return_value = None

        d = TerraformDeployer(stage, svc, MagicMock(), tmp_path, tmp_path)
        ok, msgs = d.validate_workspace()
        assert ok is False
        assert any("Workspace service is not available" in m for m in msgs)

    def test_no_iac_model_resolved_returns_false(self, tmp_path):
        stage = MagicMock()
        stage.name = "prod"
        stage.provisioner = None
        stage.topology = None

        ws = MagicMock()
        ws.model.spec.provisioners = []  # no provisioners → can't resolve
        ws.model.spec.topology = []

        svc = MagicMock()
        svc.get_workspace_service.return_value = ws
        svc.get_build_path.return_value = tmp_path / "build"

        d = TerraformDeployer(stage, svc, MagicMock(), tmp_path, tmp_path)
        ok, msgs = d.validate_workspace()
        assert ok is False
        assert any("cannot resolve" in m for m in msgs)

    def test_working_dir_missing_returns_false(self, tmp_path):
        stage = MagicMock()
        stage.name = "prod"
        stage.provisioner = "my_tf"
        stage.topology = None

        provisioner = MagicMock()
        provisioner.name = "my_tf"
        provisioner.source.target_path = "iac/terraform"
        provisioner.backend = None

        ws = MagicMock()
        ws.model.spec.provisioners = [provisioner]
        ws.model.spec.topology = []

        svc = MagicMock()
        svc.get_workspace_service.return_value = ws
        svc.get_build_path.return_value = tmp_path / "build"

        d = TerraformDeployer(stage, svc, MagicMock(), tmp_path, tmp_path)
        ok, msgs = d.validate_workspace()
        assert ok is False
        assert any("does not exist" in m for m in msgs)

    def test_no_tf_files_returns_false(self, tmp_path):
        stage = MagicMock()
        stage.name = "prod"
        stage.provisioner = "my_tf"
        stage.topology = None

        provisioner = MagicMock()
        provisioner.name = "my_tf"
        provisioner.source.target_path = "iac/terraform"
        provisioner.backend = None

        ws = MagicMock()
        ws.model.spec.provisioners = [provisioner]
        ws.model.spec.topology = []

        svc = MagicMock()
        svc.get_workspace_service.return_value = ws
        build_path = tmp_path / "build"
        svc.get_build_path.return_value = build_path

        # Create dir but no .tf files
        (build_path / "iac" / "terraform").mkdir(parents=True)

        d = TerraformDeployer(stage, svc, MagicMock(), tmp_path, tmp_path)
        ok, msgs = d.validate_workspace()
        assert ok is False
        assert any("No *.tf files" in m for m in msgs)

    def test_valid_workspace_returns_true(self, tmp_path):
        stage = MagicMock()
        stage.name = "prod"
        stage.provisioner = "my_tf"
        stage.topology = None

        provisioner = MagicMock()
        provisioner.name = "my_tf"
        provisioner.source.target_path = "iac/terraform"
        provisioner.backend = None

        ws = MagicMock()
        ws.model.spec.provisioners = [provisioner]
        ws.model.spec.topology = []

        svc = MagicMock()
        svc.get_workspace_service.return_value = ws
        build_path = tmp_path / "build"
        svc.get_build_path.return_value = build_path

        tf_dir = build_path / "iac" / "terraform"
        tf_dir.mkdir(parents=True)
        (tf_dir / "main.tf").write_text("# tf")

        d = TerraformDeployer(stage, svc, MagicMock(), tmp_path, tmp_path)
        ok, msgs = d.validate_workspace()
        assert ok is True
        assert d._working_dir == tf_dir
        assert d._plan_file == tf_dir / "prod.tfplan"


class TestTerraformDeployerValidateEnvironment:
    def test_no_iac_model_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._iac_model = None  # simulate validate_workspace not called
        ok, msgs = d.validate_environment()
        assert ok is False
        assert any("validate_workspace" in m for m in msgs)

    def test_integration_not_available_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf = None  # clear the pre-wired mock

        mock_tf = MagicMock()
        mock_tf.is_available.return_value = False

        with patch.object(TerraformDeployer, "_get_terraform_integration", return_value=mock_tf):
            ok, msgs = d.validate_environment()

        assert ok is False
        assert any("not found" in m for m in msgs)

    def test_integration_available_returns_true(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf = None

        mock_tf = MagicMock()
        mock_tf.is_available.return_value = True

        with patch.object(TerraformDeployer, "_get_terraform_integration", return_value=mock_tf):
            ok, msgs = d.validate_environment()

        assert ok is True

    def test_runtime_error_from_registry_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf = None

        with patch.object(TerraformDeployer, "_get_terraform_integration", side_effect=RuntimeError("not registered")):
            ok, msgs = d.validate_environment()

        assert ok is False
        assert any("not registered" in m for m in msgs)


class TestTerraformDeployerReadyGuard:
    def test_no_working_dir_blocks_step(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._working_dir = None
        ok, msgs = d.setup()
        assert ok is False
        assert any("validate_workspace" in m for m in msgs)

    def test_no_tf_blocks_step(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf = None
        ok, msgs = d.setup()
        assert ok is False
        assert any("validate_environment" in m for m in msgs)


class TestTerraformDeployerSetup:
    def test_success(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf.init.return_value = _ok()
        ok, msgs = d.setup()
        assert ok is True
        d._tf.init.assert_called_once()

    def test_failure_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf.init.return_value = _fail("backend error")
        ok, msgs = d.setup()
        assert ok is False
        assert any("terraform init failed" in m for m in msgs)

    def test_runtime_error_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf.init.side_effect = RuntimeError("network")
        ok, msgs = d.setup()
        assert ok is False
        assert any("terraform init error" in m for m in msgs)

    def test_backend_config_passed_when_present(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf.init.return_value = _ok()
        d._iac_model.backend.configuration = {"key": "val"}

        with patch.object(d, "_build_backend_config", return_value={"key": "val"}):
            d.setup()

        call_kwargs = d._tf.init.call_args[1]
        assert call_kwargs.get("reconfigure") is True

    def test_verbose_stdout_added(self, tmp_path):
        d = _make_deployer(tmp_path, verbose=True)
        d._tf.init.return_value = _ok(stdout="Initializing...")
        ok, msgs = d.setup()
        assert "Initializing..." in msgs


class TestTerraformDeployerCheck:
    def test_success(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf.validate.return_value = _ok()
        ok, msgs = d.check()
        assert ok is True
        d._tf.validate.assert_called_once()

    def test_failure_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf.validate.return_value = _fail("invalid config")
        ok, msgs = d.check()
        assert ok is False
        assert any("terraform validate failed" in m for m in msgs)

    def test_runtime_error_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf.validate.side_effect = RuntimeError("crash")
        ok, msgs = d.check()
        assert ok is False


class TestTerraformDeployerPlan:
    def test_success(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf.plan.return_value = _ok()
        ok, msgs = d.plan()
        assert ok is True
        d._tf.plan.assert_called_once()

    def test_failure_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf.plan.return_value = _fail("plan error")
        ok, msgs = d.plan()
        assert ok is False
        assert any("terraform plan failed" in m for m in msgs)

    def test_runtime_error_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf.plan.side_effect = RuntimeError("crash")
        ok, msgs = d.plan()
        assert ok is False

    def test_plan_file_name_in_message(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf.plan.return_value = _ok()
        ok, msgs = d.plan()
        assert any("production.tfplan" in m for m in msgs)


class TestTerraformDeployerApply:
    def test_success_adds_message(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf.apply.return_value = _ok()
        ok, msgs = d.apply()
        assert ok is True
        assert any("applied successfully" in m for m in msgs)

    def test_failure_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf.apply.return_value = _fail("state lock")
        ok, msgs = d.apply()
        assert ok is False
        assert not any("applied successfully" in m for m in msgs)

    def test_runtime_error_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf.apply.side_effect = RuntimeError("crash")
        ok, msgs = d.apply()
        assert ok is False


class TestTerraformDeployerDestroy:
    def test_success_adds_message(self, tmp_path):
        d = _make_deployer(tmp_path, force=True)
        d._tf.destroy.return_value = _ok()
        ok, msgs = d.destroy()
        assert ok is True
        assert any("destroyed successfully" in m for m in msgs)

    def test_force_passes_auto_approve(self, tmp_path):
        d = _make_deployer(tmp_path, force=True)
        d._tf.destroy.return_value = _ok()
        d.destroy()
        call_kwargs = d._tf.destroy.call_args[1]
        assert call_kwargs.get("auto_approve") is True

    def test_no_force_passes_false(self, tmp_path):
        d = _make_deployer(tmp_path, force=False)
        d._tf.destroy.return_value = _ok()
        d.destroy()
        call_kwargs = d._tf.destroy.call_args[1]
        assert call_kwargs.get("auto_approve") is False

    def test_failure_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf.destroy.return_value = _fail("resource busy")
        ok, msgs = d.destroy()
        assert ok is False

    def test_runtime_error_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf.destroy.side_effect = RuntimeError("crash")
        ok, msgs = d.destroy()
        assert ok is False


class TestTerraformDeployerPlanDestroy:
    def test_success(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf.plan.return_value = _ok()
        ok, msgs = d.plan_destroy()
        assert ok is True
        call_kwargs = d._tf.plan.call_args[1]
        assert call_kwargs.get("destroy") is True

    def test_failure_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf.plan.return_value = _fail("destroy plan error")
        ok, msgs = d.plan_destroy()
        assert ok is False
        assert any("terraform plan -destroy failed" in m for m in msgs)


class TestTerraformDeployerOutput:
    def test_success_parses_values(self, tmp_path):
        raw = json.dumps({"vpc_id": {"value": "vpc-123", "type": "string"}})
        d = _make_deployer(tmp_path)
        d._tf.output.return_value = _ok(stdout=raw)
        ok, outputs, msgs = d.output()
        assert ok is True
        assert outputs == {"vpc_id": "vpc-123"}

    def test_empty_output(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf.output.return_value = _ok(stdout="{}")
        ok, outputs, msgs = d.output()
        assert ok is True
        assert outputs == {}

    def test_failure_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf.output.return_value = _fail("no state")
        ok, outputs, msgs = d.output()
        assert ok is False
        assert outputs == {}

    def test_invalid_json_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._tf.output.return_value = _ok(stdout="not json")
        ok, outputs, msgs = d.output()
        assert ok is False
        assert any("terraform output error" in m for m in msgs)


class TestTerraformDeployerShowPlan:
    def test_no_plan_file_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        # _plan_file is set but does not exist on disk
        ok, data, msgs = d.show_plan()
        assert ok is False
        assert any("No saved plan" in m for m in msgs)

    def test_success_returns_parsed_data(self, tmp_path):
        d = _make_deployer(tmp_path)
        plan_data = {"format_version": "1.0"}
        d._plan_file.write_text(json.dumps(plan_data))
        d._tf.show.return_value = _ok(stdout=json.dumps(plan_data))
        ok, data, msgs = d.show_plan()
        assert ok is True
        assert data["format_version"] == "1.0"

    def test_failure_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._plan_file.write_text("{}")
        d._tf.show.return_value = _fail("show error")
        ok, data, msgs = d.show_plan()
        assert ok is False
        assert any("terraform show failed" in m for m in msgs)

    def test_invalid_json_returns_false(self, tmp_path):
        d = _make_deployer(tmp_path)
        d._plan_file.write_text("{}")
        d._tf.show.return_value = _ok(stdout="not json")
        ok, data, msgs = d.show_plan()
        assert ok is False


class TestTerraformDeployerResolveIacModel:
    def _ws(self, provisioners, topology=None):
        ws = MagicMock()
        ws.model.spec.provisioners = provisioners
        ws.model.spec.topology = topology or []
        return ws

    def _prov(self, name, provisioner_type=None):
        p = MagicMock()
        p.name = name
        p.provisioner = provisioner_type
        return p

    def test_no_provisioners_returns_none(self, tmp_path):
        d = _make_deployer(tmp_path)
        stage = MagicMock()
        stage.provisioner = None
        stage.topology = None
        result = d._resolve_iac_model(stage, self._ws([]))
        assert result is None

    def test_explicit_provisioner_name_matched(self, tmp_path):
        d = _make_deployer(tmp_path)
        p = self._prov("my_tf")
        stage = MagicMock()
        stage.provisioner = "my_tf"
        stage.topology = None
        result = d._resolve_iac_model(stage, self._ws([p]))
        assert result is p

    def test_explicit_provisioner_name_not_found_falls_through(self, tmp_path):
        d = _make_deployer(tmp_path)
        p = self._prov("other_tf")
        stage = MagicMock()
        stage.provisioner = "missing"
        stage.topology = None
        # Single provisioner → falls to priority 3
        result = d._resolve_iac_model(stage, self._ws([p]))
        assert result is p  # priority 3 fallback

    def test_single_provisioner_fallback(self, tmp_path):
        d = _make_deployer(tmp_path)
        p = self._prov("sole")
        stage = MagicMock()
        stage.provisioner = None
        stage.topology = None
        result = d._resolve_iac_model(stage, self._ws([p]))
        assert result is p

    def test_multiple_provisioners_no_match_returns_none(self, tmp_path):
        d = _make_deployer(tmp_path)
        p1 = self._prov("a")
        p2 = self._prov("b")
        stage = MagicMock()
        stage.provisioner = None
        stage.topology = None
        result = d._resolve_iac_model(stage, self._ws([p1, p2]))
        assert result is None


class TestTerraformDeployerBuildBackendConfig:
    def test_no_backend_returns_none(self, tmp_path):
        d = _make_deployer(tmp_path)
        iac = MagicMock()
        iac.backend = None
        assert d._build_backend_config(iac) is None

    def test_empty_configuration_returns_none(self, tmp_path):
        d = _make_deployer(tmp_path)
        iac = MagicMock()
        iac.backend.configuration = {}
        result = d._build_backend_config(iac)
        assert result is None

    def test_configuration_converted_to_strings(self, tmp_path):
        d = _make_deployer(tmp_path)
        iac = MagicMock()
        iac.backend.configuration = {"bucket": "my-bucket", "key": 42}
        result = d._build_backend_config(iac)
        assert result == {"bucket": "my-bucket", "key": "42"}


class TestTerraformDeployerGetWorkingDir:
    def test_uses_target_path(self, tmp_path):
        d = _make_deployer(tmp_path)
        svc = MagicMock()
        svc.get_build_path.return_value = tmp_path / "build"
        iac = MagicMock()
        iac.source.target_path = "iac/terraform"
        result = d._get_working_dir(svc, tmp_path, iac)
        assert result == tmp_path / "build" / "iac" / "terraform"

    def test_falls_back_to_terraform_name(self, tmp_path):
        d = _make_deployer(tmp_path)
        svc = MagicMock()
        svc.get_build_path.return_value = tmp_path / "build"
        iac = MagicMock()
        iac.source.target_path = None
        iac.name = "my_prov"
        result = d._get_working_dir(svc, tmp_path, iac)
        assert result == tmp_path / "build" / "terraform" / "my_prov"
