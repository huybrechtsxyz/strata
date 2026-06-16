"""Consul KV lock backend — uses Consul sessions + KV acquire/release."""

import dataclasses
import json
import os
import socket
import time
import urllib.error
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

_SESSION_TTL = "8h"
_LOCK_DELAY = "0s"
_KV_PREFIX = "strata/locks"
_POLL_INTERVAL = 5  # seconds between acquire retries
_HTTP_TIMEOUT = 30  # seconds for each HTTP call


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ConsulLockBackend(BaseLockBackend):
    """Consul KV lock backend.

    Creates a Consul session with ``Behavior: delete`` (auto-cleanup on TTL
    expiry) and uses KV ``?acquire``/``?release`` for atomic locking.  No
    new Python dependencies — plain ``urllib.request``.

    **Phase 2 (8h TTL, no heartbeat):** The Consul session TTL is set to 8h
    to bound stale-lock retention.  Phase 3 will introduce a heartbeat that
    renews the session every few minutes.

    Configuration keys read from ``backend_model.configuration``:
        - ``address`` — Consul agent address (default ``http://127.0.0.1:8500``)
        - ``datacenter`` — optional datacenter name

    Environment variables:
        - ``CONSUL_HTTP_TOKEN`` — optional ACL token
    """

    BACKEND_TYPE = "consul"

    def __init__(
        self,
        configuration: Dict[str, Any],
        work_path: Path,
    ) -> None:
        self._address = configuration.get("address", "http://127.0.0.1:8500")
        if isinstance(self._address, str):
            self._address = self._address.rstrip("/")
        self._datacenter = configuration.get("datacenter", "")
        self._token = os.environ.get("CONSUL_HTTP_TOKEN", "")
        self._work_path = work_path
        self._locks_dir = work_path / ".strata" / "locks"

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    def _http(
        self,
        method: str,
        path: str,
        body: Optional[bytes] = None,
    ) -> Tuple[int, bytes]:
        """Execute a request against the Consul HTTP API.

        Returns ``(status_code, response_body_bytes)``.
        """
        url = f"{self._address}{path}"
        req = urllib.request.Request(url, data=body, method=method)
        if self._token:
            req.add_header("X-Consul-Token", self._token)
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
            raise LockBackendError(f"ConsulLockBackend: network error: {exc}") from exc

    # ------------------------------------------------------------------
    # KV key
    # ------------------------------------------------------------------

    def _kv_key(self, deployment_name: str) -> str:
        return f"{_KV_PREFIX}/{deployment_name}"

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def _create_session(self, deployment_name: str) -> str:
        payload = json.dumps(
            {
                "Name": f"strata-lock-{deployment_name}",
                "TTL": _SESSION_TTL,
                "Behavior": "delete",
                "LockDelay": _LOCK_DELAY,
            }
        ).encode()
        status, body = self._http("PUT", "/v1/session/create", body=payload)
        if status != 200:
            raise LockBackendError(f"ConsulLockBackend: session create failed (HTTP {status})")
        return json.loads(body)["ID"]

    def _destroy_session(self, session_id: str) -> None:
        try:
            self._http("PUT", f"/v1/session/destroy/{session_id}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "consul_lock.session_destroy_failed",
                session_id=session_id,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # History helpers (local NDJSON)
    # ------------------------------------------------------------------

    def _history_path(self, deployment_name: str) -> Path:
        return self._locks_dir / f"consul-{deployment_name}.lock.history"

    def _append_history(self, deployment_name: str, entry: LockEntry) -> None:
        try:
            self._locks_dir.mkdir(parents=True, exist_ok=True)
            with open(self._history_path(deployment_name), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(dataclasses.asdict(entry)) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.warning("consul_lock.history_write_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Current holder from KV
    # ------------------------------------------------------------------

    def _read_current_holder(self, kv_key: str) -> str:
        status, body = self._http("GET", f"/v1/kv/{kv_key}?raw")
        if status == 200 and body:
            try:
                return json.loads(body).get("holder", "unknown")
            except (json.JSONDecodeError, AttributeError):
                pass
        return "unknown"

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
        """Create a Consul session and acquire the KV lock.  Polls every 5s."""
        lock_id = str(uuid.uuid4())
        acquired_at = _now_iso()
        deadline = time.monotonic() + timeout_seconds
        kv_key = self._kv_key(deployment_name)

        entry = LockEntry(
            lock_id=lock_id,
            deployment=deployment_name,
            holder=holder,
            hostname=socket.gethostname(),
            pid=os.getpid(),
            acquired_at=acquired_at,
            expires_at="",  # TTL is on the Consul session, not the entry
            reason=reason,
        )
        entry_json = json.dumps(dataclasses.asdict(entry)).encode()

        session_id: Optional[str] = None
        try:
            session_id = self._create_session(deployment_name)

            while True:
                status, body = self._http(
                    "PUT",
                    f"/v1/kv/{kv_key}?acquire={session_id}",
                    body=entry_json,
                )
                if status == 200:
                    try:
                        result = json.loads(body)
                    except json.JSONDecodeError:
                        result = False
                    if result is True:
                        self._append_history(deployment_name, entry)
                        logger.info(
                            "consul_lock.acquired",
                            deployment=deployment_name,
                            lock_id=lock_id,
                        )
                        return LockHandle(
                            lock_id=lock_id,
                            backend_type=self.BACKEND_TYPE,
                            acquired_at=acquired_at,
                            _backend_data={
                                "session_id": session_id,
                                "kv_key": kv_key,
                            },
                        )
                    # result is False — lock held by another session
                    current_holder = self._read_current_holder(kv_key)
                    if time.monotonic() >= deadline:
                        raise LockTimeoutError(deployment_name, timeout_seconds, current_holder)
                    logger.debug(
                        "consul_lock.waiting",
                        deployment=deployment_name,
                        holder=current_holder,
                        remaining_seconds=int(deadline - time.monotonic()),
                    )
                    time.sleep(_POLL_INTERVAL)
                    continue

                # Unexpected status
                raise LockBackendError(f"ConsulLockBackend.acquire: KV PUT returned HTTP {status}")

        except (LockTimeoutError, LockBackendError):
            if session_id:
                self._destroy_session(session_id)
            raise

    def release(self, handle: LockHandle) -> None:
        """Release the KV key and destroy the session.  Safe in a ``finally``."""
        session_id = handle._backend_data.get("session_id")
        kv_key = handle._backend_data.get("kv_key")
        try:
            if kv_key and session_id:
                self._http("PUT", f"/v1/kv/{kv_key}?release={session_id}")
            if session_id:
                self._destroy_session(session_id)
            logger.info("consul_lock.released", lock_id=handle.lock_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "consul_lock.release_failed",
                lock_id=handle.lock_id,
                error=str(exc),
            )

    def status(self, deployment_name: str) -> Optional[LockEntry]:
        """Return a ``LockEntry`` if the KV key is locked, or ``None``."""
        kv_key = self._kv_key(deployment_name)
        status_code, body = self._http("GET", f"/v1/kv/{kv_key}?raw")
        if status_code == 404:
            return None
        if status_code == 200:
            if not body:
                return None
            try:
                return LockEntry(**json.loads(body))
            except (json.JSONDecodeError, TypeError):
                return None
        raise LockBackendError(f"ConsulLockBackend.status: HTTP {status_code}")

    def force_release(self, deployment_name: str) -> None:
        """DELETE the KV key, invalidating any current lock regardless of session."""
        kv_key = self._kv_key(deployment_name)
        status_code, _ = self._http("DELETE", f"/v1/kv/{kv_key}")
        if status_code not in (200, 204):
            raise LockBackendError(f"ConsulLockBackend.force_release: HTTP {status_code}")
        logger.warning("consul_lock.force_released", deployment=deployment_name)

    def history(self, deployment_name: str, limit: int = 10) -> List[LockEntry]:
        """Return recent lock events from the local history file (most recent first)."""
        history_path = self._history_path(deployment_name)
        if not history_path.exists():
            return []
        entries: List[LockEntry] = []
        try:
            with open(history_path, encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if stripped:
                        try:
                            entries.append(LockEntry(**json.loads(stripped)))
                        except (json.JSONDecodeError, TypeError):
                            pass
        except OSError as exc:
            logger.warning("consul_lock.history_read_failed", error=str(exc))
        return list(reversed(entries))[:limit]
