"""Unit tests for BicepBuilder (ADR-0071 — bicep previously had no builder-side copy step)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from strata.builders.bicep_builder import BicepBuilder
from strata.models.common_models import ProvisionerType, SourceModel
from strata.models.workspace_model import WorkspaceIacModel


def _make_provisioner(
    source_path: str, repository: str | None = None, reference: str | None = None
) -> WorkspaceIacModel:
    """Build a WorkspaceIacModel with a bicep provisioner for testing."""
    source = SourceModel(source_path=source_path, repository=repository, reference=reference)
    return WorkspaceIacModel(name="infrastructure", provisioner=ProvisionerType.BICEP, source=source)


def _make_deployment_svc(provisioner: WorkspaceIacModel, build_path: Path) -> MagicMock:
    """Return a mock DeploymentService that surfaces the given provisioner."""
    workspace_model = MagicMock()
    workspace_model.spec.provisioners = [provisioner]

    workspace_service = MagicMock()
    workspace_service.model = workspace_model

    deployment_svc = MagicMock()
    deployment_svc.get_workspace_service.return_value = workspace_service
    deployment_svc.get_build_path.return_value = build_path
    return deployment_svc


class TestBicepBuilderBeforeBuild:
    def test_fails_when_deployment_service_not_validated(self):
        builder = BicepBuilder()
        svc = MagicMock()
        svc.is_validated.return_value = False
        result = builder.before_build(deployment_service=svc, work_path=Path("."), build_path=Path("."))
        assert result is False
        assert any("not validated" in e for e in builder.get_errors())

    def test_fails_when_workspace_service_unavailable(self):
        builder = BicepBuilder()
        svc = MagicMock()
        svc.is_validated.return_value = True
        svc.get_workspace_service.return_value = None
        result = builder.before_build(deployment_service=svc, work_path=Path("."), build_path=Path("."))
        assert result is False
        assert any("Workspace service" in e for e in builder.get_errors())

    def test_passes_when_ready(self):
        builder = BicepBuilder()
        svc = MagicMock()
        svc.is_validated.return_value = True
        result = builder.before_build(deployment_service=svc, work_path=Path("."), build_path=Path("."))
        assert result is True


class TestBicepBuilderAfterBuild:
    def test_always_returns_true(self):
        builder = BicepBuilder()
        result = builder.after_build(deployment_service=MagicMock(), work_path=Path("."), build_path=Path("."))
        assert result is True


class TestCopyProvisionerSourceNoOp:
    def test_no_workspace_model_is_a_noop(self, tmp_path):
        builder = BicepBuilder()
        svc = MagicMock()
        workspace_service = MagicMock()
        workspace_service.model = None
        svc.get_workspace_service.return_value = workspace_service

        result = builder.build(deployment_service=svc, work_path=tmp_path, build_path=tmp_path, dry_run=True)
        assert result is True
        assert not builder.has_errors()

    def test_no_bicep_provisioners_is_a_noop(self, tmp_path):
        """Non-bicep provisioners in the workspace are skipped entirely."""
        build_path = tmp_path / "build"
        build_path.mkdir()

        other_prov = MagicMock()
        other_prov.provisioner = ProvisionerType.TERRAFORM

        workspace_model = MagicMock()
        workspace_model.spec.provisioners = [other_prov]
        workspace_service = MagicMock()
        workspace_service.model = workspace_model

        svc = MagicMock()
        svc.get_workspace_service.return_value = workspace_service
        svc.get_build_path.return_value = build_path

        builder = BicepBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder.build(deployment_service=svc, work_path=tmp_path, build_path=build_path, dry_run=True)

        assert result is True
        assert not builder.has_errors()


class TestCopyProvisionerSourceSingleRepo:
    """build() resolves to work_path when repository is absent, and to
    get_provisioner_path()/fallback otherwise (mirrors TerraformBuilder's tests)."""

    def test_no_repository_resolves_to_work_path(self, tmp_path):
        src_dir = tmp_path / "bicep"
        src_dir.mkdir()
        (src_dir / "main.bicep").write_text("resource x {}")
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner(source_path="bicep")
        depl_svc = _make_deployment_svc(prov, build_path)

        builder = BicepBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder.build(deployment_service=depl_svc, work_path=tmp_path, build_path=build_path)

        assert result is True
        assert not builder.has_errors()
        assert (build_path / "bicep" / "main.bicep").exists()

    def test_missing_source_path_returns_false(self, tmp_path):
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner(source_path="nonexistent")
        depl_svc = _make_deployment_svc(prov, build_path)

        builder = BicepBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder.build(deployment_service=depl_svc, work_path=tmp_path, build_path=build_path)

        assert result is False
        assert any("nonexistent" in e for e in builder.get_errors())

    def test_with_repository_uses_repo_map(self, tmp_path):
        repo_root = tmp_path / "my-repo"
        src_dir = repo_root / "bicep"
        src_dir.mkdir(parents=True)
        (src_dir / "main.bicep").write_text("resource x {}")
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner(source_path="bicep", repository="my_repo")
        depl_svc = _make_deployment_svc(prov, build_path)

        builder = BicepBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder.build(
                deployment_service=depl_svc,
                work_path=tmp_path,
                build_path=build_path,
                repo_map={"my_repo": str(repo_root)},
            )

        assert result is True
        assert (build_path / "bicep" / "main.bicep").exists()

    def test_get_provisioner_path_used_when_solution_controller_present(self, tmp_path):
        src_dir = tmp_path / "bicep"
        src_dir.mkdir()
        (src_dir / "main.bicep").write_text("resource x {}")
        build_path = tmp_path / "build"
        build_path.mkdir()
        dest_dir = tmp_path / "custom_dest"

        prov = _make_provisioner(source_path="bicep")
        depl_svc = _make_deployment_svc(prov, build_path)

        solution_controller = MagicMock()
        solution_controller.get_provisioner_path.return_value = dest_dir

        builder = BicepBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder.build(
                deployment_service=depl_svc,
                work_path=tmp_path,
                build_path=build_path,
                solution_controller=solution_controller,
            )

        assert result is True
        assert (dest_dir / "main.bicep").exists()
        solution_controller.get_provisioner_path.assert_called_once_with(depl_svc, build_path, prov)


class TestCopyProvisionerSourceRefPinning:
    """build() honors source.reference for ref-pinned extraction, same as Terraform."""

    def test_dry_run_with_reference_reports_ref(self, tmp_path):
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner(source_path="bicep", repository="my_repo", reference="v1.4.0")
        depl_svc = _make_deployment_svc(prov, build_path)

        repo_root = tmp_path / "my-repo"
        repo_root.mkdir()

        builder = BicepBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder.build(
                deployment_service=depl_svc,
                work_path=tmp_path,
                build_path=build_path,
                dry_run=True,
                repo_map={"my_repo": str(repo_root)},
            )

        assert result is True
        messages = "\n".join(builder.get_messages())
        assert "v1.4.0" in messages
        assert "DRY-RUN" in messages

    def test_no_reference_uses_standard_copy(self, tmp_path):
        src_dir = tmp_path / "bicep"
        src_dir.mkdir()
        (src_dir / "main.bicep").write_text("resource x {}")
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner(source_path="bicep", reference=None)
        depl_svc = _make_deployment_svc(prov, build_path)

        builder = BicepBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder.build(deployment_service=depl_svc, work_path=tmp_path, build_path=build_path)

        assert result is True
        assert (build_path / "bicep" / "main.bicep").exists()

    def test_reference_on_non_git_dir_falls_back_to_copy(self, tmp_path):
        repo_root = tmp_path / "my-repo"
        src_dir = repo_root / "bicep"
        src_dir.mkdir(parents=True)
        (src_dir / "main.bicep").write_text("resource x {}")
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner(source_path="bicep", repository="my_repo", reference="v1.0.0")
        depl_svc = _make_deployment_svc(prov, build_path)

        builder = BicepBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder.build(
                deployment_service=depl_svc,
                work_path=tmp_path,
                build_path=build_path,
                repo_map={"my_repo": str(repo_root)},
            )

        assert result is True
        assert (build_path / "bicep" / "main.bicep").exists()
        messages = "\n".join(builder.get_messages())
        assert "not a git repository" in messages
