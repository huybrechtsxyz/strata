#!/usr/bin/env python3
"""Unit tests for CacheController (ADR-0026)."""

from pathlib import Path

from strata.controllers.cache_controller import CacheController


def _repo_root() -> Path:
    return Path(__file__).parent.parent.parent.parent


class TestCacheControllerReadOnlyOperations:
    """Operations that don't require a fully configured workspace/profile."""

    def test_status_on_empty_cache_returns_no_entries(self, tmp_path: Path) -> None:
        controller = CacheController(tmp_path)
        ok, rows, errors = controller.status()
        assert ok is True
        assert rows == []
        assert errors == []

    def test_clear_on_empty_cache_succeeds(self, tmp_path: Path) -> None:
        controller = CacheController(tmp_path)
        ok, errors = controller.clear()
        assert ok is True
        assert errors == []

    def test_export_writes_empty_json(self, tmp_path: Path) -> None:
        controller = CacheController(tmp_path)
        out = tmp_path / "export.json"
        ok, errors = controller.export(str(out))
        assert ok is True
        assert out.exists()
        assert out.read_text(encoding="utf-8").strip() == "{}"

    def test_warm_then_status_round_trip(self, tmp_path: Path) -> None:
        controller = CacheController(tmp_path)
        controller.cache.warm(
            "demo",
            "deployment",
            "somekey",
            {"v": 1},
            [],
        )
        ok, rows, errors = controller.status()
        assert ok is True
        assert len(rows) == 1
        assert rows[0]["name"] == "demo"


class TestCacheControllerInputPathCollection:
    """Uses the repo's own test fixture deployment (Phase-1 load only, no profile needed)."""

    def test_collect_input_paths_includes_deployment_workspace_and_environment_files(self) -> None:
        repo_root = _repo_root()
        deployment_file = repo_root / "tests" / "data" / "deployments" / "deployment-standard.yaml"
        assert deployment_file.exists()

        controller = CacheController(repo_root)
        ok, deployment_service = controller._load_deployment(str(deployment_file))
        assert ok is True
        assert deployment_service is not None

        paths = controller._collect_input_paths(deployment_service)

        # deployment file + workspace file + at least one environment file
        assert len(paths) >= 3
        for p in paths:
            assert Path(p).exists(), f"collected input path does not exist: {p}"

    def test_collect_input_paths_includes_transitive_workspace_references(self) -> None:
        """Gap-closure: providers/resources/modules/namespaces/firewalls referenced
        directly by the workspace file must be included, not just deployment +
        workspace + environment files."""
        repo_root = _repo_root()
        deployment_file = repo_root / "tests" / "data" / "deployments" / "deployment-standard.yaml"

        controller = CacheController(repo_root)
        ok, deployment_service = controller._load_deployment(str(deployment_file))
        assert ok is True
        assert deployment_service is not None

        paths = controller._collect_input_paths(deployment_service)
        names = {Path(p).name for p in paths}

        assert "provider-standard.yaml" in names
        assert "resource-standard.yaml" in names
        assert "module-standard.yaml" in names
        assert "namespace-standard.yaml" in names
        assert "firewall-standard.yaml" in names

    def test_cache_key_changes_when_module_file_changes(self, tmp_path: Path) -> None:
        """Gap-closure: a transitively-referenced module file must affect the cache key."""
        repo_root = _repo_root()
        deployment_file = repo_root / "tests" / "data" / "deployments" / "deployment-standard.yaml"

        controller = CacheController(repo_root)
        ok, deployment_service = controller._load_deployment(str(deployment_file))
        assert ok is True
        assert deployment_service is not None

        paths = controller._collect_input_paths(deployment_service)
        module_path = Path(next(p for p in paths if "module-standard" in p))

        scratch = tmp_path / module_path.name
        original_bytes = module_path.read_bytes()
        scratch.write_bytes(original_bytes)
        scratch_paths = [p if Path(p) != module_path else str(scratch) for p in paths]

        key_before = controller.cache.compute_cache_key(scratch_paths)
        scratch.write_bytes(original_bytes + b"\n# cache-test-touch\n")
        key_after = controller.cache.compute_cache_key(scratch_paths)

        assert key_before != key_after

    def test_cache_key_changes_when_environment_file_changes(self, tmp_path: Path) -> None:
        repo_root = _repo_root()
        deployment_file = repo_root / "tests" / "data" / "deployments" / "deployment-standard.yaml"

        controller = CacheController(repo_root)
        ok, deployment_service = controller._load_deployment(str(deployment_file))
        assert ok is True
        assert deployment_service is not None

        paths = controller._collect_input_paths(deployment_service)
        env_path = Path(next(p for p in paths if "environment" in p))

        # Work on a temp copy so the shared test fixture is never mutated on disk
        # (write_text() can flip line endings, which would show up as a spurious
        # git diff on a committed fixture file).
        scratch = tmp_path / env_path.name
        original_bytes = env_path.read_bytes()
        scratch.write_bytes(original_bytes)

        scratch_paths = [p if Path(p) != env_path else str(scratch) for p in paths]
        key_before = controller.cache.compute_cache_key(scratch_paths)
        assert key_before is not None

        scratch.write_bytes(original_bytes + b"\n# cache-test-touch\n")
        key_after = controller.cache.compute_cache_key(scratch_paths)
        assert key_after != key_before


