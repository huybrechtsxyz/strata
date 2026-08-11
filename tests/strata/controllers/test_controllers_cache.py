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

    def test_collect_input_paths_includes_deployment_configuration_files(self) -> None:
        """Gap-closure: deployment-level spec.configurations[].file must be included."""
        repo_root = _repo_root()
        deployment_file = repo_root / "tests" / "data" / "deployments" / "deployment-standard.yaml"

        controller = CacheController(repo_root)
        ok, deployment_service = controller._load_deployment(str(deployment_file))
        assert ok is True
        assert deployment_service is not None

        paths = controller._collect_input_paths(deployment_service)
        names = {Path(p).name for p in paths}

        assert "configuration-standard.yaml" in names

    def test_collect_input_paths_includes_tenant_file_when_present(self, tmp_path: Path) -> None:
        """Gap-closure: spec.tenant (tenants/<code>.yaml) must be included when set."""
        from unittest.mock import MagicMock

        tenants_dir = tmp_path / "tenants"
        tenants_dir.mkdir()
        tenant_file = tenants_dir / "acme.yaml"
        tenant_file.write_text("apiVersion: strata.huybrechts.xyz/v1\nkind: tenant\n", encoding="utf-8")

        controller = CacheController(tmp_path)
        deployment_service = MagicMock()
        deployment_service.path = str(tmp_path / "deploy.yaml")
        deployment_service.model.spec.workspace = None
        deployment_service.model.spec.environments = []
        deployment_service.model.spec.configurations = []
        deployment_service.model.spec.tenant = "acme"
        deployment_service._merged_repo_map.return_value = {}

        paths = controller._collect_input_paths(deployment_service)
        names = {Path(p).name for p in paths}

        assert "acme.yaml" in names

    def test_collect_input_paths_skips_tenant_file_when_missing_on_disk(self, tmp_path: Path) -> None:
        """A declared tenant whose file doesn't exist yet is skipped, not an error."""
        from unittest.mock import MagicMock

        controller = CacheController(tmp_path)
        deployment_service = MagicMock()
        deployment_service.path = str(tmp_path / "deploy.yaml")
        deployment_service.model.spec.workspace = None
        deployment_service.model.spec.environments = []
        deployment_service.model.spec.configurations = []
        deployment_service.model.spec.tenant = "does-not-exist"
        deployment_service._merged_repo_map.return_value = {}

        paths = controller._collect_input_paths(deployment_service)
        names = {Path(p).name for p in paths}

        assert "does-not-exist.yaml" not in names

    def test_collect_workspace_input_paths_recurses_into_namespace_modules(self) -> None:
        """Gap-closure: a namespace file's own spec.modules[].file must be included
        (the one real 'file referenced by a referenced file' case in the schema)."""
        repo_root = _repo_root()
        workspace_path = repo_root / "tests" / "data" / "workspaces" / "workspace-standard.yaml"

        controller = CacheController(repo_root)
        paths = controller._collect_workspace_input_paths(workspace_path, repo_map={})
        names = {Path(p).name for p in paths}

        # namespace-standard.yaml's own spec.modules[] references module-standard.yaml
        assert "module-standard.yaml" in names

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


def _fresh_validated_deployment(deployment_file: Path):
    """Build a standalone DeploymentService instance for *deployment_file*.

    Deliberately bypasses ``DeploymentService.load()``'s process-lifetime L1 cache
    (``strata.utils.service_cache``) so each test gets its own instance — the
    resolved-environment path mutates instance state (``_environment_service``),
    and a shared cached instance would leak that state across tests.
    """
    import yaml

    from strata.services.deployment_service import DeploymentService

    data = yaml.safe_load(deployment_file.read_text(encoding="utf-8"))
    ds = DeploymentService(path=str(deployment_file), data=data)
    ok, errors = ds.validate()
    assert ok, errors
    return ds


def _patched_config_service():
    from unittest.mock import MagicMock, patch

    fake_config = MagicMock()
    fake_config.get_remote_map.return_value = {}
    return patch("strata.services.deployment_service.ConfigurationService.get_instance", return_value=fake_config)


