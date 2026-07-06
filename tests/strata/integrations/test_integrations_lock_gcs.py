"""Unit tests for GcsLockBackend."""

import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from strata.integrations.lock.base_lock_backend import (
    LockBackendError,
    LockHandle,
    LockTimeoutError,
)
from strata.integrations.lock.lock_gcs import GcsLockBackend

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONFIG = {"bucket": "my-lock-bucket"}
_CONFIG_WITH_PREFIX = {"bucket": "my-lock-bucket", "prefix": "custom-prefix"}


def _make_backend(tmp_path: Path, config: dict | None = None) -> GcsLockBackend:
    return GcsLockBackend(_CONFIG if config is None else config, tmp_path)


def _gcloud_ok(token: str = "gcloud-token-xyz") -> MagicMock:
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.returncode = 0
    m.stdout = token
    m.stderr = ""
    return m


def _gcloud_fail() -> MagicMock:
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.returncode = 1
    m.stdout = ""
    m.stderr = "not authenticated"
    return m


def _gcs_response(status: int, body: bytes = b"") -> tuple:
    return status, body


def _lock_entry_bytes(holder: str = "alice") -> bytes:
    return json.dumps(
        {
            "lock_id": "test-id",
            "deployment": "dep",
            "holder": holder,
            "hostname": "host",
            "pid": 1,
            "acquired_at": "2024-01-01T00:00:00Z",
            "expires_at": "",
            "reason": "ci",
            "stage": None,
        }
    ).encode()


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


class TestGcsConfig:
    def test_missing_bucket_raises(self, tmp_path):
        backend = _make_backend(tmp_path, {"prefix": "locks"})  # bucket absent
        with pytest.raises(LockBackendError, match="bucket"):
            backend._get_bucket()

    def test_object_name_default_prefix(self, tmp_path):
        backend = _make_backend(tmp_path)
        name = backend._object_name("my-deploy")
        assert name == "strata-locks/my-deploy.lock"

    def test_object_name_custom_prefix(self, tmp_path):
        backend = _make_backend(tmp_path, _CONFIG_WITH_PREFIX)
        name = backend._object_name("my-deploy")
        assert name.startswith("custom-prefix/")
        assert name.endswith("my-deploy.lock")

    def test_history_path_uses_gcs_prefix(self, tmp_path):
        backend = _make_backend(tmp_path)
        hp = backend._history_path("dep")
        assert "gcs-dep.lock.history" in hp.name


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------


class TestGcsToken:
    @patch("subprocess.run")
    def test_token_from_gcloud(self, mock_run, tmp_path):
        mock_run.return_value = _gcloud_ok("my-token")
        backend = _make_backend(tmp_path)
        token = backend._get_token()
        assert token == "my-token"

    @patch("subprocess.run")
    def test_token_cached(self, mock_run, tmp_path):
        mock_run.return_value = _gcloud_ok("cached-token")
        backend = _make_backend(tmp_path)
        backend._get_token()
        backend._get_token()
        assert mock_run.call_count == 1

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_gcloud_not_found_raises(self, _mock, tmp_path):
        backend = _make_backend(tmp_path)
        with pytest.raises(LockBackendError, match="gcloud.*CLI not found"):
            backend._get_token()

    @patch("subprocess.run")
    def test_gcloud_failure_raises(self, mock_run, tmp_path):
        mock_run.return_value = _gcloud_fail()
        backend = _make_backend(tmp_path)
        with pytest.raises(LockBackendError, match="gcloud auth failed"):
            backend._get_token()

    @patch("subprocess.run")
    def test_empty_token_raises(self, mock_run, tmp_path):
        m = MagicMock(spec=subprocess.CompletedProcess)
        m.returncode = 0
        m.stdout = ""
        mock_run.return_value = m
        backend = _make_backend(tmp_path)
        with pytest.raises(LockBackendError, match="empty token"):
            backend._get_token()


# ---------------------------------------------------------------------------
# acquire
# ---------------------------------------------------------------------------


