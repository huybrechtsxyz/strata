"""Tests for LifecycleController."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from strata.controllers.lifecycle_controller import LifecycleController
from strata.models.common_models import CommonLifecycleModel
from strata.utils.system import CommandResult

# ---------------------------------------------------------------------------
# Helpers: lightweight lifecycle model wrappers
# ---------------------------------------------------------------------------


class _DictRootLifecycle:
    """Non-CommonLifecycleModel lifecycle with a dict `.root` (hits elif branch)."""

    def __init__(self, phases: dict):
        self.root = phases


class _AttrLifecycle:
    """Non-CommonLifecycleModel lifecycle using attributes per phase (legacy branch)."""

    def __init__(self, **phases):
        for k, v in phases.items():
            setattr(self, k, v)


def _make_phase(script_names):
    """Return a MagicMock phase with `scripts` list of script-like objects."""
    scripts = []
    for name in script_names:
        s = MagicMock()
        s.file = name
        s.description = None
        scripts.append(s)
    phase = MagicMock()
    phase.scripts = scripts
    return phase


# ---------------------------------------------------------------------------
# execute_phase — no-op paths
# ---------------------------------------------------------------------------


class TestLifecycleControllerExecutePhaseNoop:
    def test_none_lifecycle_model_returns_true(self, tmp_path):
        ctrl = LifecycleController()
        result = ctrl.execute_phase("bootstrap", None, tmp_path)
        assert result is True

    def test_phase_not_in_lifecycle_returns_true(self, tmp_path):
        lifecycle = _DictRootLifecycle({})
        ctrl = LifecycleController()
        result = ctrl.execute_phase("missing_phase", lifecycle, tmp_path)
        assert result is True

    def test_phase_with_no_scripts_returns_true(self, tmp_path):
        phase = MagicMock()
        phase.scripts = []
        ctrl = LifecycleController()
        result = ctrl.execute_phase("bootstrap", None, tmp_path, phase_model=phase)
        assert result is True

    def test_phase_with_none_scripts_returns_true(self, tmp_path):
        phase = MagicMock()
        phase.scripts = None
        ctrl = LifecycleController()
        result = ctrl.execute_phase("bootstrap", None, tmp_path, phase_model=phase)
        assert result is True

    def test_direct_phase_model_none_returns_true(self, tmp_path):
        ctrl = LifecycleController()
        result = ctrl.execute_phase("bootstrap", None, tmp_path, phase_model=None)
        assert result is True


# ---------------------------------------------------------------------------
# execute_phase — script not found
# ---------------------------------------------------------------------------


class TestLifecycleControllerExecutePhaseScriptNotFound:
    def test_script_not_found_adds_error(self, tmp_path):
        phase = _make_phase(["nonexistent_script.sh"])
        ctrl = LifecycleController()
        result = ctrl.execute_phase("test_phase", None, tmp_path, phase_model=phase)
        assert result is False
        assert ctrl.has_errors()
        assert any("nonexistent_script.sh" in e for e in ctrl.get_errors())


# ---------------------------------------------------------------------------
# has_phase
# ---------------------------------------------------------------------------


class TestLifecycleControllerHasPhase:
    def test_has_phase_returns_false_for_none_model(self):
        ctrl = LifecycleController()
        assert ctrl.has_phase(None, "bootstrap") is False

    def test_has_phase_returns_true_dict_root_with_scripts(self):
        phase = _make_phase(["script.sh"])
        lifecycle = _DictRootLifecycle({"bootstrap": phase})
        ctrl = LifecycleController()
        assert ctrl.has_phase(lifecycle, "bootstrap") is True

    def test_has_phase_returns_false_dict_root_empty_scripts(self):
        phase = MagicMock()
        phase.scripts = []
        lifecycle = _DictRootLifecycle({"bootstrap": phase})
        ctrl = LifecycleController()
        assert ctrl.has_phase(lifecycle, "bootstrap") is False

    def test_has_phase_returns_false_phase_missing(self):
        lifecycle = _DictRootLifecycle({})
        ctrl = LifecycleController()
        assert ctrl.has_phase(lifecycle, "missing") is False

    def test_has_phase_attribute_model(self):
        phase = _make_phase(["script.sh"])
        lifecycle = _AttrLifecycle(my_phase=phase)
        ctrl = LifecycleController()
        assert ctrl.has_phase(lifecycle, "my_phase") is True

    def test_has_phase_attribute_model_missing(self):
        lifecycle = _AttrLifecycle(other_phase=_make_phase(["s.sh"]))
        ctrl = LifecycleController()
        assert ctrl.has_phase(lifecycle, "missing") is False

    def test_has_phase_real_common_model_empty(self):
        lifecycle = CommonLifecycleModel()
        ctrl = LifecycleController()
        assert ctrl.has_phase(lifecycle, "anything") is False


# ---------------------------------------------------------------------------
# get_phase_script_count
# ---------------------------------------------------------------------------


class TestLifecycleControllerGetPhaseScriptCount:
    def test_count_zero_when_no_phase(self):
        lifecycle = _DictRootLifecycle({})
        ctrl = LifecycleController()
        assert ctrl.get_phase_script_count(lifecycle, "missing") == 0

    def test_count_returns_number_of_scripts(self):
        phase = _make_phase(["a.sh", "b.sh", "c.sh"])
        lifecycle = _DictRootLifecycle({"deploy": phase})
        ctrl = LifecycleController()
        assert ctrl.get_phase_script_count(lifecycle, "deploy") == 3

    def test_count_zero_for_none_model(self):
        ctrl = LifecycleController()
        assert ctrl.get_phase_script_count(None, "phase") == 0


# ---------------------------------------------------------------------------
# _execute_script (low-level, subprocess mocked)
# ---------------------------------------------------------------------------


class TestLifecycleControllerExecuteScript:
    def test_execute_script_success(self, tmp_path):
        script_file = tmp_path / "test_script.sh"
        script_file.write_text("#!/bin/sh\necho hello\n", encoding="utf-8")

        ctrl = LifecycleController(enable_templating=False)
        mock_result = CommandResult(
            returncode=0, stdout="hello\n", stderr="", command="sh test_script.sh", duration_ms=0.0
        )
        with patch("strata.controllers.lifecycle_controller.run_command", return_value=mock_result):
            result = ctrl._execute_script(
                phase_name="test",
                script_file=Path("test_script.sh"),
                script_desc=None,
                work_path=tmp_path,
            )
        assert result is True
        assert not ctrl.has_errors()

    def test_execute_script_nonzero_exit_adds_error(self, tmp_path):
        script_file = tmp_path / "fail.sh"
        script_file.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")

        ctrl = LifecycleController(enable_templating=False)
        mock_result = CommandResult(
            returncode=1, stdout="", stderr="error output", command="sh fail.sh", duration_ms=0.0
        )
        with patch("strata.controllers.lifecycle_controller.run_command", return_value=mock_result):
            result = ctrl._execute_script(
                phase_name="test",
                script_file=Path("fail.sh"),
                script_desc=None,
                work_path=tmp_path,
            )
        assert result is False
        assert ctrl.has_errors()

    def test_execute_script_file_not_found_adds_error(self, tmp_path):
        ctrl = LifecycleController(enable_templating=False)
        result = ctrl._execute_script(
            phase_name="test",
            script_file=Path("no_such_file.sh"),
            script_desc=None,
            work_path=tmp_path,
        )
        assert result is False
        assert ctrl.has_errors()

    def test_strata_env_vars_injected(self, tmp_path: Path) -> None:
        """STRATA_PHASE and standard STRATA_* vars must be present in subprocess env."""
        script_file = tmp_path / "probe.sh"
        script_file.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")

        ctrl = LifecycleController(enable_templating=False)
        captured_env: dict = {}

        def _capture(cmd, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return CommandResult(
                returncode=0,
                stdout="",
                stderr="",
                command=" ".join(cmd) if isinstance(cmd, list) else cmd,
                duration_ms=0.0,
            )

        with patch("strata.controllers.lifecycle_controller.run_command", side_effect=_capture):
            ctrl._execute_script(
                phase_name="deploy_apply_before",
                script_file=Path("probe.sh"),
                script_desc=None,
                work_path=tmp_path,
                context={"stage": "infra", "dry_run": False},
            )

        assert captured_env.get("STRATA_PHASE") == "deploy_apply_before"
        assert captured_env.get("STRATA_WORKSPACE_PATH") == str(tmp_path)
        assert "STRATA_CONFIG_PATH" in captured_env
        assert "STRATA_BUILD_PATH" in captured_env
        assert "STRATA_OBJECT_PATH" in captured_env
        assert captured_env.get("STRATA_STAGE") == "infra"
        assert captured_env.get("STRATA_DRY_RUN") == "False"
        # Old XYZ_ prefix must NOT be present
        assert not any(k.startswith("XYZ_") for k in captured_env)
