"""Unit tests for ScriptDeployer."""

import subprocess
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

from strata.deployers.script_deployer import _STEP_TO_PHASE, ScriptDeployer


def _make_deployer(
    lifecycle_root: Optional[dict] = None,
    has_lifecycle: bool = True,
    model_loaded: bool = True,
    verbose: bool = False,
    tmp_path: Optional[Path] = None,
) -> ScriptDeployer:
    """Build a ScriptDeployer backed by a MagicMock deployment service."""
    stage = MagicMock()
    stage.name = "production"

    model: MagicMock = MagicMock()

    if model_loaded:
        if has_lifecycle:
            lifecycle = MagicMock()
            lifecycle.root = lifecycle_root or {}
            model.spec.lifecycle = lifecycle
        else:
            model.spec.lifecycle = None

    deployment_service = MagicMock()
    deployment_service.model = model if model_loaded else None

    configuration_service = MagicMock()
    work_path = tmp_path or Path("/work")
    build_path = tmp_path or Path("/build")

    return ScriptDeployer(
        stage=stage,
        deployment_service=deployment_service,
        configuration_service=configuration_service,
        build_path=build_path,
        work_path=work_path,
        verbose=verbose,
    )


def _make_phase(scripts=None):
    """Return a mock lifecycle phase with an optional scripts list."""
    phase = MagicMock()
    phase.scripts = scripts
    return phase


class TestScriptDeployerMetadata:
    def test_deployer_name(self):
        d = _make_deployer()
        assert d.get_deployer_name() == "script"

    def test_supported_steps(self):
        d = _make_deployer()
        steps = d.get_supported_steps()
        assert "setup" in steps
        assert "check" in steps
        assert "plan" in steps
        assert "apply" in steps
        assert "destroy" in steps
        assert "plan_destroy" in steps
        assert "show_plan" in steps
        assert "output" in steps


class TestScriptDeployerValidateWorkspace:
    def test_no_model_returns_false(self):
        d = _make_deployer(model_loaded=False)
        ok, msgs = d.validate_workspace()
        assert ok is False
        assert any("not loaded" in m for m in msgs)

    def test_no_lifecycle_returns_false(self):
        d = _make_deployer(has_lifecycle=False)
        ok, msgs = d.validate_workspace()
        assert ok is False
        assert any("no lifecycle" in m for m in msgs)

    def test_lifecycle_present_returns_true(self):
        d = _make_deployer(lifecycle_root={"deploy_setup": _make_phase()})
        ok, msgs = d.validate_workspace()
        assert ok is True
        assert not any("error" in m.lower() for m in msgs)

    def test_verbose_lists_phases(self):
        d = _make_deployer(lifecycle_root={"deploy_setup": _make_phase(), "deploy_apply": _make_phase()}, verbose=True)
        ok, msgs = d.validate_workspace()
        assert ok is True
        assert any("deploy_setup" in m for m in msgs)

    def test_empty_lifecycle_root_still_passes(self):
        d = _make_deployer(lifecycle_root={})
        ok, msgs = d.validate_workspace()
        assert ok is True


class TestScriptDeployerValidateEnvironment:
    def test_always_returns_true(self):
        d = _make_deployer()
        ok, msgs = d.validate_environment()
        assert ok is True
        assert msgs == []


class TestScriptDeployerShowPlan:
    def test_show_plan_returns_true_empty_dict(self):
        d = _make_deployer()
        ok, data, msgs = d.show_plan()
        assert ok is True
        assert data == {}
        assert len(msgs) > 0


class TestScriptDeployerOutput:
    def test_output_no_phase_returns_true_empty_dict(self):
        d = _make_deployer(lifecycle_root={})
        ok, data, msgs = d.output()
        assert ok is True
        assert data == {}

    def test_output_with_phase_executes_scripts(self, tmp_path):
        script = tmp_path / "out.sh"
        script.write_text("#!/bin/bash\necho done")
        phase = _make_phase(scripts=[str(script)])
        d = _make_deployer(lifecycle_root={"deploy_output": phase}, tmp_path=tmp_path)

        with patch.object(d, "_execute_script", return_value=(True, ["ok"])):
            ok, data, msgs = d.output()

        assert ok is True
        assert data == {}


