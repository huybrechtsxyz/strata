#!/usr/bin/env python3
"""Unit tests for the on-disk identity token cache (ADR-0067)."""

from strata.utils import identity_token_cache as cache


def test_save_and_load_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path / "identity")

    cache.save_token("my-idp", {"access_token": "abc", "expires_at": 123})

    loaded = cache.load_token("my-idp")
    assert loaded == {"access_token": "abc", "expires_at": 123}


def test_load_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path / "identity")

    assert cache.load_token("nonexistent") is None


def test_load_corrupt_file_returns_none(tmp_path, monkeypatch):
    cache_dir = tmp_path / "identity"
    monkeypatch.setattr(cache, "_CACHE_DIR", cache_dir)
    cache_dir.mkdir(parents=True)
    (cache_dir / "broken.json").write_text("not json", encoding="utf-8")

    assert cache.load_token("broken") is None


def test_clear_token_removes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path / "identity")

    cache.save_token("my-idp", {"access_token": "abc"})
    assert cache.load_token("my-idp") is not None

    cache.clear_token("my-idp")
    assert cache.load_token("my-idp") is None


def test_clear_missing_token_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path / "identity")

    cache.clear_token("never-existed")  # must not raise


def test_name_is_sanitized_for_filesystem(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path / "identity")

    cache.save_token("weird/name:here", {"access_token": "abc"})

    assert cache.load_token("weird/name:here") == {"access_token": "abc"}