class TestCacheControllerSyncRemotes:
    """Gap-closure: _load_deployment_full replicates build run's remote checkout,
    gated by sync_remotes so background/ambient callers can opt out."""

    def _controller_with_mocked_config(self, tmp_path: Path, monkeypatch) -> CacheController:
        from unittest.mock import MagicMock

        controller = CacheController(tmp_path)
        fake_config_service = MagicMock()
        fake_config_service.model = None
        monkeypatch.setattr(controller, "_load_configuration_service", lambda: fake_config_service)
        monkeypatch.setattr(controller, "_load_deployment", lambda file_path: (True, MagicMock(path=file_path)))
        monkeypatch.setattr(controller._solution_controller, "get_repo_map", lambda: {})
        return controller

    def test_sync_remotes_true_calls_ensure_remote_refs(self, tmp_path: Path, monkeypatch) -> None:
        from unittest.mock import MagicMock, patch

        controller = self._controller_with_mocked_config(tmp_path, monkeypatch)
        deployment_service = MagicMock()
        deployment_service.validate.return_value = (True, [])
        deployment_service.load_deploy_services.return_value = True
        deployment_service.validate_related_services.return_value = (True, [])
        deployment_service.apply_environment_overrides.return_value = (True, [])
        monkeypatch.setattr(controller, "_load_deployment", lambda file_path: (True, deployment_service))

        with patch("strata.controllers.cache_controller.RepositoryController") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.ensure_remote_refs.return_value = (True, {})
            mock_repo_cls.return_value = mock_repo

            ok, result = controller._load_deployment_full("deploy.yaml", sync_remotes=True)

        assert ok is True
        assert result is deployment_service
        mock_repo.ensure_remote_refs.assert_called_once()

    def test_sync_remotes_false_skips_ensure_remote_refs(self, tmp_path: Path, monkeypatch) -> None:
        from unittest.mock import MagicMock, patch

        controller = self._controller_with_mocked_config(tmp_path, monkeypatch)
        deployment_service = MagicMock()
        deployment_service.validate.return_value = (True, [])
        deployment_service.load_deploy_services.return_value = True
        deployment_service.validate_related_services.return_value = (True, [])
        deployment_service.apply_environment_overrides.return_value = (True, [])
        monkeypatch.setattr(controller, "_load_deployment", lambda file_path: (True, deployment_service))

        with patch("strata.controllers.cache_controller.RepositoryController") as mock_repo_cls:
            ok, result = controller._load_deployment_full("deploy.yaml", sync_remotes=False)

        assert ok is True
        assert result is deployment_service
        mock_repo_cls.assert_not_called()

    def test_sync_remotes_failure_fails_the_resolve(self, tmp_path: Path, monkeypatch) -> None:
        from unittest.mock import MagicMock, patch

        controller = self._controller_with_mocked_config(tmp_path, monkeypatch)
        deployment_service = MagicMock()
        deployment_service.validate.return_value = (True, [])
        deployment_service.load_deploy_services.return_value = True
        deployment_service.validate_related_services.return_value = (True, [])
        deployment_service.apply_environment_overrides.return_value = (True, [])
        monkeypatch.setattr(controller, "_load_deployment", lambda file_path: (True, deployment_service))

        with patch("strata.controllers.cache_controller.RepositoryController") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.ensure_remote_refs.return_value = (False, {})
            mock_repo.get_errors.return_value = ["remote checkout failed"]
            mock_repo_cls.return_value = mock_repo

            ok, result = controller._load_deployment_full("deploy.yaml", sync_remotes=True)

        assert ok is False
        assert result is None
        assert "remote checkout failed" in controller.get_errors()
