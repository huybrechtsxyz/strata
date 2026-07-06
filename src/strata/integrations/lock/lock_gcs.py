"""GCS lock backend — uses conditional object writes for cross-machine locking."""

import dataclasses
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from strata.integrations.lock.base_lock_backend import (
    BaseLockBackend,
    LockBackendError,
    LockEntry,
    LockHandle,
    LockTimeoutError,
)
from strata.logger import get_logger

logger = get_logger(__name__)

_POLL_INTERVAL = 5  # seconds between acquire retries
_HTTP_TIMEOUT = 30  # seconds for each HTTP call
_CLI_TIMEOUT = 30  # seconds for gcloud token fetch
_LOCK_PREFIX = "strata-locks"
_GCS_UPLOAD_URL = "https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
_GCS_OBJECT_URL = "https://storage.googleapis.com/storage/v1/b/{bucket}/o/{object}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _encode_object_name(name: str) -> str:
    """URL-encode a GCS object name for use in REST paths."""
    return urllib.parse.quote(name, safe="")


class GcsLockBackend(BaseLockBackend):
    """GCS lock backend.

    Uses the GCS JSON API with ``ifGenerationMatch=0`` on object inserts to
    atomically create the lock object only when it does not yet exist.
    Auth is handled by ``gcloud auth print-access-token`` (cached per instance).
    No Python SDK required — plain ``urllib.request``.

    Lock file: ``gs://{bucket}/{key_prefix}/strata-locks/{deployment}.lock``
    History:   ``{work_path}/.strata/locks/gcs-{deployment}.lock.history``
               (local NDJSON, same pattern as other backends)

    Configuration keys (from ``backend_model.configuration``):
        - ``bucket``  — required; the GCS bucket name
        - ``prefix``  — optional; key prefix within the bucket
        - ``project`` — optional; GCP project ID (for ``gcloud`` context)

    Authentication uses the active ``gcloud`` account.  Run
    ``gcloud auth application-default login`` or set ``GOOGLE_APPLICATION_CREDENTIALS``.
    """

    BACKEND_TYPE = "gcs"

    def __init__(self, configuration: Dict[str, Any], work_path: Path) -> None:
        self._configuration = configuration
        self._work_path = work_path
        self._locks_dir = work_path / ".strata" / "locks"
        self._token_cache: Optional[Tuple[str, float]] = None  # (token, expiry_ts)

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    def _get_bucket(self) -> str:
        bucket = self._configuration.get("bucket")
        if not bucket:
            raise LockBackendError("GcsLockBackend: 'bucket' missing from backend configuration")
        return str(bucket)

    def _object_name(self, deployment_name: str) -> str:
        prefix = self._configuration.get("prefix", "").strip("/")
        if prefix:
            return f"{prefix}/{_LOCK_PREFIX}/{deployment_name}.lock"
        return f"{_LOCK_PREFIX}/{deployment_name}.lock"

    # ------------------------------------------------------------------
    # Authentication (cached gcloud token)
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
        """Return a valid GCS Bearer token, refreshing if necessary."""
        if self._token_cache:
            token, expiry = self._token_cache
            if time.time() < expiry - 60:
                return token
        try:
            result = subprocess.run(
                ["gcloud", "auth", "print-access-token"],
                capture_output=True,
                text=True,
                timeout=_CLI_TIMEOUT,
            )
        except FileNotFoundError as exc:
            raise LockBackendError("GcsLockBackend: 'gcloud' CLI not found in PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise LockBackendError(f"GcsLockBackend: gcloud timed out after {_CLI_TIMEOUT}s") from exc

        if result.returncode != 0:
            raise LockBackendError(f"GcsLockBackend: gcloud auth failed: {result.stderr.strip()}")

        token = result.stdout.strip()
        if not token:
            raise LockBackendError("GcsLockBackend: empty token from gcloud auth print-access-token")

        # GCS tokens typically expire in 1h; cache for 55 minutes
        self._token_cache = (token, time.time() + 3300)
        return token

    # ------------------------------------------------------------------
    # HTTP helper for GCS JSON API
    # ------------------------------------------------------------------

    def _gcs_request(
        self,
        method: str,
        url: str,
        token: str,
        body: Optional[bytes] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, bytes]:
        """Send a request to the GCS JSON API. Returns (status_code, body_bytes)."""
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        if extra_headers:
            for k, v in extra_headers.items():
                req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            raw = b""
            try:
                raw = exc.read()
            except Exception:  # noqa: BLE001
                pass
            return exc.code, raw
        except urllib.error.URLError as exc:
            raise LockBackendError(f"GcsLockBackend: network error: {exc}") from exc

    # ------------------------------------------------------------------
    # History helpers
    # ------------------------------------------------------------------

    def _history_path(self, deployment_name: str) -> Path:
        return self._locks_dir / f"gcs-{deployment_name}.lock.history"

    def _append_history(self, deployment_name: str, entry: LockEntry) -> None:
        try:
            self._locks_dir.mkdir(parents=True, exist_ok=True)
            with open(self._history_path(deployment_name), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(dataclasses.asdict(entry)) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.warning("gcs_lock.history_write_failed", error=str(exc))

    # ------------------------------------------------------------------
    # BaseLockBackend interface
    # ------------------------------------------------------------------

    def acquire(
        self,
        deployment_name: str,
        holder: str,
        reason: str,
        timeout_seconds: int,
    ) -> LockHandle:
        """Acquire the GCS lock via conditional insert (ifGenerationMatch=0). Polls every 5s."""
        bucket = self._get_bucket()
        obj_name = self._object_name(deployment_name)
        lock_id = str(uuid.uuid4())
        acquired_at = _now_iso()
        deadline = time.monotonic() + timeout_seconds

        entry = LockEntry(
            lock_id=lock_id,
            deployment=deployment_name,
            holder=holder,
            hostname=socket.gethostname(),
            pid=os.getpid(),
            acquired_at=acquired_at,
            expires_at="",
            reason=reason,
        )
        entry_bytes = json.dumps(dataclasses.asdict(entry)).encode()

        # Upload URL: insertObject with ifGenerationMatch=0 (only if object absent)
        upload_url = (
            _GCS_UPLOAD_URL.format(bucket=bucket)
            + f"?uploadType=media&name={_encode_object_name(obj_name)}&ifGenerationMatch=0"
        )

        while True:
            token = self._get_token()
            status, body = self._gcs_request(
                "POST",
                upload_url,
                token,
                body=entry_bytes,
                extra_headers={"Content-Type": "application/json"},
            )

            if status in (200, 201):
                self._append_history(deployment_name, entry)
                logger.info("gcs_lock.acquired", deployment=deployment_name, lock_id=lock_id)
                return LockHandle(
                    lock_id=lock_id,
                    backend_type=self.BACKEND_TYPE,
                    acquired_at=acquired_at,
                    _backend_data={"bucket": bucket, "object": obj_name},
                )

            if status == 412:
                # PreconditionFailed — object already exists, lock is held
                if time.monotonic() >= deadline:
                    current_holder = self._read_holder(bucket, obj_name, token)
                    raise LockTimeoutError(deployment_name, timeout_seconds, current_holder)
                current_holder = self._read_holder(bucket, obj_name, token)
                logger.debug(
                    "gcs_lock.waiting",
                    deployment=deployment_name,
                    holder=current_holder,
                    remaining_seconds=int(deadline - time.monotonic()),
                )
                time.sleep(_POLL_INTERVAL)
                continue

            raise LockBackendError(
                f"GcsLockBackend.acquire: unexpected HTTP {status}: {body.decode(errors='replace')[:200]}"
            )

    def _read_holder(self, bucket: str, obj_name: str, token: str) -> str:
        """Return the holder field from the lock object, or 'unknown'."""
        url = _GCS_OBJECT_URL.format(bucket=bucket, object=_encode_object_name(obj_name)) + "?alt=media"
        status, body = self._gcs_request("GET", url, token)
        if status == 200 and body:
            try:
                return json.loads(body).get("holder", "unknown")
            except (json.JSONDecodeError, AttributeError):
                pass
        return "unknown"

    def release(self, handle: LockHandle) -> None:
        """Delete the lock object. Safe to call in a ``finally`` block."""
        bucket = handle._backend_data.get("bucket")
        obj_name = handle._backend_data.get("object")
        if not bucket or not obj_name:
            logger.warning("gcs_lock.release_missing_data", lock_id=handle.lock_id)
            return
        try:
            token = self._get_token()
            url = _GCS_OBJECT_URL.format(bucket=bucket, object=_encode_object_name(obj_name))
            status, _ = self._gcs_request("DELETE", url, token)
            if status not in (200, 204):
                logger.warning("gcs_lock.release_http_error", lock_id=handle.lock_id, status=status)
            else:
                logger.info("gcs_lock.released", lock_id=handle.lock_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("gcs_lock.release_exception", lock_id=handle.lock_id, error=str(exc))

    def status(self, deployment_name: str) -> Optional[LockEntry]:
        """Return a ``LockEntry`` if the lock object exists, or ``None``."""
        bucket = self._get_bucket()
        obj_name = self._object_name(deployment_name)
        try:
            token = self._get_token()
            url = _GCS_OBJECT_URL.format(bucket=bucket, object=_encode_object_name(obj_name)) + "?alt=media"
            status, body = self._gcs_request("GET", url, token)
            if status == 404:
                return None
            if status == 200 and body:
                return LockEntry(**json.loads(body))
        except Exception:  # noqa: BLE001
            pass
        return None

    def force_release(self, deployment_name: str) -> None:
        """Force-delete the lock object regardless of holder."""
        bucket = self._get_bucket()
        obj_name = self._object_name(deployment_name)
        token = self._get_token()
        url = _GCS_OBJECT_URL.format(bucket=bucket, object=_encode_object_name(obj_name))
        status, body = self._gcs_request("DELETE", url, token)
        if status not in (200, 204):
            raise LockBackendError(
                f"GcsLockBackend.force_release: HTTP {status}: {body.decode(errors='replace')[:200]}"
            )
        logger.info("gcs_lock.force_released", deployment=deployment_name)

    def history(self, deployment_name: str, limit: int = 10) -> List[LockEntry]:
        """Return recent lock events from the local history file."""
        history_path = self._history_path(deployment_name)
        if not history_path.exists():
            return []
        entries: List[LockEntry] = []
        try:
            with open(history_path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(LockEntry(**json.loads(line)))
                        except Exception:  # noqa: BLE001
                            pass
        except Exception:  # noqa: BLE001
            return []
        return list(reversed(entries))[:limit]
