"""Regression guards: gating must never leak into non-deploy surfaces (ADR-0083 D11).

The unifying principle is that gating suppresses **making changes**, never
**observing state**. Each test below pins one surface that must keep seeing a
disabled stage:

* ``deploy destroy`` — gating it would strand infrastructure created before the
  flag was turned off (D3). Covered in ``test_commands_deploy.py``.
* ``build plan``     — a preview; hiding a stage would make "disabled" look like
  "deleted".
* drift detection    — drift against a disabled stage is precisely how orphaned
  infrastructure is discovered.

Without these, a future refactor that routes those commands through
``select_stages`` with gating on would regress silently, and the failure mode
(invisible orphaned resources) is one nobody notices until a bill or an audit.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from strata.commands.builders.plan_build_command import PlanBuildCommand
from strata.commands.deploy.drift_deploy_command import DriftDeployCommand
from strata.utils.resolved_values import ResolvedValues


def _stage(name: str, enabled=None) -> MagicMock:
    stage = MagicMock()
    stage.name = name
    stage.scope = None
    stage.enabled = enabled
    stage.depends_on = None
    return stage


def _service_with(stages) -> MagicMock:
    svc = MagicMock()
    svc.model.spec.stages = stages
    return svc


class TestBuildPlanIsNotGated:
    """`build plan` previews every stage, disabled or not."""

    def _command(self, tmp_path: Path, stages) -> PlanBuildCommand:
        cmd = PlanBuildCommand.__new__(PlanBuildCommand)
        cmd._deployment_service = _service_with(stages)
        cmd._errors = []
        cmd._stage = None
        cmd._work_path = tmp_path
        cmd._configuration_service = MagicMock()
        cmd._solution_controller = MagicMock()
        return cmd

    def test_disabled_stage_is_still_planned(self, tmp_path):
        core, api = _stage("core"), _stage("api", enabled=False)
        cmd = self._command(tmp_path, [core, api])

        with patch.object(PlanBuildCommand, "_plan_stage", return_value={"stage": "x"}) as plan_stage:
            cmd._run_terraform_plan(tmp_path, None)

        assert [c.args[0].name for c in plan_stage.call_args_list] == ["core", "api"]

    def test_expression_disabled_stage_is_still_planned(self, tmp_path):
        api = _stage("api", enabled="${feature:enable_api}")
        cmd = self._command(tmp_path, [api])
        resolved = ResolvedValues(features={"enable_api": False})

        with patch.object(PlanBuildCommand, "_plan_stage", return_value={"stage": "x"}) as plan_stage:
            cmd._run_terraform_plan(tmp_path, resolved)

        plan_stage.assert_called_once()


class TestBuildPlanMarksDisabledStages:
    """Planned, but clearly labelled — "disabled" must not read as "deleted"."""

    def _command(self, tmp_path: Path) -> PlanBuildCommand:
        cmd = PlanBuildCommand.__new__(PlanBuildCommand)
        cmd._deployment_service = MagicMock()
        cmd._errors = []
        cmd._work_path = tmp_path
        cmd._configuration_service = None  # short-circuits before terraform runs
        cmd._solution_controller = MagicMock()
        return cmd

    def test_enabled_stage_is_not_marked(self, tmp_path):
        cmd = self._command(tmp_path)

        result = cmd._plan_stage(_stage("core"), tmp_path, None)

        assert result["would_skip"] is False
        assert result["skip_reason"] is None

    def test_disabled_stage_is_marked_with_a_reason(self, tmp_path):
        cmd = self._command(tmp_path)

        result = cmd._plan_stage(_stage("api", enabled=False), tmp_path, None)

        assert result["would_skip"] is True
        assert result["skip_reason"] is not None
        assert any("would skip" in m for m in result["messages"])

    def test_marker_names_the_expression_and_resolved_value(self, tmp_path):
        cmd = self._command(tmp_path)
        resolved = ResolvedValues(features={"enable_api": False})

        result = cmd._plan_stage(_stage("api", enabled="${feature:enable_api}"), tmp_path, resolved)

        assert result["would_skip"] is True
        assert "${feature:enable_api}" in result["skip_reason"]


class TestDriftIsNotGated:
    """Drift against a disabled stage is how orphaned infrastructure is found."""

    def test_disabled_stage_is_still_drift_checked(self, tmp_path):
        core, api = _stage("core"), _stage("api", enabled=False)
        cmd = DriftDeployCommand.__new__(DriftDeployCommand)
        cmd._deployment_service = _service_with([core, api])
        cmd._configuration_service = MagicMock()
        cmd._errors = []
        cmd._messages = []
        cmd._stage = None
        cmd._work_path = tmp_path
        cmd._build_path = tmp_path / "build"
        cmd._solution_controller = MagicMock()
        cmd._output_format = "json"
        cmd._output_quiet = True
        cmd._output_verbose = False
        cmd._severity_threshold = MagicMock(value="low")

        with patch("strata.commands.deploy.drift_deploy_command.DriftController") as controller_cls:
            controller = controller_cls.return_value
            controller.get_errors.return_value = []
            controller.get_messages.return_value = []
            try:
                cmd._run_drift_detection()
            except Exception:
                # Rendering/persistence after detect_drift is out of scope here;
                # the assertion below is what this test exists for.
                pass

        assert controller.detect_drift.called, "drift detection must run"
        checked = controller.detect_drift.call_args.kwargs["stages"]
        assert [s.name for s in checked] == ["core", "api"]
