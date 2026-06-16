"""Unit tests for TfcLockBackend."""

import json
import urllib.error
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from strata.integrations.lock.base_lock_backend import (
    LockBackendError,
    LockHandle,
    LockTimeoutError,
)
from strata.integrations.lock.lock_tfc import TfcLockBackend

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONFIG = {
    "organization": "my-org",
    "workspaces": {"name": "my-workspace"},
}

_WS_ID = "ws-abc123"


def _make_backend(tmp_path: Path, config: dict | None = None) -> TfcLockBackend:
    return TfcLockBackend(config or _CONFIG, tmp_path)


def _http_response(status: int, body: Any) -> MagicMock:
    """Build a mock urllib response context manager."""
    raw = json.dumps(body).encode() if body else b""
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = raw
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _http_error(status: int) -> urllib.error.HTTPError:
    err = urllib.error.HTTPError(url="", code=status, msg="", hdrs=None, fp=BytesIO(b""))  # type: ignore[arg-type]
    return err


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


class TestTfcConfig:
    def test_get_org_raises_when_missing(self, tmp_path):
        backend = _make_backend(tmp_path, {"workspaces": {"name": "ws"}})
        with pytest.raises(LockBackendError, match="organization"):
            backend._get_org()

    def test_get_workspace_name_from_nested_dict(self, tmp_path):
        backend = _make_backend(tmp_path)
        assert backend._get_workspace_name() == "my-workspace"

    def test_get_workspace_name_from_flat_key(self, tmp_path):
        backend = _make_backend(tmp_path, {"organization": "org", "workspace": "flat-ws"})
        assert backend._get_workspace_name() == "flat-ws"

    def test_get_workspace_name_from_string_workspaces(self, tmp_path):
        backend = _make_backend(tmp_path, {"organization": "org", "workspaces": "str-ws"})
        assert backend._get_workspace_name() == "str-ws"

    def test_get_workspace_name_raises_when_missing(self, tmp_path):
        backend = _make_backend(tmp_path, {"organization": "org"})
        with pytest.raises(LockBackendError, match="workspace name missing"):
            backend._get_workspace_name()


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


class TestTfcToken:
    def test_token_from_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TF_TOKEN_app_terraform_io", "my-env-token")
        backend = _make_backend(tmp_path)
        assert backend._get_token() == "my-env-token"

    def test_token_from_terraformrc(self, tmp_path, monkeypatch, tmp_path_factory):
        monkeypatch.delenv("TF_TOKEN_app_terraform_io", raising=False)
        rc_dir = tmp_path_factory.mktemp("home")
        rc_file = rc_dir / ".terraformrc"
        rc_file.write_text('credentials "app.terraform.io" {\n  token = "rc-token-abc"\n}\n')
        with patch("pathlib.Path.home", return_value=rc_dir):
            backend = _make_backend(tmp_path)
            assert backend._get_token() == "rc-token-abc"

    def test_token_not_found_raises(self, tmp_path, monkeypatch, tmp_path_factory):
        monkeypatch.delenv("TF_TOKEN_app_terraform_io", raising=False)
        empty_dir = tmp_path_factory.mktemp("nohome")
        with patch("pathlib.Path.home", return_value=empty_dir):
            backend = _make_backend(tmp_path)
            with pytest.raises(LockBackendError, match="TFC token not found"):
                backend._get_token()


# ---------------------------------------------------------------------------
# Workspace ID resolution
# ---------------------------------------------------------------------------


class TestTfcWorkspaceResolution:
    @patch("urllib.request.urlopen")
    def test_resolves_workspace_id(self, mock_urlopen, tmp_path, monkeypatch):
        monkeypatch.setenv("TF_TOKEN_app_terraform_io", "tok")
        ws_resp = {"data": {"id": _WS_ID}}
        mock_urlopen.return_value = _http_response(200, ws_resp)
        backend = _make_backend(tmp_path)
        ws_id = backend._resolve_workspace_id("tok")
        assert ws_id == _WS_ID

    @patch("urllib.request.urlopen")
    def test_caches_workspace_id(self, mock_urlopen, tmp_path, monkeypatch):
        monkeypatch.setenv("TF_TOKEN_app_terraform_io", "tok")
        ws_resp = {"data": {"id": _WS_ID}}
        mock_urlopen.return_value = _http_response(200, ws_resp)
        backend = _make_backend(tmp_path)
        backend._resolve_workspace_id("tok")
        backend._resolve_workspace_id("tok")  # second call — should use cache
        assert mock_urlopen.call_count == 1

    @patch("urllib.request.urlopen")
    def test_workspace_not_found_raises(self, mock_urlopen, tmp_path, monkeypatch):
        monkeypatch.setenv("TF_TOKEN_app_terraform_io", "tok")
        mock_urlopen.side_effect = _http_error(404)
        backend = _make_backend(tmp_path)
        with pytest.raises(LockBackendError, match="not found"):
            backend._resolve_workspace_id("tok")


