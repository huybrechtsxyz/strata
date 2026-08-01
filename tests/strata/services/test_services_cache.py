#!/usr/bin/env python3
"""Unit tests for CacheService (ADR-0026 resolved-model cache)."""

from pathlib import Path

import pytest

from strata.services.cache_service import CacheService, CacheStatus


@pytest.fixture
def work_path(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def input_file(tmp_path: Path) -> Path:
    f = tmp_path / "deployment.yaml"
    f.write_text("meta:\n  name: demo\n", encoding="utf-8")
    return f


class TestCacheServiceReadWrite:
    def test_cold_cache_returns_none(self, work_path: Path, input_file: Path) -> None:
        cache = CacheService(work_path)
        cache_key = cache.compute_cache_key([str(input_file)])
        assert cache_key is not None
        assert cache.get("demo", cache_key) is None
        assert cache.status("demo", cache_key) == CacheStatus.COLD

    def test_warm_then_get_returns_same_payload(self, work_path: Path, input_file: Path) -> None:
        cache = CacheService(work_path)
        cache_key = cache.compute_cache_key([str(input_file)])
        assert cache_key is not None

        resolved = {"meta": {"name": "demo"}, "spec": {"a": 1}}
        assert cache.warm("demo", "deployment", cache_key, resolved, [str(input_file)]) is True

        fetched = cache.get("demo", cache_key)
        assert fetched == resolved
        assert cache.status("demo", cache_key) == CacheStatus.FRESH

    def test_stale_when_input_file_changes(self, work_path: Path, input_file: Path) -> None:
        cache = CacheService(work_path)
        cache_key1 = cache.compute_cache_key([str(input_file)])
        assert cache_key1 is not None
        cache.warm("demo", "deployment", cache_key1, {"v": 1}, [str(input_file)])

        # Simulate an edit to the source file
        input_file.write_text("meta:\n  name: demo\n  extra: changed\n", encoding="utf-8")
        cache_key2 = cache.compute_cache_key([str(input_file)])
        assert cache_key2 is not None
        assert cache_key2 != cache_key1

        assert cache.get("demo", cache_key2) is None
        assert cache.status("demo", cache_key2) == CacheStatus.STALE
        # Old key still technically matches what's stored, but a caller would never
        # recompute the old key again once the file has changed.
        assert cache.get("demo", cache_key1) == {"v": 1}

    def test_compute_cache_key_returns_none_for_missing_file(self, work_path: Path) -> None:
        cache = CacheService(work_path)
        assert cache.compute_cache_key([str(work_path / "does-not-exist.yaml")]) is None

    def test_cache_key_order_independent(self, work_path: Path, tmp_path: Path) -> None:
        f1 = tmp_path / "a.yaml"
        f2 = tmp_path / "b.yaml"
        f1.write_text("a", encoding="utf-8")
        f2.write_text("b", encoding="utf-8")
        cache = CacheService(work_path)
        assert cache.compute_cache_key([str(f1), str(f2)]) == cache.compute_cache_key([str(f2), str(f1)])


class TestCacheServiceInvalidation:
    def test_invalidate_removes_entry(self, work_path: Path, input_file: Path) -> None:
        cache = CacheService(work_path)
        key = cache.compute_cache_key([str(input_file)])
        cache.warm("demo", "deployment", key, {"v": 1}, [str(input_file)])
        cache.invalidate("demo")
        assert cache.get("demo", key) is None

    def test_invalidate_all_clears_every_entry(self, work_path: Path, input_file: Path) -> None:
        cache = CacheService(work_path)
        key = cache.compute_cache_key([str(input_file)])
        cache.warm("a", "deployment", key, {"v": 1}, [str(input_file)])
        cache.warm("b", "deployment", key, {"v": 2}, [str(input_file)])
        cache.invalidate_all()
        assert cache.list_entries() == []

    def test_invalidate_by_path_prefix(self, work_path: Path, tmp_path: Path) -> None:
        remote_dir = tmp_path / "remote"
        remote_dir.mkdir()
        f = remote_dir / "env.yaml"
        f.write_text("x", encoding="utf-8")
        other = tmp_path / "local.yaml"
        other.write_text("y", encoding="utf-8")

        cache = CacheService(work_path)
        key_a = cache.compute_cache_key([str(f)])
        key_b = cache.compute_cache_key([str(other)])
        cache.warm("a", "deployment", key_a, {"v": 1}, [str(f)])
        cache.warm("b", "deployment", key_b, {"v": 2}, [str(other)])

        removed = cache.invalidate_by_path_prefix(str(remote_dir))
        assert removed == 1
        assert cache.get("a", key_a) is None
        assert cache.get("b", key_b) == {"v": 2}


class TestCacheServiceListingAndExport:
    def test_list_entries_metadata_only(self, work_path: Path, input_file: Path) -> None:
        cache = CacheService(work_path)
        key = cache.compute_cache_key([str(input_file)])
        cache.warm("demo", "deployment", key, {"v": 1}, [str(input_file)])

        entries = cache.list_entries()
        assert len(entries) == 1
        assert entries[0]["name"] == "demo"
        assert entries[0]["kind"] == "deployment"
        assert entries[0]["size_bytes"] > 0

    def test_export_json_round_trips_resolved_payload(self, work_path: Path, input_file: Path) -> None:
        cache = CacheService(work_path)
        key = cache.compute_cache_key([str(input_file)])
        resolved = {"meta": {"name": "demo"}}
        cache.warm("demo", "deployment", key, resolved, [str(input_file)])

        exported = cache.export_json()
        assert "demo" in exported
        assert exported["demo"]["resolved"] == resolved


class TestCacheServiceHighLevelApi:
    def test_get_or_resolve_cold_then_cached(self, work_path: Path, input_file: Path) -> None:
        cache = CacheService(work_path)
        calls = {"count": 0}

        def resolve_fn():
            calls["count"] += 1
            return {"v": calls["count"]}

        resolved1, indicator1 = cache.get_or_resolve("demo", "deployment", [str(input_file)], resolve_fn)
        assert indicator1 == "refreshed"
        assert resolved1 == {"v": 1}

        resolved2, indicator2 = cache.get_or_resolve("demo", "deployment", [str(input_file)], resolve_fn)
        assert indicator2 == "cached"
        assert resolved2 == {"v": 1}
        assert calls["count"] == 1  # resolve_fn not called again

    def test_get_or_resolve_no_cache_always_resolves_live(self, work_path: Path, input_file: Path) -> None:
        cache = CacheService(work_path)
        calls = {"count": 0}

        def resolve_fn():
            calls["count"] += 1
            return {"v": calls["count"]}

        cache.get_or_resolve("demo", "deployment", [str(input_file)], resolve_fn, no_cache=True)
        cache.get_or_resolve("demo", "deployment", [str(input_file)], resolve_fn, no_cache=True)
        assert calls["count"] == 2
        assert cache.list_entries() == []  # no-cache never writes

    def test_get_or_resolve_refresh_cache_forces_recompute(self, work_path: Path, input_file: Path) -> None:
        cache = CacheService(work_path)
        calls = {"count": 0}

        def resolve_fn():
            calls["count"] += 1
            return {"v": calls["count"]}

        cache.get_or_resolve("demo", "deployment", [str(input_file)], resolve_fn)
        resolved, indicator = cache.get_or_resolve("demo", "deployment", [str(input_file)], resolve_fn, refresh_cache=True)
        assert indicator == "refreshed"
        assert resolved == {"v": 2}
        assert calls["count"] == 2
