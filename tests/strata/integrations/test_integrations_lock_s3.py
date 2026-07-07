"""Unit tests for S3LockBackend."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from strata.integrations.lock.base_lock_backend import (
    LockBackendError,
    LockHandle,
    LockTimeoutError,
)
from strata.integrations.lock.lock_s3 import S3LockBackend

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONFIG = {"bucket": "my-lock-bucket"}
_CONFIG_WITH_REGION = {"bucket": "my-lock-bucket", "region": "eu-west-1"}
_CONFIG_WITH_PREFIX = {"bucket": "my-lock-bucket", "key": "custom-prefix"}


def _make_backend(tmp_path: Path, config: dict | None = None) -> S3LockBackend:
    return S3LockBackend(_CONFIG if config is None else config, tmp_path)


def _aws_ok(stdout: bytes = b"") -> MagicMock:
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.returncode = 0
    m.stdout = stdout
    m.stderr = b""
    return m


def _aws_fail(returncode: int = 1, stderr: bytes = b"error") -> MagicMock:
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.returncode = returncode
    m.stdout = b""
    m.stderr = stderr
    return m


def _aws_precondition_failed() -> MagicMock:
    return _aws_fail(returncode=1, stderr=b"An error occurred (PreconditionFailed)")


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


class TestS3Config:
    def test_missing_bucket_raises(self, tmp_path):
        backend = _make_backend(tmp_path, {"region": "us-east-1"})  # bucket absent
        with pytest.raises(LockBackendError, match="bucket"):
            backend._get_bucket()

    def test_region_args_when_set(self, tmp_path):
        backend = _make_backend(tmp_path, _CONFIG_WITH_REGION)
        args = backend._get_region_args()
        assert "--region" in args
        assert "eu-west-1" in args

    def test_region_args_empty_when_not_set(self, tmp_path):
        backend = _make_backend(tmp_path)
        assert backend._get_region_args() == []

    def test_object_key_default_prefix(self, tmp_path):
        backend = _make_backend(tmp_path)
        key = backend._object_key("my-deploy")
        assert key == "strata-locks/my-deploy.lock"

    def test_object_key_custom_prefix(self, tmp_path):
        backend = _make_backend(tmp_path, _CONFIG_WITH_PREFIX)
        key = backend._object_key("my-deploy")
        assert key.startswith("custom-prefix/")
        assert key.endswith("my-deploy.lock")

    def test_history_path_uses_s3_prefix(self, tmp_path):
        backend = _make_backend(tmp_path)
        hp = backend._history_path("dep")
        assert "s3-dep.lock.history" in hp.name


# ---------------------------------------------------------------------------
# AWS CLI not found
# ---------------------------------------------------------------------------


class TestS3AwsNotFound:
    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_missing_aws_cli_raises(self, _mock, tmp_path):
        backend = _make_backend(tmp_path)
        with pytest.raises(LockBackendError, match="aws.*CLI not found"):
            backend._aws(["s3api", "list-buckets"])

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="aws", timeout=30))
    def test_timeout_raises(self, _mock, tmp_path):
        backend = _make_backend(tmp_path)
        with pytest.raises(LockBackendError, match="timed out"):
            backend._aws(["s3api", "list-buckets"])


# ---------------------------------------------------------------------------
# acquire
# ---------------------------------------------------------------------------


class TestS3Acquire:
    @patch("subprocess.run")
    def test_acquire_success(self, mock_run, tmp_path):
        mock_run.return_value = _aws_ok(b'{"ETag": "abc"}')
        backend = _make_backend(tmp_path)
        handle = backend.acquire("dep", "alice", "ci run", 60)
        assert handle.backend_type == "s3"
        assert handle._backend_data["bucket"] == "my-lock-bucket"
        assert "dep.lock" in handle._backend_data["key"]

    @patch("subprocess.run")
    def test_acquire_writes_history(self, mock_run, tmp_path):
        mock_run.return_value = _aws_ok(b'{"ETag": "abc"}')
        backend = _make_backend(tmp_path)
        backend.acquire("dep", "alice", "ci run", 60)
        hp = backend._history_path("dep")
        assert hp.exists()
        data = json.loads(hp.read_text().strip())
        assert data["holder"] == "alice"

    @patch("time.sleep")
    @patch("subprocess.run")
    def test_acquire_polls_on_precondition_failed(self, mock_run, mock_sleep, tmp_path):
        holder_bytes = _lock_entry_bytes("bob")
        call_count = {"n": 0}

        def side_effect(cmd, **kwargs):
            if "put-object" in cmd:
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return _aws_precondition_failed()
                return _aws_ok(b'{"ETag": "abc"}')
            # get-object for _read_holder: write holder bytes to the outfile arg
            if "get-object" in cmd:
                outfile = cmd[-1]
                try:
                    with open(outfile, "wb") as f:
                        f.write(holder_bytes)
                except Exception:
                    pass
                return _aws_ok()
            return _aws_ok()

        mock_run.side_effect = side_effect
        backend = _make_backend(tmp_path)
        handle = backend.acquire("dep", "alice", "r", 60)
        assert handle.lock_id
        mock_sleep.assert_called_once()

    @patch("time.sleep")
    @patch("time.monotonic")
    @patch("subprocess.run")
    def test_acquire_timeout_raises(self, mock_run, mock_mono, mock_sleep, tmp_path):
        holder_bytes = _lock_entry_bytes("bob")
        mock_mono.side_effect = [0.0, 10.0, 10.0]

        def side_effect(cmd, **kwargs):
            if "put-object" in cmd:
                return _aws_precondition_failed()
            if "get-object" in cmd:
                outfile = cmd[-1]
                try:
                    with open(outfile, "wb") as f:
                        f.write(holder_bytes)
                except Exception:
                    pass
                return _aws_ok()
            return _aws_ok()

        mock_run.side_effect = side_effect
        backend = _make_backend(tmp_path)
        with pytest.raises(LockTimeoutError) as exc_info:
            backend.acquire("dep", "alice", "r", 10)
        assert "bob" in str(exc_info.value)


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------


class TestS3Release:
    @patch("subprocess.run")
    def test_release_success(self, mock_run, tmp_path):
        mock_run.return_value = _aws_ok()
        backend = _make_backend(tmp_path)
        handle = LockHandle(
            lock_id="lid",
            backend_type="s3",
            acquired_at="2024-01-01T00:00:00Z",
            _backend_data={"bucket": "my-lock-bucket", "key": "strata-locks/dep.lock"},
        )
        backend.release(handle)  # should not raise

    def test_release_missing_data_does_not_raise(self, tmp_path):
        backend = _make_backend(tmp_path)
        handle = LockHandle(
            lock_id="lid",
            backend_type="s3",
            acquired_at="",
            _backend_data={},
        )
        backend.release(handle)  # should not raise

    @patch("subprocess.run")
    def test_release_aws_failure_logs_not_raises(self, mock_run, tmp_path):
        mock_run.return_value = _aws_fail()
        backend = _make_backend(tmp_path)
        handle = LockHandle(
            lock_id="lid",
            backend_type="s3",
            acquired_at="",
            _backend_data={"bucket": "my-lock-bucket", "key": "strata-locks/dep.lock"},
        )
        backend.release(handle)  # should not raise


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


class TestS3Status:
    @patch("subprocess.run")
    def test_status_object_not_found_returns_none(self, mock_run, tmp_path):
        mock_run.return_value = _aws_fail(returncode=1, stderr=b"NoSuchKey")
        backend = _make_backend(tmp_path)
        result = backend.status("dep")
        assert result is None

    @patch("subprocess.run")
    def test_status_returns_entry(self, mock_run, tmp_path):
        entry_data = {
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

        # get-object writes the file to the path given as the last positional arg
        def side_effect(cmd, **kwargs):
            if "get-object" in cmd:
                outfile = cmd[-1]
                with open(outfile, "w") as f:
                    json.dump(entry_data, f)
                return _aws_ok()
            return _aws_ok()

        mock_run.side_effect = side_effect
        backend = _make_backend(tmp_path)
        entry = backend.status("dep")
        assert entry is not None
        assert entry.holder == "alice"


# ---------------------------------------------------------------------------
# force_release
# ---------------------------------------------------------------------------


class TestS3ForceRelease:
    @patch("subprocess.run")
    def test_force_release_success(self, mock_run, tmp_path):
        mock_run.return_value = _aws_ok()
        backend = _make_backend(tmp_path)
        backend.force_release("dep")  # should not raise

    @patch("subprocess.run")
    def test_force_release_failure_raises(self, mock_run, tmp_path):
        mock_run.return_value = _aws_fail(returncode=1, stderr=b"AccessDenied")
        backend = _make_backend(tmp_path)
        with pytest.raises(LockBackendError):
            backend.force_release("dep")


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


class TestS3History:
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