class TestGcsAcquire:
    def _patch_token(self, backend: GcsLockBackend, token: str = "tok") -> None:
        backend._get_token = MagicMock(return_value=token)  # type: ignore[method-assign]

    def test_acquire_success_201(self, tmp_path):
        backend = _make_backend(tmp_path)
        self._patch_token(backend)
        backend._gcs_request = MagicMock(return_value=(201, b"{}"))  # type: ignore[method-assign]

        handle = backend.acquire("dep", "alice", "ci run", 60)
        assert handle.backend_type == "gcs"
        assert handle._backend_data["bucket"] == "my-lock-bucket"

    def test_acquire_success_200(self, tmp_path):
        backend = _make_backend(tmp_path)
        self._patch_token(backend)
        backend._gcs_request = MagicMock(return_value=(200, b"{}"))  # type: ignore[method-assign]

        handle = backend.acquire("dep", "alice", "ci run", 60)
        assert handle.lock_id

    def test_acquire_writes_history(self, tmp_path):
        backend = _make_backend(tmp_path)
        self._patch_token(backend)
        backend._gcs_request = MagicMock(return_value=(201, b"{}"))  # type: ignore[method-assign]

        backend.acquire("dep", "alice", "ci run", 60)
        hp = backend._history_path("dep")
        assert hp.exists()
        data = json.loads(hp.read_text().strip())
        assert data["holder"] == "alice"

    @patch("time.sleep")
    def test_acquire_polls_on_412(self, mock_sleep, tmp_path):
        backend = _make_backend(tmp_path)
        self._patch_token(backend)
        call_count = {"n": 0}

        def gcs_request(method, url, token, **kwargs):
            if method == "POST":
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return 412, b""
                return 201, b"{}"
            if method == "GET":
                return 200, _lock_entry_bytes("bob")
            return 200, b""

        backend._gcs_request = MagicMock(side_effect=gcs_request)  # type: ignore[method-assign]
        handle = backend.acquire("dep", "alice", "r", 60)
        assert handle.lock_id
        mock_sleep.assert_called_once()

    @patch("time.sleep")
    @patch("time.monotonic")
    def test_acquire_timeout_raises(self, mock_mono, mock_sleep, tmp_path):
        mock_mono.side_effect = [0.0, 10.0, 10.0]
        backend = _make_backend(tmp_path)
        self._patch_token(backend)

        def gcs_request(method, url, token, **kwargs):
            if method == "POST":
                return 412, b""
            if method == "GET":
                return 200, _lock_entry_bytes("bob")
            return 200, b""

        backend._gcs_request = MagicMock(side_effect=gcs_request)  # type: ignore[method-assign]
        with pytest.raises(LockTimeoutError) as exc_info:
            backend.acquire("dep", "alice", "r", 10)
        assert "bob" in str(exc_info.value)

    def test_acquire_unexpected_status_raises(self, tmp_path):
        backend = _make_backend(tmp_path)
        self._patch_token(backend)
        backend._gcs_request = MagicMock(return_value=(500, b"internal error"))  # type: ignore[method-assign]

        with pytest.raises(LockBackendError, match="500"):
            backend.acquire("dep", "alice", "r", 30)


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------