# ---------------------------------------------------------------------------
# acquire
# ---------------------------------------------------------------------------


class TestTfcAcquire:
    def _setup_urlopen_sequence(self, mock_urlopen, ws_resp, lock_resp):
        """Return mock sequence: first call = workspace, second = lock action."""
        mock_urlopen.side_effect = [
            _http_response(200, ws_resp),
            lock_resp,
        ]

    @patch("urllib.request.urlopen")
    def test_acquire_success(self, mock_urlopen, tmp_path, monkeypatch):
        monkeypatch.setenv("TF_TOKEN_app_terraform_io", "tok")
        ws_resp = {"data": {"id": _WS_ID}}
        lock_resp = _http_response(200, {"data": {"id": _WS_ID}})
        self._setup_urlopen_sequence(mock_urlopen, ws_resp, lock_resp)

        backend = _make_backend(tmp_path)
        handle = backend.acquire("my-deployment", "alice", "ci run", 60)

        assert handle.backend_type == "terraform_cloud"
        assert handle._backend_data["workspace_id"] == _WS_ID
        assert handle.lock_id

    @patch("urllib.request.urlopen")
    def test_acquire_writes_history(self, mock_urlopen, tmp_path, monkeypatch):
        monkeypatch.setenv("TF_TOKEN_app_terraform_io", "tok")
        ws_resp = {"data": {"id": _WS_ID}}
        lock_resp = _http_response(200, {"data": {"id": _WS_ID}})
        self._setup_urlopen_sequence(mock_urlopen, ws_resp, lock_resp)

        backend = _make_backend(tmp_path)
        backend.acquire("my-deployment", "alice", "ci run", 60)

        history_path = backend._history_path("my-deployment")
        assert history_path.exists()
        line = json.loads(history_path.read_text().strip())
        assert line["holder"] == "alice"

    @patch("urllib.request.urlopen")
    @patch("time.sleep")
    def test_acquire_polls_on_409(self, mock_sleep, mock_urlopen, tmp_path, monkeypatch):
        monkeypatch.setenv("TF_TOKEN_app_terraform_io", "tok")
        ws_resp = {"data": {"id": _WS_ID}}
        ws_detail = {"data": {"id": _WS_ID, "attributes": {"locked": True, "locked-by": {"username": "bob"}}}}
        lock_ok = _http_response(200, {"data": {"id": _WS_ID}})

        # First call: ws resolution, second: 409, third: ws detail (holder), fourth: lock success
        mock_urlopen.side_effect = [
            _http_response(200, ws_resp),
            _http_error(409),
            _http_response(200, ws_detail),
            lock_ok,
        ]

        backend = _make_backend(tmp_path)
        handle = backend.acquire("dep", "alice", "r", 30)
        assert handle.lock_id
        assert mock_sleep.called

    @patch("urllib.request.urlopen")
    @patch("time.sleep")
    @patch("time.monotonic")
    def test_acquire_timeout_raises(self, mock_mono, mock_sleep, mock_urlopen, tmp_path, monkeypatch):
        monkeypatch.setenv("TF_TOKEN_app_terraform_io", "tok")
        ws_resp = {"data": {"id": _WS_ID}}
        ws_detail = {"data": {"id": _WS_ID, "attributes": {"locked": True, "locked-by": {"username": "bob"}}}}
        # monotonic: first call baseline=0, second call inside loop returns timeout
        mock_mono.side_effect = [0.0, 10.0, 10.0]  # deadline=10, first check already past

        mock_urlopen.side_effect = [
            _http_response(200, ws_resp),
            _http_error(409),
            _http_response(200, ws_detail),
        ]

        backend = _make_backend(tmp_path)
        with pytest.raises(LockTimeoutError) as exc_info:
            backend.acquire("dep", "alice", "r", 10)
        assert "bob" in str(exc_info.value)

    @patch("urllib.request.urlopen")
    def test_acquire_unexpected_status_raises(self, mock_urlopen, tmp_path, monkeypatch):
        monkeypatch.setenv("TF_TOKEN_app_terraform_io", "tok")
        ws_resp = {"data": {"id": _WS_ID}}
        mock_urlopen.side_effect = [
            _http_response(200, ws_resp),
            _http_error(500),
        ]
        backend = _make_backend(tmp_path)
        with pytest.raises(LockBackendError, match="unexpected HTTP 500"):
            backend.acquire("dep", "alice", "r", 30)


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------


