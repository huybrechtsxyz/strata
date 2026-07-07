"""AWS S3 lock backend — uses conditional object writes for cross-machine locking."""

import dataclasses
import json
import os
import socket
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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
_CLI_TIMEOUT = 30  # seconds for each aws CLI call
_LOCK_PREFIX = "strata-locks"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class S3LockBackend(BaseLockBackend):
    """AWS S3 lock backend.

    Uses S3 conditional ``PutObject`` with ``--if-none-match "*"`` to
    atomically create the lock object only when it does not yet exist.
    Releasing the lock deletes the object.  No Python SDK required —
    all S3 calls go through the ``aws`` CLI.

    Lock file: ``s3://{bucket}/{key_prefix}/strata-locks/{deployment}.lock``
    History:   ``{work_path}/.strata/locks/s3-{deployment}.lock.history``
               (local NDJSON, same pattern as other backends)

    Configuration keys (from ``backend_model.configuration``):
        - ``bucket``     — required; the S3 bucket name
        - ``key``        — optional; used as ``key_prefix`` if set
        - ``region``     — optional; AWS region override

    Authentication follows the standard AWS credential chain: environment
    variables, ``~/.aws/credentials``, instance profile, etc.  The ``aws``
    CLI must be in PATH.
    """

    BACKEND_TYPE = "s3"

    def __init__(self, configuration: Dict[str, Any], work_path: Path) -> None:
        self._configuration = configuration
        self._work_path = work_path
        self._locks_dir = work_path / ".strata" / "locks"

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    def _get_bucket(self) -> str:
        bucket = self._configuration.get("bucket")
        if not bucket:
            raise LockBackendError("S3LockBackend: 'bucket' missing from backend configuration")
        return str(bucket)

    def _get_region_args(self) -> List[str]:
        region = self._configuration.get("region")
        return ["--region", str(region)] if region else []

    def _object_key(self, deployment_name: str) -> str:
        prefix = self._configuration.get("key", "").strip("/")
        if prefix:
            return f"{prefix}/{_LOCK_PREFIX}/{deployment_name}.lock"
        return f"{_LOCK_PREFIX}/{deployment_name}.lock"

    # ------------------------------------------------------------------
    # AWS CLI subprocess helper
    # ------------------------------------------------------------------

    def _aws(self, args: List[str], input_bytes: Optional[bytes] = None) -> subprocess.CompletedProcess:
        """Run ``aws`` with *args*. Returns the CompletedProcess; never raises."""
        # Global flags go before the subcommand so that the user-supplied args
        # (including any output file path) remain at the end of the command.
        global_flags = ["--output", "json"] + self._get_region_args()
        cmd = ["aws"] + global_flags + args
        try:
            return subprocess.run(
                cmd,
                input=input_bytes,
                capture_output=True,
                timeout=_CLI_TIMEOUT,
            )
        except FileNotFoundError as exc:
            raise LockBackendError("S3LockBackend: 'aws' CLI not found in PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise LockBackendError(f"S3LockBackend: aws CLI timed out after {_CLI_TIMEOUT}s") from exc

    # ------------------------------------------------------------------
    # History helpers
    # ------------------------------------------------------------------

    def _history_path(self, deployment_name: str) -> Path:
        return self._locks_dir / f"s3-{deployment_name}.lock.history"

    def _append_history(self, deployment_name: str, entry: LockEntry) -> None:
        try:
            self._locks_dir.mkdir(parents=True, exist_ok=True)
            with open(self._history_path(deployment_name), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(dataclasses.asdict(entry)) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.warning("s3_lock.history_write_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Lock read helper
    # ------------------------------------------------------------------

    def _read_holder(self, bucket: str, key: str) -> str:
        """Return the holder name from the lock object, or 'unknown'."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            result = self._aws(["s3api", "get-object", "--bucket", bucket, "--key", key, tmp_path])
            if result.returncode != 0:
                return "unknown"
            with open(tmp_path, encoding="utf-8") as fh:
                return json.load(fh).get("holder", "unknown")
        except Exception:  # noqa: BLE001
            return "unknown"
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

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
        """Acquire the S3 lock via conditional PutObject. Polls every 5s until free or timeout."""
        bucket = self._get_bucket()
        key = self._object_key(deployment_name)
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

        while True:
            # Write entry JSON to a temp file; aws s3api requires a file path for --body
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
                tmp.write(entry_bytes)
                tmp_path = tmp.name

            try:
                result = self._aws(
                    [
                        "s3api",
                        "put-object",
                        "--bucket",
                        bucket,
                        "--key",
                        key,
                        "--body",
                        tmp_path,
                        "--content-type",
                        "application/json",
                        "--if-none-match",
                        "*",
                    ]
                )
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

            if result.returncode == 0:
                self._append_history(deployment_name, entry)
                logger.info("s3_lock.acquired", deployment=deployment_name, lock_id=lock_id)
                return LockHandle(
                    lock_id=lock_id,
                    backend_type=self.BACKEND_TYPE,
                    acquired_at=acquired_at,
                    _backend_data={"bucket": bucket, "key": key},
                )

            # 412 PreconditionFailed — object already exists, lock is held
            stderr = result.stderr.decode(errors="replace")
            if "PreconditionFailed" in stderr or result.returncode != 0:
                if time.monotonic() >= deadline:
                    current_holder = self._read_holder(bucket, key)
                    raise LockTimeoutError(deployment_name, timeout_seconds, current_holder)
                current_holder = self._read_holder(bucket, key)
                logger.debug(
                    "s3_lock.waiting",
                    deployment=deployment_name,
                    holder=current_holder,
                    remaining_seconds=int(deadline - time.monotonic()),
                )
                time.sleep(_POLL_INTERVAL)
                continue

            raise LockBackendError(f"S3LockBackend.acquire: aws CLI error: {stderr}")

    def release(self, handle: LockHandle) -> None:
        """Delete the lock object. Safe to call in a ``finally`` block."""
        bucket = handle._backend_data.get("bucket")
        key = handle._backend_data.get("key")
        if not bucket or not key:
            logger.warning("s3_lock.release_missing_data", lock_id=handle.lock_id)
            return
        try:
            result = self._aws(["s3api", "delete-object", "--bucket", bucket, "--key", key])
            if result.returncode != 0:
                logger.warning(
                    "s3_lock.release_failed",
                    lock_id=handle.lock_id,
                    error=result.stderr.decode(errors="replace"),
                )
            else:
                logger.info("s3_lock.released", lock_id=handle.lock_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("s3_lock.release_exception", lock_id=handle.lock_id, error=str(exc))

    def status(self, deployment_name: str) -> Optional[LockEntry]:
        """Return a ``LockEntry`` if the lock object exists, or ``None``."""
        bucket = self._get_bucket()
        key = self._object_key(deployment_name)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            result = self._aws(["s3api", "get-object", "--bucket", bucket, "--key", key, tmp_path])
            if result.returncode != 0:
                return None
            with open(tmp_path, encoding="utf-8") as fh:
                data = json.load(fh)
            return LockEntry(**data)
        except Exception:  # noqa: BLE001
            return None
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def force_release(self, deployment_name: str) -> None:
        """Force-delete the lock object regardless of holder."""
        bucket = self._get_bucket()
        key = self._object_key(deployment_name)
        result = self._aws(["s3api", "delete-object", "--bucket", bucket, "--key", key])
        if result.returncode != 0:
            raise LockBackendError(f"S3LockBackend.force_release: {result.stderr.decode(errors='replace')}")
        logger.info("s3_lock.force_released", deployment=deployment_name)

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
