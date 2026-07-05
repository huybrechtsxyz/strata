"""Unit tests for ConsulLockBackend."""

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from strata.integrations.lock.base_lock_backend import (
    LockBackendError,
    LockHandle,
    LockTimeoutError,
)
from strata.integrations.lock.lock_consul import ConsulLockBackend

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONFIG = {"address": "http://localhost:8500"}


def _make_backend(tmp_path: Path, config: dict | None = None) -> ConsulLockBackend:
    return ConsulLockBackend(config or _CONFIG, tmp_path)


def _http_response(status: int, body: bytes = b"") -> tuple:
    return status, body


def _entry_json(holder: str = "alice", lock_id: str = "lid") -> bytes:
    return json.dumps(
        {
            "lock_id": lock_id,
            "deployment": "dep",
            "holder": holder,
            "hostname": "h",
            "pid": 1,
            "acquired_at": "2024-01-01T00:00:00Z",
            "expires_at": "",
            "reason": "ci",
            "stage": None,
        }
    ).encode()


def _kv_response(holder: str = "alice", lock_id: str = "lid", session: str = "sess-1") -> bytes:
    """Build a Consul KV GET response (JSON array with base64-encoded Value)."""
    value_b64 = base64.b64encode(_entry_json(holder, lock_id)).decode()
    return json.dumps(
        [
            {
                "Key": "strata/locks/dep",
                "Value": value_b64,
                "Session": session,
            }
        ]
    ).encode()


# ---------------------------------------------------------------------------
# Configuration / address
# ---------------------------------------------------------------------------


