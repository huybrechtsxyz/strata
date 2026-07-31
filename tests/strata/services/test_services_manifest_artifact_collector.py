"""Unit tests for src.strata.services.manifest_artifact_collector.

This is the shared implementation used by both `_collect_platform_artifact`,
`_collect_repository_info`, and `_collect_provider_info` on `BaseDeployCommand`
and `RunBuildCommand` (previously duplicated verbatim in both files — see
_lesson.md I1 / _todo.md).
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

from strata.models.common_models import PlatformName
from strata.services.manifest_artifact_collector import (
    collect_platform_artifact,
    collect_provider_info,
    collect_repository_info,
)

# ---------------------------------------------------------------------------
# collect_platform_artifact
# ---------------------------------------------------------------------------


class TestCollectPlatformArtifact:
    def test_hashes_platform_json(self, tmp_path: Path) -> None:
        build_path = tmp_path / "build"
        deployment_build_path = build_path / "test_deployment"
        deployment_build_path.mkdir(parents=True)
        platform_path = deployment_build_path / "platform.json"
        content = {"apiVersion": "strata.huybrechts.xyz/v1", "kind": "platform", "spec": {"name": "test"}}
        platform_path.write_text(json.dumps(content), encoding="utf-8")

        svc = MagicMock()
        svc.get_build_path.return_value = deployment_build_path

        result = collect_platform_artifact(svc, build_path, tmp_path)
        assert result.hash.startswith("sha256:")
        assert result.content == content
        assert result.path == str(platform_path.relative_to(tmp_path))

    def test_returns_unknown_when_deployment_service_is_none(self, tmp_path: Path) -> None:
        result = collect_platform_artifact(None, tmp_path / "build", tmp_path)
        assert result.hash == "unknown"
        assert result.content is None

    def test_returns_unknown_when_platform_json_missing(self, tmp_path: Path) -> None:
        build_path = tmp_path / "build"
        svc = MagicMock()
        svc.get_build_path.return_value = build_path  # platform.json never written

        result = collect_platform_artifact(svc, build_path, tmp_path)
        assert result.hash == "unknown"

    def test_returns_unknown_content_on_invalid_json(self, tmp_path: Path) -> None:
        build_path = tmp_path / "build"
        build_path.mkdir(parents=True)
        (build_path / "platform.json").write_bytes(b"{not valid json")

        svc = MagicMock()
        svc.get_build_path.return_value = build_path

        result = collect_platform_artifact(svc, build_path, tmp_path)
        assert result.hash.startswith("sha256:")
        assert result.content is None


# ---------------------------------------------------------------------------
# collect_repository_info
# ---------------------------------------------------------------------------


class TestCollectRepositoryInfo:
    def test_returns_none_without_solution_controller(self) -> None:
        assert collect_repository_info(None) is None

    def test_returns_none_when_solution_is_none(self) -> None:
        controller = MagicMock()
        controller.solution = None
        assert collect_repository_info(controller) is None

    def test_returns_none_without_repositories(self) -> None:
        controller = MagicMock()
        controller.solution.spec.repositories = []
        assert collect_repository_info(controller) is None

    def test_collects_url_and_ref_without_git_checkout(self) -> None:
        repo = MagicMock()
        repo.name = PlatformName("infra")
        repo.url = "git@github.com:org/infra.git"
        repo.ref = "main"

        controller = MagicMock()
        controller.solution.spec.repositories = [repo]
        controller.get_repo_map.return_value = {}

        result = collect_repository_info(controller)
        assert result is not None
        assert "infra" in result
        assert result["infra"].url == "git@github.com:org/infra.git"
        assert result["infra"].ref == "main"
        assert result["infra"].commit is None

    def test_resolves_commit_from_detached_head(self, tmp_path: Path) -> None:
        repo_path = tmp_path / "infra"
        git_dir = repo_path / ".git"
        git_dir.mkdir(parents=True)
        (git_dir / "HEAD").write_text("abc123deadbeef\n", encoding="utf-8")

        repo = MagicMock()
        repo.name = PlatformName("infra")
        repo.url = None
        repo.ref = None

        controller = MagicMock()
        controller.solution.spec.repositories = [repo]
        controller.get_repo_map.return_value = {"infra": str(repo_path)}

        result = collect_repository_info(controller)
        assert result is not None
        assert result["infra"].commit == "abc123deadbeef"

    def test_resolves_commit_from_symbolic_ref(self, tmp_path: Path) -> None:
        repo_path = tmp_path / "infra"
        git_dir = repo_path / ".git"
        (git_dir / "refs" / "heads").mkdir(parents=True)
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (git_dir / "refs" / "heads" / "main").write_text("deadbeef1234\n", encoding="utf-8")

        repo = MagicMock()
        repo.name = PlatformName("infra")
        repo.url = None
        repo.ref = None

        controller = MagicMock()
        controller.solution.spec.repositories = [repo]
        controller.get_repo_map.return_value = {"infra": str(repo_path)}

        result = collect_repository_info(controller)
        assert result is not None
        assert result["infra"].commit == "deadbeef1234"


# ---------------------------------------------------------------------------
# collect_provider_info
# ---------------------------------------------------------------------------


class TestCollectProviderInfo:
    def test_returns_none_without_deployment_service(self) -> None:
        assert collect_provider_info(None) is None

    def test_returns_none_without_workspace_service(self) -> None:
        svc = MagicMock()
        svc.get_workspace_service.return_value = None
        assert collect_provider_info(svc) is None

    def test_returns_none_without_provisioners(self) -> None:
        svc = MagicMock()
        workspace_svc = MagicMock()
        workspace_svc.model.spec.provisioners = []
        svc.get_workspace_service.return_value = workspace_svc
        assert collect_provider_info(svc) is None

    def test_collects_provisioner_metadata(self) -> None:
        prov = MagicMock()
        prov.name = PlatformName("infra")
        prov.provisioner = "terraform"
        prov.backend.type = "terraform_cloud"
        prov.backend.configuration = {"organization": "myorg"}
        prov.properties.model_dump.return_value = {"source_path": "terraform"}

        workspace_svc = MagicMock()
        workspace_svc.model.spec.provisioners = [prov]

        svc = MagicMock()
        svc.get_workspace_service.return_value = workspace_svc

        result = collect_provider_info(svc)
        assert result is not None
        assert len(result) == 1
        assert result[0].name == "infra"
        assert result[0].type == "terraform"
        assert result[0].backend == {"type": "terraform_cloud", "configuration": {"organization": "myorg"}}
        assert result[0].details == {"source_path": "terraform"}

    def test_no_backend_or_properties_yields_none_fields(self) -> None:
        prov = MagicMock()
        prov.name = PlatformName("infra")
        prov.provisioner = "compose"
        prov.backend = None
        prov.properties = None

        workspace_svc = MagicMock()
        workspace_svc.model.spec.provisioners = [prov]

        svc = MagicMock()
        svc.get_workspace_service.return_value = workspace_svc

        result = collect_provider_info(svc)
        assert result is not None
        assert result[0].backend is None
        assert result[0].details is None
