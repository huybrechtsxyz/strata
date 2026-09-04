"""Unit tests for SyncBuilder path resolution (ADR-0071).

Focused, targeted coverage for the change made in this ADR item — this module had
zero prior test coverage. Not a full test suite for SyncBuilder's rendering logic.
"""

from unittest.mock import MagicMock

from strata.builders.sync_builder import SyncBuilder


def _make_stage(name: str = "gitops"):
    stage = MagicMock()
    stage.name = name
    stage.backend.integration = "argocd"
    return stage


def _make_integration(template: str = "manifest.yaml.j2", output_file: str = "manifest.yaml"):
    integration = MagicMock()
    integration.capabilities = {"sync"}
    integration.properties = {"template": template, "output_file": output_file}
    return integration


class TestSyncBuilderRenderStagePathResolution:
    """output_path resolution goes through SolutionController.get_sync_output_path()
    when a solution_controller is supplied, instead of independently re-deriving
    deployment_build_path/stage_name/output_rel (the same field SyncDeployer reads
    independently)."""

    def _setup(self, tmp_path):
        template_dir = tmp_path / ".strata" / "templates"
        template_dir.mkdir(parents=True)
        (template_dir / "manifest.yaml.j2").write_text("rendered: {{ value }}")

        builder = SyncBuilder()
        builder._find_integration = MagicMock(return_value=_make_integration())
        builder._build_context = MagicMock(return_value={"value": "x"})
        builder._render_template = MagicMock(return_value="rendered: x")
        return builder

    def test_uses_get_sync_output_path_when_solution_controller_present(self, tmp_path):
        builder = self._setup(tmp_path)
        stage = _make_stage()
        deployment_service = MagicMock()
        build_path = tmp_path / "build"
        custom_dest = tmp_path / "custom" / "gitops" / "manifest.yaml"

        solution_controller = MagicMock()
        solution_controller.get_sync_output_path.return_value = custom_dest

        ok = builder._render_stage(
            stage=stage,
            spec=MagicMock(),
            work_path=tmp_path,
            deployment_service=deployment_service,
            build_path=build_path,
            deployment_build_path=build_path,
            dry_run=False,
            solution_controller=solution_controller,
        )

        assert ok is True, builder.get_errors()
        assert custom_dest.exists()
        assert custom_dest.read_text(encoding="utf-8") == "rendered: x"
        solution_controller.get_sync_output_path.assert_called_once_with(
            deployment_service, build_path, "gitops", "manifest.yaml"
        )

    def test_falls_back_to_default_shape_when_no_solution_controller(self, tmp_path):
        builder = self._setup(tmp_path)
        stage = _make_stage()
        deployment_service = MagicMock()
        build_path = tmp_path / "build"

        ok = builder._render_stage(
            stage=stage,
            spec=MagicMock(),
            work_path=tmp_path,
            deployment_service=deployment_service,
            build_path=build_path,
            deployment_build_path=build_path,
            dry_run=False,
            solution_controller=None,
        )

        assert ok is True, builder.get_errors()
        assert (build_path / "gitops" / "manifest.yaml").read_text(encoding="utf-8") == "rendered: x"

    def test_dry_run_reports_planned_path_without_writing(self, tmp_path):
        builder = self._setup(tmp_path)
        stage = _make_stage()
        deployment_service = MagicMock()
        build_path = tmp_path / "build"

        ok = builder._render_stage(
            stage=stage,
            spec=MagicMock(),
            work_path=tmp_path,
            deployment_service=deployment_service,
            build_path=build_path,
            deployment_build_path=build_path,
            dry_run=True,
            solution_controller=None,
        )

        assert ok is True, builder.get_errors()
        assert not (build_path / "gitops" / "manifest.yaml").exists()
        assert any("DRY-RUN" in m for m in builder.get_messages())