class TestConsulConfig:
    def test_default_address(self, tmp_path):
        backend = _make_backend(tmp_path, {})
        assert "localhost:8500" in backend._address or "127.0.0.1:8500" in backend._address

    def test_custom_address(self, tmp_path):
        backend = _make_backend(tmp_path, {"address": "http://consul.internal:8500"})
        assert "consul.internal" in backend._address

    def test_trailing_slash_stripped(self, tmp_path):
        backend = _make_backend(tmp_path, {"address": "http://consul:8500/"})
        assert not backend._address.endswith("/")

    def test_token_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CONSUL_HTTP_TOKEN", "secret-token")
        backend = _make_backend(tmp_path)
        assert backend._token == "secret-token"

    def test_token_empty_when_env_absent(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CONSUL_HTTP_TOKEN", raising=False)
        backend = _make_backend(tmp_path)
        assert backend._token == ""


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


class TestConsulSession:
    def test_create_session_returns_id(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._http = MagicMock(return_value=(200, json.dumps({"ID": "sess-abc"}).encode()))
        session_id = backend._create_session("dep")
        assert session_id == "sess-abc"

    def test_create_session_failure_raises(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._http = MagicMock(return_value=(500, b""))
        with pytest.raises(LockBackendError, match="session create failed"):
            backend._create_session("dep")

    def test_destroy_session_swallows_errors(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._http = MagicMock(side_effect=Exception("network down"))
        backend._destroy_session("sess-abc")  # must not raise


# ---------------------------------------------------------------------------
# acquire
# ---------------------------------------------------------------------------


class TestConsulAcquire:
    def test_acquire_success(self, tmp_path):
        backend = _make_backend(tmp_path)
        call_count = {"n": 0}

        def http_side_effect(method, path, body=None):
            call_count["n"] += 1
            if path == "/v1/session/create":
                return 200, json.dumps({"ID": "sess-1"}).encode()
            if "acquire=" in path:
                return 200, b"true"
            return 200, b""

        backend._http = MagicMock(side_effect=http_side_effect)
        handle = backend.acquire("dep", "alice", "ci run", 60)
        assert handle.backend_type == "consul"
        assert handle._backend_data["session_id"] == "sess-1"

    def test_acquire_writes_history(self, tmp_path):
        backend = _make_backend(tmp_path)

        def http_side_effect(method, path, body=None):
            if path == "/v1/session/create":
                return 200, json.dumps({"ID": "sess-1"}).encode()
            if "acquire=" in path:
                return 200, b"true"
            return 200, b""

        backend._http = MagicMock(side_effect=http_side_effect)
        backend.acquire("dep", "alice", "ci run", 60)
        hp = backend._history_path("dep")
        assert hp.exists()
        data = json.loads(hp.read_text().strip())
        assert data["holder"] == "alice"

    @patch("time.sleep")
    def test_acquire_polls_on_false(self, mock_sleep, tmp_path):
        backend = _make_backend(tmp_path)
        call_count = {"kv": 0}

        def http_side_effect(method, path, body=None):
            if path == "/v1/session/create":
                return 200, json.dumps({"ID": "sess-1"}).encode()
            if "acquire=" in path:
                call_count["kv"] += 1
                if call_count["kv"] == 1:
                    return 200, b"false"  # contention
                return 200, b"true"
            if "raw" in path:
                return 200, _entry_json("bob")
            return 200, b""

        backend._http = MagicMock(side_effect=http_side_effect)
        handle = backend.acquire("dep", "alice", "r", 30)
        assert handle.lock_id
        mock_sleep.assert_called()

    @patch("time.sleep")
    @patch("time.monotonic")
    def test_acquire_timeout_raises(self, mock_mono, mock_sleep, tmp_path):
        backend = _make_backend(tmp_path)
        mock_mono.side_effect = [0.0, 10.0, 10.0]

        def http_side_effect(method, path, body=None):
            if path == "/v1/session/create":
                return 200, json.dumps({"ID": "sess-1"}).encode()
            if "acquire=" in path:
                return 200, b"false"
            if "raw" in path:
                return 200, _entry_json("bob")
            return 200, b""

        backend._http = MagicMock(side_effect=http_side_effect)
        with pytest.raises(LockTimeoutError) as exc_info:
            backend.acquire("dep", "alice", "r", 10)
        assert "bob" in str(exc_info.value)

    def test_acquire_destroys_session_on_timeout(self, tmp_path):
        backend = _make_backend(tmp_path)
        destroy_calls = []

        def http_side_effect(method, path, body=None):
            if path == "/v1/session/create":
                return 200, json.dumps({"ID": "sess-1"}).encode()
            if "acquire=" in path:
                return 200, b"false"
            if "/v1/session/destroy/" in path:
                destroy_calls.append(path)
                return 200, b""
            if "raw" in path:
                return 200, _entry_json("bob")
            return 200, b""

        with patch("time.monotonic", side_effect=[0.0, 10.0, 10.0]):
            with patch("time.sleep"):
                backend._http = MagicMock(side_effect=http_side_effect)
                with pytest.raises(LockTimeoutError):
                    backend.acquire("dep", "alice", "r", 10)

        # Session should have been destroyed in the error path
        assert any("/v1/session/destroy/sess-1" in c for c in destroy_calls)

    def test_acquire_unexpected_http_status_raises(self, tmp_path):
        backend = _make_backend(tmp_path)

        def http_side_effect(method, path, body=None):
            if path == "/v1/session/create":
                return 200, json.dumps({"ID": "sess-1"}).encode()
            if "acquire=" in path:
                return 500, b""
            return 200, b""

        backend._http = MagicMock(side_effect=http_side_effect)
        with pytest.raises(LockBackendError, match="KV PUT returned HTTP 500"):
            backend.acquire("dep", "alice", "r", 30)


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------


class TestConsulRelease:
    def test_release_calls_kv_release_and_session_destroy(self, tmp_path):
        backend = _make_backend(tmp_path)
        released_paths = []

        def http_side_effect(method, path, body=None):
            released_paths.append(path)
            return 200, b""

        backend._http = MagicMock(side_effect=http_side_effect)
        handle = LockHandle(
            lock_id="lid",
            backend_type="consul",
            acquired_at="2024-01-01T00:00:00Z",
            _backend_data={"session_id": "sess-1", "kv_key": "strata/locks/dep"},
        )
        backend.release(handle)
        assert any("release=sess-1" in p for p in released_paths)

    def test_release_missing_data_does_not_raise(self, tmp_path):
        backend = _make_backend(tmp_path)
        handle = LockHandle(
            lock_id="lid",
            backend_type="consul",
            acquired_at="",
            _backend_data={},
        )
        backend.release(handle)  # no exception


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


class TestConsulStatus:
    def test_status_404_returns_none(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._http = MagicMock(return_value=(404, b""))
        assert backend.status("dep") is None

    def test_status_locked_returns_entry(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._http = MagicMock(return_value=(200, _kv_response("alice")))
        entry = backend.status("dep")
        assert entry is not None
        assert entry.holder == "alice"

    def test_status_no_session_returns_none(self, tmp_path):
        """KV key exists but no active session — lock is not held."""
        backend = _make_backend(tmp_path)
        value_b64 = base64.b64encode(_entry_json("alice")).decode()
        body = json.dumps([{"Key": "strata/locks/dep", "Value": value_b64, "Session": ""}]).encode()
        backend._http = MagicMock(return_value=(200, body))
        assert backend.status("dep") is None

    def test_status_unexpected_http_raises(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._http = MagicMock(return_value=(500, b""))
        with pytest.raises(LockBackendError, match="HTTP 500"):
            backend.status("dep")


# ---------------------------------------------------------------------------
# force_release
# ---------------------------------------------------------------------------


class TestConsulForceRelease:
    def test_force_release_success(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._http = MagicMock(return_value=(200, b""))
        backend.force_release("dep")  # should not raise

    def test_force_release_unexpected_status_raises(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._http = MagicMock(return_value=(500, b""))
        with pytest.raises(LockBackendError, match="HTTP 500"):
            backend.force_release("dep")


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


class TestConsulHistory:
    def test_empty_when_no_file(self, tmp_path):
        backend = _make_backend(tmp_path)
        assert backend.history("dep") == []

    def test_returns_entries_most_recent_first(self, tmp_path):
        backend = _make_backend(tmp_path)
        hp = backend._history_path("dep")
        hp.parent.mkdir(parents=True, exist_ok=True)
        entries = [
            {
                "lock_id": f"id{i}",
                "deployment": "dep",
                "holder": f"u{i}",
                "hostname": "h",
                "pid": i,
                "acquired_at": f"2024-01-0{i + 1}T00:00:00Z",
                "expires_at": "",
                "reason": "r",
                "stage": None,
            }
            for i in range(3)
        ]
        hp.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        result = backend.history("dep")
        assert result[0].lock_id == "id2"

    def test_limit_respected(self, tmp_path):
        backend = _make_backend(tmp_path)
        hp = backend._history_path("dep")
        hp.parent.mkdir(parents=True, exist_ok=True)
        entries = [
            {
                "lock_id": f"id{i}",
                "deployment": "dep",
                "holder": "u",
                "hostname": "h",
                "pid": i,
                "acquired_at": "2024-01-01T00:00:00Z",
                "expires_at": "",
                "reason": "r",
                "stage": None,
            }
            for i in range(10)
        ]
        hp.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        result = backend.history("dep", limit=5)
        assert len(result) == 5

    def test_skips_malformed_lines(self, tmp_path):
        backend = _make_backend(tmp_path)
        hp = backend._history_path("dep")
        hp.parent.mkdir(parents=True, exist_ok=True)
        hp.write_text(
            'not-json\n{"lock_id":"id0","deployment":"dep","holder":"u","hostname":"h","pid":0,"acquired_at":"","expires_at":"","reason":"r","stage":null}\n'
        )
        result = backend.history("dep")
        assert len(result) == 1
        assert result[0].lock_id == "id0"
