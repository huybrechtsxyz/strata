"""Tests for build manifest generation and the manifest CLI command group."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from strata.commands.builders.run_build_command import RunBuildCommand
from strata.commands.cli_manifest import manifest_group
from strata.models.common_models import PlatformName

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_build_command(tmp_path: Path, dry_run: bool = False) -> RunBuildCommand:
    """Create a RunBuildCommand with mocked context for testing."""
    cmd = RunBuildCommand.__new__(RunBuildCommand)
    cmd._work_path = tmp_path
    cmd._dry_run = dry_run
    cmd._build_path = tmp_path / "build"
    cmd._build_started_at = "2026-07-05T10:00:00+00:00"
    cmd._sbom_reference = None
    cmd._sbom_components = []
    cmd._policy_results = []
    cmd._errors = []
    cmd._messages = []
    cmd._output_data = {}
    cmd._file_path = Path("deploy/deploy-prd.yaml")
    cmd._output_format = "console"
    cmd._output_quiet = False
    cmd._deployment_service = None
    cmd._configuration_service = None
    cmd._solution_controller = None
    cmd.logger = MagicMock()
    return cmd


def _mock_deployment_service(tmp_path: Path) -> MagicMock:
    """Create a mocked deployment service that returns a valid build path."""
    svc = MagicMock()
    svc.model.meta.name = PlatformName("test_deployment")
    svc.model.meta.annotations = None
    svc.model.meta.labels = {"version": "1.0.0", "environment": "production"}
    svc.model.meta.tags = None

    workspace_svc = MagicMock()
    workspace_svc.model.meta.name = PlatformName("test_workspace")
    workspace_svc.model.spec.provisioners = []
    svc.get_workspace_service.return_value = workspace_svc

    build_path = tmp_path / "build" / "test_deployment"
    build_path.mkdir(parents=True, exist_ok=True)
    svc.get_build_path.return_value = build_path

    return svc


def _write_platform_json(build_path: Path) -> Path:
    """Write a minimal platform.json for testing."""
    platform_path = build_path / "platform.json"
    content = {"apiVersion": "strata.huybrechts.xyz/v1", "kind": "platform", "spec": {"name": "test"}}
    platform_path.write_text(json.dumps(content), encoding="utf-8")
    return platform_path


# ---------------------------------------------------------------------------
# Build manifest generation — _write_build_manifest
# ---------------------------------------------------------------------------


class TestWriteBuildManifest:
    def test_writes_manifest_json(self, tmp_path: Path) -> None:
        """Build manifest is written to the deployment build path."""
        cmd = _make_build_command(tmp_path)
        svc = _mock_deployment_service(tmp_path)
        cmd._deployment_service = svc

        build_path = svc.get_build_path.return_value
        _write_platform_json(build_path)

        path = cmd._write_build_manifest()
        assert path is not None
        assert path.name == "manifest.json"
        assert path.exists()

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["kind"] == "deployment-manifest"
        assert data["spec"]["action"] == "build"
        assert data["spec"]["status"] == "success"
        assert data["spec"]["deployment_name"] == "test_deployment"
        assert data["spec"]["workspace_name"] == "test_workspace"

    def test_skips_on_dry_run(self, tmp_path: Path) -> None:
        """Dry-run should not write a manifest."""
        cmd = _make_build_command(tmp_path, dry_run=True)
        result = cmd._write_build_manifest()
        assert result is None

    def test_skips_without_deployment_service(self, tmp_path: Path) -> None:
        """No deployment service means no manifest."""
        cmd = _make_build_command(tmp_path)
        cmd._deployment_service = None
        result = cmd._write_build_manifest()
        assert result is None

    def test_includes_platform_hash(self, tmp_path: Path) -> None:
        """Manifest should contain a SHA-256 hash of platform.json."""
        cmd = _make_build_command(tmp_path)
        svc = _mock_deployment_service(tmp_path)
        cmd._deployment_service = svc

        build_path = svc.get_build_path.return_value
        _write_platform_json(build_path)

        path = cmd._write_build_manifest()
        data = json.loads(path.read_text(encoding="utf-8"))
        platform_hash = data["spec"]["artifacts"]["platform"]["hash"]
        assert platform_hash.startswith("sha256:")
        assert len(platform_hash) > 10

    def test_includes_sbom_reference(self, tmp_path: Path) -> None:
        """SBOM reference is embedded when present."""
        from strata.models.sbom_model import SbomReferenceModel

        cmd = _make_build_command(tmp_path)
        svc = _mock_deployment_service(tmp_path)
        cmd._deployment_service = svc
        cmd._sbom_reference = SbomReferenceModel(
            path="build/test_deployment/sbom.json",
            format="cyclonedx-1.6",
            sha256="sha256:abc123",
            component_count=42,
        )

        build_path = svc.get_build_path.return_value
        _write_platform_json(build_path)

        path = cmd._write_build_manifest()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["spec"]["sbom"]["path"] == "build/test_deployment/sbom.json"
        assert data["spec"]["sbom"]["component_count"] == 42

    def test_includes_policy_results(self, tmp_path: Path) -> None:
        """Policy results are embedded in the manifest."""
        cmd = _make_build_command(tmp_path)
        svc = _mock_deployment_service(tmp_path)
        cmd._deployment_service = svc
        cmd._policy_results = [
            {
                "policy_name": "required_tags",
                "policy_type": "required_tags",
                "phase": "build",
                "enforcement": "deny",
                "passed": True,
                "violations": [],
            }
        ]

        build_path = svc.get_build_path.return_value
        _write_platform_json(build_path)

        path = cmd._write_build_manifest()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["spec"]["policy_results"]) == 1
        assert data["spec"]["policy_results"][0]["policy_name"] == "required_tags"
        assert data["spec"]["policy_results"][0]["passed"] is True

    def test_includes_environment_and_version(self, tmp_path: Path) -> None:
        """Environment and version from deployment labels are captured."""
        cmd = _make_build_command(tmp_path)
        svc = _mock_deployment_service(tmp_path)
        cmd._deployment_service = svc

        build_path = svc.get_build_path.return_value
        _write_platform_json(build_path)

        path = cmd._write_build_manifest()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["spec"]["environment"] == "production"
        assert data["meta"]["labels"]["version"] == "1.0.0"

    def test_includes_duration(self, tmp_path: Path) -> None:
        """Duration is computed from build start/end."""
        cmd = _make_build_command(tmp_path)
        svc = _mock_deployment_service(tmp_path)
        cmd._deployment_service = svc

        build_path = svc.get_build_path.return_value
        _write_platform_json(build_path)

        path = cmd._write_build_manifest()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "duration_seconds" in data["spec"]
        assert isinstance(data["spec"]["duration_seconds"], int)

    def test_stores_to_manifest_config_when_available(self, tmp_path: Path) -> None:
        """When manifest config is present, manifest is also saved via the service."""
        cmd = _make_build_command(tmp_path)
        svc = _mock_deployment_service(tmp_path)
        cmd._deployment_service = svc

        build_path = svc.get_build_path.return_value
        _write_platform_json(build_path)

        mock_config = MagicMock()
        mock_config.push_manifest = False
        cmd._configuration_service = MagicMock()
        cmd._configuration_service.model.spec.deployment.manifest = mock_config

        with patch(
            "strata.services.deployment_manifest_service.DeploymentManifestService.save_with_config"
        ) as mock_save:
            mock_save.return_value = tmp_path / "stored_manifest.json"
            path = cmd._write_build_manifest()

        assert path is not None
        mock_save.assert_called_once()

    def test_failure_does_not_raise(self, tmp_path: Path) -> None:
        """Build manifest write failure must not crash the build."""
        cmd = _make_build_command(tmp_path)
        svc = MagicMock()
        svc.model.meta.name = PlatformName("test")
        svc.model.meta.annotations = None
        svc.model.meta.labels = {}
        svc.model.meta.tags = None
        svc.get_workspace_service.return_value = None
        svc.get_build_path.side_effect = RuntimeError("disk full")
        cmd._deployment_service = svc

        # Must not raise
        result = cmd._write_build_manifest()
        assert result is None


# ---------------------------------------------------------------------------
# Build manifest — artifact collection
# ---------------------------------------------------------------------------


class TestCollectPlatformArtifact:
    def test_hashes_platform_json(self, tmp_path: Path) -> None:
        cmd = _make_build_command(tmp_path)
        svc = _mock_deployment_service(tmp_path)
        cmd._deployment_service = svc

        build_path = svc.get_build_path.return_value
        _write_platform_json(build_path)

        result = cmd._collect_platform_artifact()
        assert result.hash.startswith("sha256:")
        assert result.content is not None
        assert result.path is not None

    def test_returns_unknown_when_missing(self, tmp_path: Path) -> None:
        cmd = _make_build_command(tmp_path)
        svc = _mock_deployment_service(tmp_path)
        cmd._deployment_service = svc
        # Don't create platform.json

        result = cmd._collect_platform_artifact()
        assert result.hash == "unknown"


class TestCollectRepositoryInfo:
    def test_collects_repo_info(self, tmp_path: Path) -> None:
        cmd = _make_build_command(tmp_path)

        # Mock solution controller with a repo
        mock_solution = MagicMock()
        mock_repo = MagicMock()
        mock_repo.name = PlatformName("infra")
        mock_repo.url = "git@github.com:org/infra.git"
        mock_repo.ref = "main"
        mock_solution.spec.repositories = [mock_repo]

        cmd._solution_controller = MagicMock()
        cmd._solution_controller.solution = mock_solution
        cmd._solution_controller.get_repo_map.return_value = {}

        result = cmd._collect_repository_info()
        assert result is not None
        assert "infra" in result

    def test_returns_none_without_solution(self, tmp_path: Path) -> None:
        cmd = _make_build_command(tmp_path)
        cmd._solution_controller = None
        result = cmd._collect_repository_info()
        assert result is None


class TestCollectProviderInfo:
    def test_returns_none_without_provisioners(self, tmp_path: Path) -> None:
        cmd = _make_build_command(tmp_path)
        svc = _mock_deployment_service(tmp_path)
        cmd._deployment_service = svc

        result = cmd._collect_provider_info()
        assert result is None


# ---------------------------------------------------------------------------
# Manifest CLI — list
# ---------------------------------------------------------------------------


class TestManifestList:
    def test_list_empty(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(manifest_group, ["list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "No manifests" in result.output or "manifests" in result.output

    def test_list_json_empty(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(manifest_group, ["list", "--output", "json", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["data"]["manifests"] == []

    def test_list_finds_manifests(self, tmp_path: Path) -> None:
        """Should list manifests from the deployments directory."""
        deployments_dir = tmp_path / ".strata" / "deployments"
        deployments_dir.mkdir(parents=True)

        manifest = {
            "kind": "deployment-manifest",
            "spec": {
                "deployment_name": "prod",
                "action": "build",
                "status": "success",
                "started_at": "2026-07-05T10:00:00Z",
                "deployed_by": "alice",
            },
        }
        (deployments_dir / "prod_20260705T100000.json").write_text(json.dumps(manifest), encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(manifest_group, ["list", "--output", "json", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["data"]["manifests"]) == 1
        assert data["data"]["manifests"][0]["action"] == "build"

    def test_list_filters_by_deployment(self, tmp_path: Path) -> None:
        deployments_dir = tmp_path / ".strata" / "deployments"
        deployments_dir.mkdir(parents=True)

        for name in ["prod_20260705T100000.json", "staging_20260705T100000.json"]:
            (deployments_dir / name).write_text(
                json.dumps(
                    {
                        "spec": {
                            "deployment_name": name.split("_")[0],
                            "action": "build",
                            "status": "success",
                            "started_at": "",
                            "deployed_by": "",
                        }
                    }
                ),
                encoding="utf-8",
            )

        runner = CliRunner()
        result = runner.invoke(
            manifest_group, ["list", "--deployment", "prod", "--output", "json", "--work-path", str(tmp_path)]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["data"]["manifests"]) == 1


# ---------------------------------------------------------------------------
# Manifest CLI — show
# ---------------------------------------------------------------------------


class TestManifestShow:
    def test_show_json(self, tmp_path: Path) -> None:
        manifest = {
            "kind": "deployment-manifest",
            "meta": {"name": "prod"},
            "spec": {
                "deployment_name": "prod",
                "workspace_name": "ws",
                "action": "build",
                "status": "success",
                "started_at": "2026-07-05T10:00:00Z",
                "deployed_by": "alice",
                "artifacts": {"platform": {"hash": "sha256:abc"}},
            },
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(manifest_group, ["show", str(manifest_path), "--output", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["data"]["spec"]["action"] == "build"

    def test_show_console(self, tmp_path: Path) -> None:
        manifest = {
            "meta": {"name": "prod"},
            "spec": {
                "deployment_name": "prod",
                "workspace_name": "ws",
                "action": "deploy",
                "status": "success",
                "started_at": "2026-07-05T10:00:00Z",
                "deployed_by": "alice",
                "artifacts": {"platform": {"hash": "sha256:abc"}},
            },
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(manifest_group, ["show", str(manifest_path)])
        assert result.exit_code == 0
        assert "prod" in result.output
        assert "deploy" in result.output


# ---------------------------------------------------------------------------
# Manifest CLI — export
# ---------------------------------------------------------------------------


class TestManifestExport:
    def test_export_copies_manifests(self, tmp_path: Path) -> None:
        deployments_dir = tmp_path / ".strata" / "deployments"
        deployments_dir.mkdir(parents=True)

        manifest = {
            "kind": "deployment-manifest",
            "spec": {
                "deployment_name": "prod",
                "action": "build",
                "status": "success",
                "started_at": "2026-07-05T10:00:00Z",
            },
        }
        (deployments_dir / "prod_20260705T100000.json").write_text(json.dumps(manifest), encoding="utf-8")

        out_dir = tmp_path / "evidence"
        runner = CliRunner()
        result = runner.invoke(
            manifest_group,
            ["export", "--out", str(out_dir), "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0
        assert (out_dir / "manifests" / "prod_20260705T100000.json").exists()

    def test_export_json_output(self, tmp_path: Path) -> None:
        deployments_dir = tmp_path / ".strata" / "deployments"
        deployments_dir.mkdir(parents=True)

        (deployments_dir / "prod_20260705T100000.json").write_text(
            json.dumps({"spec": {"deployment_name": "prod"}}), encoding="utf-8"
        )

        out_dir = tmp_path / "evidence"
        runner = CliRunner()
        result = runner.invoke(
            manifest_group,
            ["export", "--out", str(out_dir), "--output", "json", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["data"]["manifest_count"] == 1

    def test_export_includes_sbom(self, tmp_path: Path) -> None:
        deployments_dir = tmp_path / ".strata" / "deployments"
        deployments_dir.mkdir(parents=True)

        sbom_path = tmp_path / "build" / "prod" / "sbom.json"
        sbom_path.parent.mkdir(parents=True)
        sbom_path.write_text('{"bomFormat": "CycloneDX"}', encoding="utf-8")

        manifest = {
            "spec": {
                "deployment_name": "prod",
                "sbom": {"path": "build/prod/sbom.json"},
            }
        }
        (deployments_dir / "prod_20260705T100000.json").write_text(json.dumps(manifest), encoding="utf-8")

        out_dir = tmp_path / "evidence"
        runner = CliRunner()
        result = runner.invoke(
            manifest_group,
            [
                "export",
                "--out",
                str(out_dir),
                "--include-sbom",
                "--work-path",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        assert (out_dir / "sbom" / "sbom.json").exists()


# ---------------------------------------------------------------------------
# Audit export — include-manifests flag
# ---------------------------------------------------------------------------


class TestAuditExportIncludeManifests:
    def test_include_manifests_flag_exists(self, tmp_path: Path) -> None:
        """The --include-manifests flag should be accepted."""
        from strata.commands.cli_audit import audit_group

        runner = CliRunner()
        result = runner.invoke(
            audit_group,
            ["export", "--include-manifests", "--work-path", str(tmp_path)],
        )
        # Should not fail with "no such option"
        assert "no such option" not in (result.output or "").lower()
