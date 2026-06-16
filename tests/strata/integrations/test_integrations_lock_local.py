"""Unit tests for LocalLockBackend."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from strata.integrations.lock.base_lock_backend import (
    LockBackendError,
    LockEntry,
    LockHandle,
    LockTimeoutError,
)
from strata.integrations.lock.lock_local import LocalLockBackend, _dict_to_entry, _entry_to_dict

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend(tmp_path: Path) -> LocalLockBackend:
    return LocalLockBackend(tmp_path)


def _make_entry(**kwargs) -> LockEntry:
    defaults = dict(
        lock_id="test-lock-id",
        deployment="prod-platform",
        holder="alice@company.com",
        hostname="WORKSTATION-A",
        pid=12345,
        acquired_at="2026-06-16T14:02:01Z",
        expires_at="2026-06-16T22:02:01Z",
        reason="strata deploy run",
    )
    defaults.update(kwargs)
    return LockEntry(**defaults)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


class TestSerializationHelpers:
    def test_entry_roundtrip(self):
        entry = _make_entry()
        assert _dict_to_entry(_entry_to_dict(entry)) == entry

    def test_stage_preserved(self):
        entry = _make_entry(stage="provision")
        assert _dict_to_entry(_entry_to_dict(entry)).stage == "provision"


# ---------------------------------------------------------------------------
# acquire + release — happy path (mocked OS lock)
# ---------------------------------------------------------------------------


class TestAcquireRelease:
    def test_acquire_returns_handle(self, tmp_path):
        backend = _make_backend(tmp_path)
        with patch.object(LocalLockBackend, "_os_lock", return_value=True):
            handle = backend.acquire("prod", "alice", "deploy run", 30)
        assert handle.backend_type == "local"
        assert handle.lock_id
        assert "fp" in handle._backend_data

    def test_acquire_creates_lock_file(self, tmp_path):
        backend = _make_backend(tmp_path)
        with patch.object(LocalLockBackend, "_os_lock", return_value=True):
            handle = backend.acquire("prod", "alice", "deploy run", 30)
        lock_path = tmp_path / ".strata" / "locks" / "prod.lock"
        assert lock_path.exists()

    def test_acquire_writes_entry_json(self, tmp_path):
        backend = _make_backend(tmp_path)
        with patch.object(LocalLockBackend, "_os_lock", return_value=True):
            handle = backend.acquire("prod", "alice", "deploy run", 30)
        lock_path = tmp_path / ".strata" / "locks" / "prod.lock"
        data = json.loads(lock_path.read_bytes())
        assert data["holder"] == "alice"
        assert data["deployment"] == "prod"
        assert data["lock_id"] == handle.lock_id

    def test_acquire_appends_history(self, tmp_path):
        backend = _make_backend(tmp_path)
        with patch.object(LocalLockBackend, "_os_lock", return_value=True):
            backend.acquire("prod", "alice", "deploy run", 30)
        history_path = tmp_path / ".strata" / "locks" / "prod.lock.history"
        assert history_path.exists()
        lines = [ln for ln in history_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["holder"] == "alice"

    def test_release_removes_lock_file(self, tmp_path):
        backend = _make_backend(tmp_path)
        with patch.object(LocalLockBackend, "_os_lock", return_value=True):
            handle = backend.acquire("prod", "alice", "deploy run", 30)
        lock_path = tmp_path / ".strata" / "locks" / "prod.lock"
        assert lock_path.exists()
        backend.release(handle)
        assert not lock_path.exists()

    def test_release_is_safe_when_file_already_gone(self, tmp_path):
        backend = _make_backend(tmp_path)
        handle = LockHandle(
            lock_id="x",
            backend_type="local",
            acquired_at="2026-06-16T14:00:00Z",
            _backend_data={"fp": None, "lock_path": str(tmp_path / "nonexistent.lock")},
        )
        backend.release(handle)  # must not raise

    def test_acquire_pid_is_current_process(self, tmp_path):
        backend = _make_backend(tmp_path)
        with patch.object(LocalLockBackend, "_os_lock", return_value=True):
            backend.acquire("prod", "alice", "deploy run", 30)
        lock_path = tmp_path / ".strata" / "locks" / "prod.lock"
        data = json.loads(lock_path.read_bytes())
        assert data["pid"] == os.getpid()


# ---------------------------------------------------------------------------
# Contention — lock already held
# ---------------------------------------------------------------------------


class TestContention:
    def test_timeout_raises_lock_timeout_error(self, tmp_path):
        """When the OS lock is never available, LockTimeoutError is raised."""
        backend = _make_backend(tmp_path)
        # Seed the lock file with an existing entry so the holder can be read
        locks_dir = tmp_path / ".strata" / "locks"
        locks_dir.mkdir(parents=True)
        lock_path = locks_dir / "prod.lock"
        entry = _make_entry(holder="bob@company.com")
        lock_path.write_bytes(json.dumps(_entry_to_dict(entry)).encode())

        with patch.object(LocalLockBackend, "_os_lock", return_value=False):
            with pytest.raises(LockTimeoutError) as exc_info:
                backend.acquire("prod", "alice", "deploy run", timeout_seconds=0)

        assert exc_info.value.deployment_name == "prod"
        assert "bob@company.com" in str(exc_info.value)

    def test_timeout_error_contains_timeout_value(self, tmp_path):
        backend = _make_backend(tmp_path)
        locks_dir = tmp_path / ".strata" / "locks"
        locks_dir.mkdir(parents=True)
        (locks_dir / "prod.lock").write_bytes(json.dumps(_entry_to_dict(_make_entry())).encode())
        with patch.object(LocalLockBackend, "_os_lock", return_value=False):
            with pytest.raises(LockTimeoutError) as exc_info:
                backend.acquire("prod", "alice", "deploy run", timeout_seconds=0)
        assert exc_info.value.timeout_seconds == 0


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------


class TestStatus:
    def test_no_lock_file_returns_none(self, tmp_path):
        backend = _make_backend(tmp_path)
        assert backend.status("prod") is None

    def test_returns_entry_when_lock_held_by_another(self, tmp_path):
        """When the OS-level lock is held, status() returns the entry from the file."""
        backend = _make_backend(tmp_path)
        locks_dir = tmp_path / ".strata" / "locks"
        locks_dir.mkdir(parents=True)
        entry = _make_entry(holder="bob@company.com")
        lock_path = locks_dir / "prod.lock"
        lock_path.write_bytes(json.dumps(_entry_to_dict(entry)).encode())

        # Simulate: can't acquire (returns False), so read content
        with patch.object(LocalLockBackend, "_os_lock", return_value=False):
            result = backend.status("prod")

        assert result is not None
        assert result.holder == "bob@company.com"
        assert result.deployment == "prod-platform"

    def test_returns_none_when_lock_file_empty(self, tmp_path):
        backend = _make_backend(tmp_path)
        locks_dir = tmp_path / ".strata" / "locks"
        locks_dir.mkdir(parents=True)
        (locks_dir / "prod.lock").write_bytes(b"")
        with patch.object(LocalLockBackend, "_os_lock", return_value=True):
            assert backend.status("prod") is None

    def test_returns_none_when_lock_free(self, tmp_path):
        """When the OS lock can be acquired, status() returns None (not locked)."""
        backend = _make_backend(tmp_path)
        locks_dir = tmp_path / ".strata" / "locks"
        locks_dir.mkdir(parents=True)
        entry = _make_entry()
        (locks_dir / "prod.lock").write_bytes(json.dumps(_entry_to_dict(entry)).encode())
        # _os_lock returns True means we got the lock → unlocked
        with patch.object(LocalLockBackend, "_os_lock", return_value=True):
            assert backend.status("prod") is None


# ---------------------------------------------------------------------------
# force_release()
# ---------------------------------------------------------------------------


class TestForceRelease:
    def test_removes_lock_file(self, tmp_path):
        backend = _make_backend(tmp_path)
        locks_dir = tmp_path / ".strata" / "locks"
        locks_dir.mkdir(parents=True)
        lock_path = locks_dir / "prod.lock"
        lock_path.write_bytes(b"content")
        backend.force_release("prod")
        assert not lock_path.exists()

    def test_no_error_when_file_absent(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend.force_release("nonexistent")  # must not raise

    def test_raises_backend_error_on_os_error(self, tmp_path):
        backend = _make_backend(tmp_path)
        with patch.object(Path, "unlink", side_effect=OSError("permission denied")):
            with pytest.raises(LockBackendError, match="permission denied"):
                backend.force_release("prod")


# ---------------------------------------------------------------------------
# history()
# ---------------------------------------------------------------------------


class TestHistory:
    def test_empty_when_no_history_file(self, tmp_path):
        backend = _make_backend(tmp_path)
        assert backend.history("prod") == []

    def test_returns_entries_most_recent_first(self, tmp_path):
        backend = _make_backend(tmp_path)
        locks_dir = tmp_path / ".strata" / "locks"
        locks_dir.mkdir(parents=True)
        history_path = locks_dir / "prod.lock.history"
        entries = [_make_entry(lock_id=f"id-{i}", acquired_at="2026-06-16T14:0{i}:00Z") for i in range(3)]
        with open(history_path, "w") as fh:
            for e in entries:
                fh.write(json.dumps(_entry_to_dict(e)) + "\n")

        result = backend.history("prod")
        assert len(result) == 3
        assert result[0].lock_id == "id-2"  # most recent first
        assert result[-1].lock_id == "id-0"

    def test_limit_respected(self, tmp_path):
        backend = _make_backend(tmp_path)
        locks_dir = tmp_path / ".strata" / "locks"
        locks_dir.mkdir(parents=True)
        history_path = locks_dir / "prod.lock.history"
        with open(history_path, "w") as fh:
            for i in range(5):
                fh.write(json.dumps(_entry_to_dict(_make_entry(lock_id=f"id-{i}"))) + "\n")

        result = backend.history("prod", limit=2)
        assert len(result) == 2

    def test_skips_malformed_lines(self, tmp_path):
        backend = _make_backend(tmp_path)
        locks_dir = tmp_path / ".strata" / "locks"
        locks_dir.mkdir(parents=True)
        history_path = locks_dir / "prod.lock.history"
        valid_entry = _make_entry(lock_id="good-id")
        history_path.write_text("NOT JSON\n" + json.dumps(_entry_to_dict(valid_entry)) + "\n")
        result = backend.history("prod")
        assert len(result) == 1
        assert result[0].lock_id == "good-id"

    def test_acquire_appends_to_history_on_each_call(self, tmp_path):
        backend = _make_backend(tmp_path)
        with patch.object(LocalLockBackend, "_os_lock", return_value=True):
            h1 = backend.acquire("prod", "alice", "first run", 30)
            backend.release(h1)
            h2 = backend.acquire("prod", "alice", "second run", 30)
            backend.release(h2)

        result = backend.history("prod")
        assert len(result) == 2
        assert result[0].reason == "second run"
        assert result[1].reason == "first run"