class TestTfcRelease:
    @patch("urllib.request.urlopen")
    def test_release_success(self, mock_urlopen, tmp_path, monkeypatch):
        monkeypatch.setenv("TF_TOKEN_app_terraform_io", "tok")
        mock_urlopen.return_value = _http_response(200, {})
        handle = LockHandle(
            lock_id="lid",
            backend_type="terraform_cloud",
            acquired_at="2024-01-01T00:00:00Z",
            _backend_data={"workspace_id": _WS_ID},
        )
        backend = _make_backend(tmp_path)
        backend.release(handle)  # should not raise

    def test_release_missing_ws_id_logs_warning(self, tmp_path):
        handle = LockHandle(
            lock_id="lid",
            backend_type="terraform_cloud",
            acquired_at="2024-01-01T00:00:00Z",
            _backend_data={},
        )
        backend = _make_backend(tmp_path)
        backend.release(handle)  # no exception


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


class TestTfcStatus:
    @patch("urllib.request.urlopen")
    def test_status_unlocked_returns_none(self, mock_urlopen, tmp_path, monkeypatch):
        monkeypatch.setenv("TF_TOKEN_app_terraform_io", "tok")
        ws_resp = {"data": {"id": _WS_ID}}
        ws_detail = {"data": {"id": _WS_ID, "attributes": {"locked": False}}}
        mock_urlopen.side_effect = [
            _http_response(200, ws_resp),
            _http_response(200, ws_detail),
        ]
        backend = _make_backend(tmp_path)
        assert backend.status("dep") is None

    @patch("urllib.request.urlopen")
    def test_status_locked_returns_entry(self, mock_urlopen, tmp_path, monkeypatch):
        monkeypatch.setenv("TF_TOKEN_app_terraform_io", "tok")
        ws_resp = {"data": {"id": _WS_ID}}
        ws_detail = {
            "data": {
                "id": _WS_ID,
                "attributes": {
                    "locked": True,
                    "locked-by": {"username": "ci-user"},
                    "updated-at": "2024-01-01T00:00:00Z",
                },
            }
        }
        mock_urlopen.side_effect = [
            _http_response(200, ws_resp),
            _http_response(200, ws_detail),
        ]
        backend = _make_backend(tmp_path)
        entry = backend.status("dep")
        assert entry is not None
        assert entry.holder == "ci-user"
        assert entry.deployment == "dep"


# ---------------------------------------------------------------------------
# force_release
# ---------------------------------------------------------------------------


class TestTfcForceRelease:
    @patch("urllib.request.urlopen")
    def test_force_unlock_success(self, mock_urlopen, tmp_path, monkeypatch):
        monkeypatch.setenv("TF_TOKEN_app_terraform_io", "tok")
        ws_resp = {"data": {"id": _WS_ID}}
        unlock_resp = _http_response(200, {})
        mock_urlopen.side_effect = [
            _http_response(200, ws_resp),
            unlock_resp,
        ]
        backend = _make_backend(tmp_path)
        backend.force_release("dep")  # should not raise

    @patch("urllib.request.urlopen")
    def test_force_unlock_falls_back_to_regular_unlock(self, mock_urlopen, tmp_path, monkeypatch):
        monkeypatch.setenv("TF_TOKEN_app_terraform_io", "tok")
        ws_resp = {"data": {"id": _WS_ID}}
        mock_urlopen.side_effect = [
            _http_response(200, ws_resp),
            _http_error(403),  # force-unlock fails
            _http_response(200, {}),  # regular unlock succeeds
        ]
        backend = _make_backend(tmp_path)
        backend.force_release("dep")  # should not raise


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


class TestTfcHistory:
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
        result = backend.history("dep", limit=10)
        assert result[0].lock_id == "id2"  # most recent first
        assert len(result) == 3

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
        result = backend.history("dep", limit=3)
        assert len(result) == 3
