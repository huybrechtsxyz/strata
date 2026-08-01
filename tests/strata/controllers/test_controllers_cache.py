#!/usr/bin/env python3
"""Unit tests for CacheController (ADR-0026)."""

from pathlib import Path

import pytest

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
