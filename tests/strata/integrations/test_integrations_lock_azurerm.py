"""Unit tests for AzurermLockBackend."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from strata.integrations.lock.base_lock_backend import (
    LockBackendError,
    LockHandle,
    LockTimeoutError,
)
from strata.integrations.lock.lock_azurerm import AzurermLockBackend

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONFIG = {
    "storage_account_name": "mystorageaccount",
    "container_name": "tfstate",
}


def _make_backend(tmp_path: Path, config: dict | None = None) -> AzurermLockBackend:
    return AzurermLockBackend(config or _CONFIG, tmp_path)


def _az_token_result(token: str = "az-token-abc") -> MagicMock:
    """Return a mock subprocess.CompletedProcess for az get-access-token."""
    m = MagicMock()
    m.returncode = 0
    m.stdout = json.dumps(
        {
            "accessToken": token,
            "expiresOn": "2099-01-01 00:00:00.000000",
        }
    )
    m.stderr = ""
    return m


def _blob_response(status: int, headers: dict | None = None, body: bytes = b"") -> tuple:
    """Return (status, body, headers_mock) as returned by _blob_request."""
    h = MagicMock()
    h.get = lambda k, default=None: (headers or {}).get(k, default)
    return status, body, h


class _MockHeaders:
    def __init__(self, data: dict):
        self._data = {k.lower(): v for k, v in data.items()}

    def get(self, key: str, default: str = "") -> str:
        return self._data.get(key.lower(), default)


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


class TestAzurermConfig:
    def test_missing_storage_account_raises(self, tmp_path):
        backend = _make_backend(tmp_path, {"container_name": "c"})
        with pytest.raises(LockBackendError, match="storage_account_name"):
            backend._get_storage_account()

    def test_missing_container_raises(self, tmp_path):
        backend = _make_backend(tmp_path, {"storage_account_name": "sa"})
        with pytest.raises(LockBackendError, match="container_name"):
            backend._get_container()

    def test_blob_url_format(self, tmp_path):
        backend = _make_backend(tmp_path)
        url = backend._blob_url("my-deployment")
        assert "mystorageaccount.blob.core.windows.net" in url
        assert "tfstate/strata-locks/my-deployment.lock" in url


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------


class TestAzurermToken:
    @patch("subprocess.run")
    def test_token_from_az_cli(self, mock_run, tmp_path):
        mock_run.return_value = _az_token_result("hello-token")
        backend = _make_backend(tmp_path)
        token = backend._get_token()
        assert token == "hello-token"

    @patch("subprocess.run")
    def test_token_cached(self, mock_run, tmp_path):
        mock_run.return_value = _az_token_result("cached-token")
        backend = _make_backend(tmp_path)
        backend._get_token()
        backend._get_token()
        assert mock_run.call_count == 1

    @patch("subprocess.run")
    def test_az_failure_raises(self, mock_run, tmp_path):
        m = MagicMock()
        m.returncode = 1
        m.stderr = "not logged in"
        m.stdout = ""
        mock_run.return_value = m
        backend = _make_backend(tmp_path)
        with pytest.raises(LockBackendError, match="az get-access-token failed"):
            backend._get_token()


# ---------------------------------------------------------------------------
# acquire
# ---------------------------------------------------------------------------


class TestAzurermAcquire:
    def _patch_backend(self, backend: AzurermLockBackend, token: str = "tok") -> None:
        backend._get_token = MagicMock(return_value=token)  # type: ignore[method-assign]

    def test_acquire_success_first_attempt(self, tmp_path):
        backend = _make_backend(tmp_path)
        self._patch_backend(backend)
        lease_id = "lease-abc"

        def blob_request_side_effect(method, url, token, extra_headers=None, body=None):
            if "comp=lease" in url and extra_headers and extra_headers.get("x-ms-lease-action") == "acquire":
                h = _MockHeaders({"x-ms-lease-id": lease_id})
                return 201, b"", h
            return 200, b"", _MockHeaders({})

        backend._blob_request = MagicMock(side_effect=blob_request_side_effect)

        handle = backend.acquire("dep", "alice", "ci run", 60)
        assert handle.backend_type == "azurerm"
        assert handle._backend_data["lease_id"] == lease_id

    def test_acquire_creates_blob_on_404(self, tmp_path):
        backend = _make_backend(tmp_path)
        self._patch_backend(backend)
        lease_id = "lease-abc"
        call_count = {"n": 0}

        def blob_request_side_effect(method, url, token, extra_headers=None, body=None):
            if "comp=lease" in url:
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return 404, b"", _MockHeaders({})
                h = _MockHeaders({"x-ms-lease-id": lease_id})
                return 201, b"", h
            return 201, b"", _MockHeaders({})  # blob creation

        backend._blob_request = MagicMock(side_effect=blob_request_side_effect)

        handle = backend.acquire("dep", "alice", "ci run", 60)
        assert handle.lock_id

    @patch("time.sleep")
    def test_acquire_polls_on_409(self, mock_sleep, tmp_path):
        backend = _make_backend(tmp_path)
        self._patch_backend(backend)
        lease_id = "lease-abc"
        call_count = {"n": 0}

        def blob_request_side_effect(method, url, token, extra_headers=None, body=None):
            if "comp=lease" in url and extra_headers and extra_headers.get("x-ms-lease-action") == "acquire":
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return 409, b"", _MockHeaders({})
                h = _MockHeaders({"x-ms-lease-id": lease_id})
                return 201, b"", h
            if method == "GET":
                entry = {
                    "holder": "bob",
                    "lock_id": "x",
                    "deployment": "dep",
                    "hostname": "h",
                    "pid": 1,
                    "acquired_at": "",
                    "expires_at": "",
                    "reason": "r",
                    "stage": None,
                }
                return 200, json.dumps(entry).encode(), _MockHeaders({})
            return 200, b"", _MockHeaders({})

        backend._blob_request = MagicMock(side_effect=blob_request_side_effect)
        handle = backend.acquire("dep", "alice", "r", 30)
        assert handle.lock_id
        mock_sleep.assert_called()

    @patch("time.sleep")
    @patch("time.monotonic")
    def test_acquire_timeout_raises(self, mock_mono, mock_sleep, tmp_path):
        backend = _make_backend(tmp_path)
        self._patch_backend(backend)
        mock_mono.side_effect = [0.0, 10.0, 10.0]

        def blob_request_side_effect(method, url, token, extra_headers=None, body=None):
            if "comp=lease" in url:
                return 409, b"", _MockHeaders({})
            entry = {
                "holder": "bob",
                "lock_id": "x",
                "deployment": "dep",
                "hostname": "h",
                "pid": 1,
                "acquired_at": "",
                "expires_at": "",
                "reason": "r",
                "stage": None,
            }
            return 200, json.dumps(entry).encode(), _MockHeaders({})

        backend._blob_request = MagicMock(side_effect=blob_request_side_effect)
        with pytest.raises(LockTimeoutError) as exc_info:
            backend.acquire("dep", "alice", "r", 10)
        assert "bob" in str(exc_info.value)

    def test_acquire_writes_history(self, tmp_path):
        backend = _make_backend(tmp_path)
        self._patch_backend(backend)
        lease_id = "lease-abc"

        def blob_request_side_effect(method, url, token, extra_headers=None, body=None):
            if "comp=lease" in url and extra_headers and extra_headers.get("x-ms-lease-action") == "acquire":
                h = _MockHeaders({"x-ms-lease-id": lease_id})
                return 201, b"", h
            return 200, b"", _MockHeaders({})

        backend._blob_request = MagicMock(side_effect=blob_request_side_effect)
        backend.acquire("dep", "alice", "ci run", 60)

        hp = backend._history_path("dep")
        assert hp.exists()
        data = json.loads(hp.read_text().strip())
        assert data["holder"] == "alice"

    def test_acquire_unexpected_status_raises(self, tmp_path):
        backend = _make_backend(tmp_path)
        self._patch_backend(backend)

        backend._blob_request = MagicMock(return_value=(500, b"", _MockHeaders({})))
        with pytest.raises(LockBackendError, match="unexpected HTTP 500"):
            backend.acquire("dep", "alice", "r", 30)


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------


class TestAzurermRelease:
    def test_release_success(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._get_token = MagicMock(return_value="tok")
        backend._blob_request = MagicMock(return_value=(200, b"", _MockHeaders({})))

        handle = LockHandle(
            lock_id="lid",
            backend_type="azurerm",
            acquired_at="2024-01-01T00:00:00Z",
            _backend_data={"blob_url": "https://sa.blob.core.windows.net/c/x.lock", "lease_id": "lid"},
        )
        backend.release(handle)  # should not raise

    def test_release_missing_data_logs_warning(self, tmp_path):
        backend = _make_backend(tmp_path)
        handle = LockHandle(
            lock_id="lid",
            backend_type="azurerm",
            acquired_at="",
            _backend_data={},
        )
        backend.release(handle)  # no exception


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


class TestAzurermStatus:
    def test_status_blob_not_found_returns_none(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._get_token = MagicMock(return_value="tok")
        backend._blob_request = MagicMock(return_value=(404, b"", _MockHeaders({})))
        assert backend.status("dep") is None

    def test_status_unlocked_returns_none(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._get_token = MagicMock(return_value="tok")
        backend._blob_request = MagicMock(return_value=(200, b"", _MockHeaders({"x-ms-lease-state": "available"})))
        assert backend.status("dep") is None

    def test_status_leased_returns_entry(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._get_token = MagicMock(return_value="tok")
        entry_data = {
            "lock_id": "lid",
            "deployment": "dep",
            "holder": "alice",
            "hostname": "host",
            "pid": 1,
            "acquired_at": "2024-01-01T00:00:00Z",
            "expires_at": "",
            "reason": "ci",
            "stage": None,
        }

        def side_effect(method, url, token, extra_headers=None, body=None):
            if method == "HEAD":
                return 200, b"", _MockHeaders({"x-ms-lease-state": "leased"})
            return 200, json.dumps(entry_data).encode(), _MockHeaders({})

        backend._blob_request = MagicMock(side_effect=side_effect)
        entry = backend.status("dep")
        assert entry is not None
        assert entry.holder == "alice"


# ---------------------------------------------------------------------------
# force_release
# ---------------------------------------------------------------------------


class TestAzurermForceRelease:
    def test_force_release_success(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._get_token = MagicMock(return_value="tok")
        backend._blob_request = MagicMock(return_value=(202, b"", _MockHeaders({})))
        backend.force_release("dep")  # should not raise

    def test_force_release_on_missing_blob_is_noop(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._get_token = MagicMock(return_value="tok")
        backend._blob_request = MagicMock(return_value=(404, b"", _MockHeaders({})))
        backend.force_release("dep")  # should not raise

    def test_force_release_unexpected_status_raises(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._get_token = MagicMock(return_value="tok")
        backend._blob_request = MagicMock(return_value=(500, b"", _MockHeaders({})))
        with pytest.raises(LockBackendError, match="HTTP 500"):
            backend.force_release("dep")


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


class TestAzurermHistory:
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
        result = backend.history("dep", limit=4)
        assert len(result) == 4