class TestGcsRelease:
    def test_release_success(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._get_token = MagicMock(return_value="tok")  # type: ignore[method-assign]
        backend._gcs_request = MagicMock(return_value=(204, b""))  # type: ignore[method-assign]

        handle = LockHandle(
            lock_id="lid",
            backend_type="gcs",
            acquired_at="2024-01-01T00:00:00Z",
            _backend_data={"bucket": "my-lock-bucket", "object": "strata-locks/dep.lock"},
        )
        backend.release(handle)  # should not raise

    def test_release_missing_data_does_not_raise(self, tmp_path):
        backend = _make_backend(tmp_path)
        handle = LockHandle(
            lock_id="lid",
            backend_type="gcs",
            acquired_at="",
            _backend_data={},
        )
        backend.release(handle)  # should not raise


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


class TestGcsStatus:
    def test_status_object_not_found_returns_none(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._get_token = MagicMock(return_value="tok")  # type: ignore[method-assign]
        backend._gcs_request = MagicMock(return_value=(404, b""))  # type: ignore[method-assign]

        assert backend.status("dep") is None

    def test_status_returns_entry(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._get_token = MagicMock(return_value="tok")  # type: ignore[method-assign]
        backend._gcs_request = MagicMock(return_value=(200, _lock_entry_bytes("alice")))  # type: ignore[method-assign]

        entry = backend.status("dep")
        assert entry is not None
        assert entry.holder == "alice"


# ---------------------------------------------------------------------------
# force_release
# ---------------------------------------------------------------------------


class TestGcsForceRelease:
    def test_force_release_success(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._get_token = MagicMock(return_value="tok")  # type: ignore[method-assign]
        backend._gcs_request = MagicMock(return_value=(204, b""))  # type: ignore[method-assign]

        backend.force_release("dep")  # should not raise

    def test_force_release_failure_raises(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._get_token = MagicMock(return_value="tok")  # type: ignore[method-assign]
        backend._gcs_request = MagicMock(return_value=(403, b"Access denied"))  # type: ignore[method-assign]

        with pytest.raises(LockBackendError, match="403"):
            backend.force_release("dep")


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


class TestGcsHistory:
    def test_empty_when_no_file(self, tmp_path):
        backend = _make_backend(tmp_path)
        assert backend.history("dep") == []

    def test_history_returns_entries(self, tmp_path):
        backend = _make_backend(tmp_path)
        hp = backend._history_path("dep")
        hp.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "lock_id": "lid",
            "deployment": "dep",
            "holder": "alice",
            "hostname": "h",
            "pid": 1,
            "acquired_at": "2024-01-01T00:00:00Z",
            "expires_at": "",
            "reason": "ci",
            "stage": None,
        }
        with open(hp, "w") as f:
            f.write(json.dumps(entry) + "\n")
        entries = backend.history("dep")
        assert len(entries) == 1
        assert entries[0].holder == "alice"

    def test_history_respects_limit(self, tmp_path):
        backend = _make_backend(tmp_path)
        hp = backend._history_path("dep")
        hp.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "lock_id": "lid",
            "deployment": "dep",
            "holder": "alice",
            "hostname": "h",
            "pid": 1,
            "acquired_at": "2024-01-01T00:00:00Z",
            "expires_at": "",
            "reason": "ci",
            "stage": None,
        }
        with open(hp, "w") as f:
            for _ in range(15):
                f.write(json.dumps(entry) + "\n")
        entries = backend.history("dep", limit=5)
        assert len(entries) == 5


# ---------------------------------------------------------------------------
# _gcs_request low-level
# ---------------------------------------------------------------------------


class TestGcsRequest:
    @patch("urllib.request.urlopen")
    def test_successful_get(self, mock_open, tmp_path):
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200
        mock_resp.read = MagicMock(return_value=b'{"ok": true}')
        mock_open.return_value = mock_resp

        backend = _make_backend(tmp_path)
        status, body = backend._gcs_request("GET", "https://storage.googleapis.com/...", "tok")
        assert status == 200
        assert body == b'{"ok": true}'

    @patch("urllib.request.urlopen")
    def test_http_error_returns_status_code(self, mock_open, tmp_path):
        err = urllib.error.HTTPError(
            url="https://storage.googleapis.com/...",
            code=412,
            msg="Precondition Failed",
            hdrs=MagicMock(),  # type: ignore[arg-type]
            fp=None,
        )
        mock_open.side_effect = err

        backend = _make_backend(tmp_path)
        status, _ = backend._gcs_request("POST", "https://storage.googleapis.com/...", "tok")
        assert status == 412

    @patch("urllib.request.urlopen")
    def test_url_error_raises_backend_error(self, mock_open, tmp_path):
        import socket as _socket

        mock_open.side_effect = urllib.error.URLError(reason=_socket.gaierror("name or service not known"))

        backend = _make_backend(tmp_path)
        with pytest.raises(LockBackendError, match="network error"):
            backend._gcs_request("GET", "https://storage.googleapis.com/...", "tok")
