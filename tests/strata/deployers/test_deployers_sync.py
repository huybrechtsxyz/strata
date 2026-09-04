"""Unit tests for SyncDeployer path resolution (ADR-0071).

Focused, targeted coverage for the change made in this ADR item — this module had
zero prior test coverage. Not a full test suite for SyncDeployer's git/GitOps logic.
"""

from pathlib import Path
from unittest.mock import MagicMock

from strata.deployers.sync_deployer import ArgocdDeployer


def _make_deployer(tmp_path: Path, solution_controller=None) -> ArgocdDeployer:
    stage = MagicMock()
    stage.name = "gitops"
    stage.backend.integration = "argocd"
    stage.backend.remote = "gitops-repo"

    deployment_service = MagicMock()
    configuration_service = MagicMock()

    d = ArgocdDeployer(
        stage=stage,
        deployment_service=deployment_service,
        configuration_service=configuration_service,
        build_path=tmp_path / "build",
        work_path=tmp_path,
        solution_controller=solution_controller,
    )
    return d


def _make_integration(output_file: str = "manifest.yaml"):
    integration = MagicMock()
    integration.properties = {"output_file": output_file}
    return integration


class TestSyncDeployerValidateWorkspacePathResolution:
    """rendered_file resolution goes through
    SolutionController.get_sync_output_path() when a solution_controller is
    supplied, instead of independently re-deriving deployment_build_path/stage/
    output_file (the same field SyncBuilder reads independently)."""

    def test_uses_get_sync_output_path_when_solution_controller_present(self, tmp_path):
        build_path = tmp_path / "build"
        custom_dest = tmp_path / "custom" / "gitops" / "manifest.yaml"
        custom_dest.parent.mkdir(parents=True)
        custom_dest.write_text("rendered content")

        solution_controller = MagicMock()
        solution_controller.get_sync_output_path.return_value = custom_dest

        d = _make_deployer(tmp_path, solution_controller=solution_controller)
        d.deployment_service.get_build_path.return_value = build_path
        d._find_integration = MagicMock(return_value=_make_integration())

        ok, messages = d.validate_workspace()

        assert ok is True, messages
        assert d._rendered_file == custom_dest
        solution_controller.get_sync_output_path.assert_called_once_with(
            d.deployment_service, d.build_path, "gitops", "manifest.yaml"
        )

    def test_falls_back_to_default_shape_when_no_solution_controller(self, tmp_path):
        build_path = tmp_path / "build"
        default_dest = build_path / "gitops" / "manifest.yaml"
        default_dest.parent.mkdir(parents=True)
        default_dest.write_text("rendered content")

        d = _make_deployer(tmp_path, solution_controller=None)
        d.deployment_service.get_build_path.return_value = build_path
        d._find_integration = MagicMock(return_value=_make_integration())

        ok, messages = d.validate_workspace()

        assert ok is True, messages
        assert d._rendered_file == default_dest

    def test_missing_rendered_file_reports_error(self, tmp_path):
        build_path = tmp_path / "build"
        d = _make_deployer(tmp_path, solution_controller=None)
        d.deployment_service.get_build_path.return_value = build_path
        d._find_integration = MagicMock(return_value=_make_integration())

        ok, messages = d.validate_workspace()

        assert ok is False
        assert any("not found" in m for m in messages)