class TestScriptDeployerApplyDestroySuffix:
    def test_apply_adds_success_message_on_success(self, tmp_path):
        phase = _make_phase(scripts=["script.sh"])
        d = _make_deployer(lifecycle_root={"deploy_apply": phase}, tmp_path=tmp_path)
        with patch.object(d, "_execute_script", return_value=(True, [])):
            ok, msgs = d.apply()
        assert ok is True
        assert any("applied successfully" in m for m in msgs)

    def test_apply_no_success_message_on_failure(self, tmp_path):
        phase = _make_phase(scripts=["script.sh"])
        d = _make_deployer(lifecycle_root={"deploy_apply": phase}, tmp_path=tmp_path)
        with patch.object(d, "_execute_script", return_value=(False, ["boom"])):
            ok, msgs = d.apply()
        assert ok is False
        assert not any("applied successfully" in m for m in msgs)

    def test_destroy_adds_success_message_on_success(self, tmp_path):
        phase = _make_phase(scripts=["script.sh"])
        d = _make_deployer(lifecycle_root={"deploy_destroy": phase}, tmp_path=tmp_path)
        with patch.object(d, "_execute_script", return_value=(True, [])):
            ok, msgs = d.destroy()
        assert ok is True
        assert any("destroyed successfully" in m for m in msgs)


class TestScriptDeployerRunPhase:
    def test_missing_phase_returns_true(self):
        d = _make_deployer(lifecycle_root={})
        ok, msgs = d._run_phase("setup")
        assert ok is True

    def test_no_lifecycle_returns_true(self):
        d = _make_deployer(has_lifecycle=False)
        ok, msgs = d._run_phase("setup")
        assert ok is True

    def test_phase_with_scripts_calls_execute_script(self, tmp_path):
        script = tmp_path / "setup.sh"
        script.write_text("#!/bin/bash")
        phase = _make_phase(scripts=[str(script)])
        d = _make_deployer(lifecycle_root={"deploy_setup": phase}, tmp_path=tmp_path)

        with patch.object(d, "_execute_script", return_value=(True, ["ran"])) as mock_exec:
            ok, msgs = d._run_phase("setup")

        assert ok is True
        mock_exec.assert_called_once()

    def test_script_failure_aborts_phase(self, tmp_path):
        scripts = ["a.sh", "b.sh"]
        phase = _make_phase(scripts=scripts)
        d = _make_deployer(lifecycle_root={"deploy_setup": phase}, tmp_path=tmp_path)

        call_count = 0

        def side_effect(path):
            nonlocal call_count
            call_count += 1
            return (False, ["failed"])

        with patch.object(d, "_execute_script", side_effect=side_effect):
            ok, msgs = d._run_phase("setup")

        assert ok is False
        assert call_count == 1  # stops after first failure

    def test_phase_with_no_scripts_returns_true(self):
        phase = _make_phase(scripts=None)
        d = _make_deployer(lifecycle_root={"deploy_setup": phase})
        ok, msgs = d._run_phase("setup")
        assert ok is True

    def test_verbose_logs_phase_skip(self):
        d = _make_deployer(lifecycle_root={}, verbose=True)
        ok, msgs = d._run_phase("setup")
        assert ok is True
        assert any("not defined" in m for m in msgs)

    def test_verbose_logs_no_scripts_skip(self):
        phase = _make_phase(scripts=None)
        d = _make_deployer(lifecycle_root={"deploy_setup": phase}, verbose=True)
        ok, msgs = d._run_phase("setup")
        assert ok is True
        assert any("No scripts" in m for m in msgs)

    def test_scriptpathmodel_entry_uses_file_attribute(self, tmp_path):
        """ScriptPathModel entries have .file, not a plain string."""
        script = tmp_path / "deploy.sh"
        script.write_text("#!/bin/bash")

        script_entry = MagicMock(spec=[])  # not a str
        script_entry.file = str(script)

        phase = _make_phase(scripts=[script_entry])
        d = _make_deployer(lifecycle_root={"deploy_setup": phase}, tmp_path=tmp_path)

        with patch.object(d, "_execute_script", return_value=(True, [])) as mock_exec:
            d._run_phase("setup")

        called_path = mock_exec.call_args[0][0]
        assert called_path == script


