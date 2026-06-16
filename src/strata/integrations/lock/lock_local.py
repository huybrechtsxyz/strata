"""File-based lock backend — protects against concurrent deploys on a single machine."""

import dataclasses
import json
import os
import platform
import socket
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

_POLL_INTERVAL_SECONDS = 5
_HISTORY_SUFFIX = ".history"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_to_timestamp(iso: str) -> float:
    """Parse an ISO-8601 UTC string (``YYYY-MM-DDTHH:MM:SSZ``) to a POSIX timestamp."""
    dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _entry_to_dict(entry: LockEntry) -> Dict[str, Any]:
    return dataclasses.asdict(entry)


def _dict_to_entry(data: Dict[str, Any]) -> LockEntry:
    return LockEntry(**data)


class LocalLockBackend(BaseLockBackend):
    """File-based lock backend using OS-native file locking.

    - Protects concurrent deploys **on the same machine only**.
    - Lock file: ``{work_path}/.strata/locks/{deployment_name}.lock``
    - History file: ``{work_path}/.strata/locks/{deployment_name}.lock.history``
      (append-only NDJSON — one ``LockEntry`` JSON object per line)
    - Locking mechanism: ``fcntl.flock`` (Unix) / Win32 ``LockFileEx`` (Windows)

    Appropriate for development environments or single-operator teams.
    For multi-machine protection use a remote backend (azurerm, s3, etc.).
    """

    BACKEND_TYPE = "local"

    def __init__(self, work_path: Path) -> None:
        self._locks_dir = work_path / ".strata" / "locks"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _lock_path(self, deployment_name: str) -> Path:
        return self._locks_dir / f"{deployment_name}.lock"

    def _history_path(self, deployment_name: str) -> Path:
        return self._locks_dir / f"{deployment_name}.lock{_HISTORY_SUFFIX}"

    def _ensure_locks_dir(self) -> None:
        self._locks_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # OS-level file lock / unlock
    # ------------------------------------------------------------------

    @staticmethod
    def _os_lock(fp: Any) -> bool:
        """Apply an exclusive, non-blocking OS-level lock to *fp*.

        Returns ``True`` if the lock was obtained, ``False`` if already held.
        """
        if platform.system() == "Windows":
            import msvcrt

            try:
                msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                return False
        else:
            import fcntl

            try:
                fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
                return True
            except OSError:
                return False

    @staticmethod
    def _os_unlock(fp: Any) -> None:
        """Release the OS-level lock on *fp*."""
        if platform.system() == "Windows":
            import msvcrt

            try:
                msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl

            try:
                fcntl.flock(fp.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
            except OSError:
                pass

    # ------------------------------------------------------------------
    # History helpers
    # ------------------------------------------------------------------

    def _append_history(self, deployment_name: str, entry: LockEntry) -> None:
        try:
            self._ensure_locks_dir()
            with open(self._history_path(deployment_name), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(_entry_to_dict(entry)) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.warning("local_lock.history_write_failed", error=str(exc))

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
        """Acquire the file lock, polling every 5s until *timeout_seconds* elapses."""
        self._ensure_locks_dir()
        lock_path = self._lock_path(deployment_name)
        deadline = time.monotonic() + timeout_seconds
        lock_id = str(uuid.uuid4())
        acquired_at = _now_iso()
        # expires_at is informational — the OS lock is released by the process
        expires_at_ts = _iso_to_timestamp(acquired_at) + timeout_seconds
        expires_at = datetime.fromtimestamp(expires_at_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        while True:
            # Open (or create) the lock file in read+write mode without truncating
            try:
                fp = open(lock_path, "a+b")  # noqa: WPS515
            except OSError as exc:
                raise LockBackendError(f"Cannot open lock file {lock_path}: {exc}") from exc

            if self._os_lock(fp):
                # We hold the OS lock — write entry JSON and return handle
                fp.seek(0)
                fp.truncate()
                entry = LockEntry(
                    lock_id=lock_id,
                    deployment=deployment_name,
                    holder=holder,
                    hostname=socket.gethostname(),
                    pid=os.getpid(),
                    acquired_at=acquired_at,
                    expires_at=expires_at,
                    reason=reason,
                )
                fp.write(json.dumps(_entry_to_dict(entry)).encode())
                fp.flush()
                self._append_history(deployment_name, entry)
                logger.info(
                    "local_lock.acquired",
                    deployment=deployment_name,
                    lock_id=lock_id,
                    holder=holder,
                )
                return LockHandle(
                    lock_id=lock_id,
                    backend_type=self.BACKEND_TYPE,
                    acquired_at=acquired_at,
                    _backend_data={"fp": fp, "lock_path": str(lock_path)},
                )

            # Lock is held — read who holds it then close and wait
            try:
                fp.seek(0)
                raw = fp.read().decode(errors="replace")
                current_holder = "unknown"
                if raw:
                    try:
                        current_holder = json.loads(raw).get("holder", "unknown")
                    except json.JSONDecodeError:
                        pass
            finally:
                fp.close()

            if time.monotonic() >= deadline:
                raise LockTimeoutError(deployment_name, timeout_seconds, current_holder)

            logger.debug(
                "local_lock.waiting",
                deployment=deployment_name,
                holder=current_holder,
                remaining_seconds=int(deadline - time.monotonic()),
            )
            time.sleep(_POLL_INTERVAL_SECONDS)

    def release(self, handle: LockHandle) -> None:
        """Release the file lock. Safe to call in a ``finally`` block."""
        fp = handle._backend_data.get("fp")
        lock_path = handle._backend_data.get("lock_path")
        try:
            if fp and not fp.closed:
                self._os_unlock(fp)
                fp.seek(0)
                fp.truncate()
                fp.close()
            if lock_path:
                try:
                    Path(lock_path).unlink(missing_ok=True)
                except OSError:
                    pass
            logger.info(
                "local_lock.released",
                lock_id=handle.lock_id,
                backend_type=handle.backend_type,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("local_lock.release_failed", lock_id=handle.lock_id, error=str(exc))

    def status(self, deployment_name: str) -> Optional[LockEntry]:
        """Return the current lock entry, or ``None`` if unlocked."""
        lock_path = self._lock_path(deployment_name)
        if not lock_path.exists():
            return None
        try:
            with open(lock_path, "rb") as fp:
                if self._os_lock(fp):
                    # We can grab the lock → nothing is holding it
                    self._os_unlock(fp)
                    return None
                # Lock is held by someone else — read content without OS lock
                fp.seek(0)
                raw = fp.read().decode(errors="replace").strip()
                if not raw:
                    return None
                return _dict_to_entry(json.loads(raw))
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.warning("local_lock.status_read_failed", error=str(exc))
            return None

    def force_release(self, deployment_name: str) -> None:
        """Force-delete the lock file regardless of the current holder."""
        lock_path = self._lock_path(deployment_name)
        try:
            lock_path.unlink(missing_ok=True)
            logger.warning(
                "local_lock.force_released",
                deployment=deployment_name,
            )
        except OSError as exc:
            raise LockBackendError(f"force_release failed for '{deployment_name}': {exc}") from exc

    def history(self, deployment_name: str, limit: int = 10) -> List[LockEntry]:
        """Return recent lock events (most recent first), up to *limit* entries."""
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
                            entries.append(_dict_to_entry(json.loads(line)))
                        except (json.JSONDecodeError, TypeError):
                            pass
        except OSError as exc:
            logger.warning("local_lock.history_read_failed", error=str(exc))
        return list(reversed(entries))[:limit]