class TestCacheControllerResolvedEnvironment:
    """ADR-0026 Path B: the lighter ``resolved_environment`` cache kind.

    Unlike the ``deployment`` (build-artifact) kind, this never loads the
    workspace/providers/resources/modules — only the deployment + environment
    file(s). Uses an isolated ``CacheService`` (tmp_path-backed) so these tests
    never touch the repo's real ``.strata/cache/model/cache.db``.
    """

    def _controller(self, tmp_path: Path) -> CacheController:
        from strata.services.cache_service import CacheService

        repo_root = _repo_root()
        controller = CacheController(repo_root)
        # Redirect only the cache storage to an isolated tmp dir; keep repo_root as
        # the work_path so the fixture's relative file references still resolve.
        controller._cache = CacheService(tmp_path)
        return controller

    def test_collect_environment_input_paths_excludes_workspace(self, tmp_path: Path) -> None:
        deployment_file = _repo_root() / "tests" / "data" / "deployments" / "deployment-standard.yaml"
        controller = self._controller(tmp_path)
        deployment_service = _fresh_validated_deployment(deployment_file)

        paths = controller._collect_environment_input_paths(deployment_service)
        names = {Path(p).name for p in paths}

        assert "deployment-standard.yaml" in names
        assert "environment-standard.yaml" in names
        assert "workspace-standard.yaml" not in names
        assert "provider-standard.yaml" not in names

    def test_get_or_resolve_environment_cold_then_cached(self, tmp_path: Path) -> None:
        deployment_file = _repo_root() / "tests" / "data" / "deployments" / "deployment-standard.yaml"
        controller = self._controller(tmp_path)

        deployment_service = _fresh_validated_deployment(deployment_file)
        with _patched_config_service():
            ok, snapshot, indicator = controller.get_or_resolve_environment(deployment_service, {})
        assert ok is True
        assert indicator == "refreshed"
        assert snapshot is not None
        assert snapshot["deployment_name"] == "valid_platform"
        var_keys = {v["key"] for v in snapshot["environment"]["spec"]["variables"]}
        assert var_keys == {"WORKSPACE", "DATACENTER", "KAMATERA_MANAGER_ID"}
        # Cold path mutates the caller's deployment_service in place (matches a live
        # load_environment_only() call).
        assert deployment_service._environment_service is not None

        # Second call, fresh instance (simulating a new process) — should be a cache hit.
        deployment_service2 = _fresh_validated_deployment(deployment_file)
        with _patched_config_service():
            ok2, snapshot2, indicator2 = controller.get_or_resolve_environment(deployment_service2, {})
        assert ok2 is True
        assert indicator2 == "cached"
        assert snapshot2 == snapshot
        # Cache-hit path never calls load_environment_only() — deployment_service2
        # is left untouched; callers must use apply_environment_snapshot() themselves.
        assert deployment_service2._environment_service is None

    def test_apply_environment_snapshot_rehydrates_accessors(self, tmp_path: Path) -> None:
        deployment_file = _repo_root() / "tests" / "data" / "deployments" / "deployment-standard.yaml"
        controller = self._controller(tmp_path)

        warm_deployment_service = _fresh_validated_deployment(deployment_file)
        with _patched_config_service():
            ok, snapshot, _ = controller.get_or_resolve_environment(warm_deployment_service, {})
        assert ok is True
        assert snapshot is not None

        # Simulate a fresh process: a new deployment_service with no environment loaded.
        fresh_deployment_service = _fresh_validated_deployment(deployment_file)
        assert fresh_deployment_service._environment_service is None

        CacheController.apply_environment_snapshot(fresh_deployment_service, snapshot)

        env_service = fresh_deployment_service.get_environment_service()
        assert env_service is not None
        keys = {v.key for v in env_service.get_variables()}
        assert keys == {"WORKSPACE", "DATACENTER", "KAMATERA_MANAGER_ID"}

        provenance = fresh_deployment_service.get_merge_provenance()
        # Single environment file in this fixture — merge_envfiles() never ran, so
        # provenance is None (matches a live single-file load_environment_only() call).
        assert provenance is None

    def test_kind_scoping_does_not_collide_with_deployment_build_artifact_kind(self, tmp_path: Path) -> None:
        """Regression test for the surrogate-key schema fix: warming both cache
        kinds for the same deployment name must not collide."""
        deployment_file = _repo_root() / "tests" / "data" / "deployments" / "deployment-standard.yaml"
        controller = self._controller(tmp_path)

        deployment_service = _fresh_validated_deployment(deployment_file)
        controller.cache.warm(
            "valid_platform", "deployment", "buildkey", {"kind": "build-artifact"}, [str(deployment_file)]
        )
        with _patched_config_service():
            ok, snapshot, _ = controller.get_or_resolve_environment(deployment_service, {})
        assert ok is True

        assert controller.cache.get("valid_platform", "deployment", "buildkey") == {"kind": "build-artifact"}
        entries = {(e["name"], e["kind"]) for e in controller.status()[1]}
        assert ("valid_platform", "deployment") in entries
        assert ("valid_platform", "resolved_environment") in entries