class TestScriptDeployerExecuteScript:
    def test_unsupported_extension_returns_false(self, tmp_path):
        script = tmp_path / "run.rb"
        script.write_text("puts 'hello'")
        d = _make_deployer(tmp_path=tmp_path)
        ok, msgs = d._execute_script(script)
        assert ok is False
        assert any("Unsupported script type" in m for m in msgs)

    def test_bash_script_success(self, tmp_path):
        script = tmp_path / "run.sh"
        script.write_text("#!/bin/bash\nexit 0")
        d = _make_deployer(tmp_path=tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            ok, msgs = d._execute_script(script)

        assert ok is True
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "bash"

    def test_python_script_success(self, tmp_path):
        script = tmp_path / "run.py"
        script.write_text("print('hi')")
        d = _make_deployer(tmp_path=tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            ok, msgs = d._execute_script(script)

        assert ok is True
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "python"

    def test_ps1_script_success(self, tmp_path):
        script = tmp_path / "run.ps1"
        script.write_text("Write-Host hi")
        d = _make_deployer(tmp_path=tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            ok, msgs = d._execute_script(script)

        assert ok is True
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "pwsh"

    def test_nonzero_exit_returns_false(self, tmp_path):
        script = tmp_path / "fail.sh"
        script.write_text("exit 1")
        d = _make_deployer(tmp_path=tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "something broke"

        with patch("subprocess.run", return_value=mock_result):
            ok, msgs = d._execute_script(script)

        assert ok is False
        assert any("1" in m for m in msgs)  # exit code in message
        assert any("something broke" in m for m in msgs)

    def test_timeout_returns_false(self, tmp_path):
        script = tmp_path / "slow.sh"
        script.write_text("sleep 999")
        d = _make_deployer(tmp_path=tmp_path)

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="bash", timeout=300)):
            ok, msgs = d._execute_script(script)

        assert ok is False
        assert any("timed out" in m for m in msgs)

    def test_file_not_found_returns_false(self, tmp_path):
        script = tmp_path / "missing.sh"
        script.write_text("")
        d = _make_deployer(tmp_path=tmp_path)

        with patch("subprocess.run", side_effect=FileNotFoundError):
            ok, msgs = d._execute_script(script)

        assert ok is False
        assert any("Interpreter not found" in m for m in msgs)

    def test_env_vars_injected(self, tmp_path):
        script = tmp_path / "env.sh"
        script.write_text("#!/bin/bash")
        d = _make_deployer(tmp_path=tmp_path)
        d.work_path = Path("/my/work")
        d.build_path = Path("/my/build")
        d.stage.name = "staging"

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            d._execute_script(script)

        env = mock_run.call_args[1]["env"]
        assert env["WORK_PATH"] == str(Path("/my/work"))
        assert env["BUILD_PATH"] == str(Path("/my/build"))
        assert env["STAGE_NAME"] == "staging"

    def test_verbose_stdout_added_to_messages(self, tmp_path):
        script = tmp_path / "out.sh"
        script.write_text("#!/bin/bash\necho hello")
        d = _make_deployer(tmp_path=tmp_path, verbose=True)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "hello"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            ok, msgs = d._execute_script(script)

        assert ok is True
        assert "hello" in msgs


class TestStepToPhaseMapping:
    def test_all_mapped_steps_have_phase(self):
        for step in ("setup", "check", "plan", "apply", "destroy", "plan_destroy", "output"):
            assert step in _STEP_TO_PHASE, f"Step '{step}' not in _STEP_TO_PHASE"

    def test_show_plan_not_in_mapping(self):
        # show_plan has no lifecycle equivalent — handled directly
        assert "show_plan" not in _STEP_TO_PHASE
