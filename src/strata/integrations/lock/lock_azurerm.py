"""Azure Blob Storage lock backend — uses blob leases for cross-machine locking."""

import dataclasses
import json
import os
import socket
import subprocess
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

_STORAGE_RESOURCE = "https://storage.azure.com"
_API_VERSION = "2020-08-04"
_POLL_INTERVAL = 5  # seconds between acquire retries
_HTTP_TIMEOUT = 30  # seconds for each HTTP call
_LOCK_PREFIX = "strata-locks"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AzurermLockBackend(BaseLockBackend):
    """Azure Blob Storage lock backend.

    Uses blob leases to serialise deploys across machines.  Authentication
    follows the same pattern as ``AzureKeyVaultIntegration``: ``az`` CLI is
    used to obtain a Bearer token; all REST calls go through ``urllib.request``.
    No new Python dependencies are introduced.

    **Phase 2 (infinite lease):** Acquires an infinite lease (``duration: -1``)
    so the lock holds for the entire deploy without a heartbeat.  Phase 3 will
    switch to 60-second renewable leases for tighter stale-lock detection.

    Configuration keys read from ``backend_model.configuration``:
        - ``storage_account_name`` (required)
        - ``container_name`` (required)
        - ``resource_group_name``, ``key`` — ignored for locking
    """

    BACKEND_TYPE = "azurerm"

    def __init__(
        self,
        configuration: Dict[str, Any],
        work_path: Path,
    ) -> None:
        self._configuration = configuration
        self._work_path = work_path
        self._locks_dir = work_path / ".strata" / "locks"
        self._token_cache: Optional[Tuple[str, float]] = None  # (token, expiry_ts)

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    def _get_storage_account(self) -> str:
        sa = self._configuration.get("storage_account_name")
        if not sa:
            raise LockBackendError("AzurermLockBackend: 'storage_account_name' missing from backend configuration")
        return str(sa)

    def _get_container(self) -> str:
        container = self._configuration.get("container_name")
        if not container:
            raise LockBackendError("AzurermLockBackend: 'container_name' missing from backend configuration")
        return str(container)

    def _blob_url(self, deployment_name: str) -> str:
        sa = self._get_storage_account()
        container = self._get_container()
        return f"https://{sa}.blob.core.windows.net/{container}/{_LOCK_PREFIX}/{deployment_name}.lock"

    # ------------------------------------------------------------------
    # Authentication (cached az CLI token)
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
        """Return a valid Azure Storage Bearer token, refreshing if necessary."""
        if self._token_cache:
            token, expiry = self._token_cache
            if time.time() < expiry - 60:
                return token

        result = subprocess.run(
            [
                "az",
                "account",
                "get-access-token",
                "--resource",
                _STORAGE_RESOURCE,
                "--output",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise LockBackendError(f"AzurermLockBackend: az get-access-token failed: {result.stderr.strip()}")
        token_data = json.loads(result.stdout)
        token = token_data.get("accessToken", "")
        if not token:
            raise LockBackendError("AzurermLockBackend: empty accessToken from az CLI")

        expires_on = token_data.get("expiresOn", "")
        try:
            expiry = datetime.strptime(expires_on[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
        except (ValueError, IndexError):
            expiry = time.time() + 3600

        self._token_cache = (token, expiry)
        return token

    # ------------------------------------------------------------------
    # HTTP helper for Blob Storage REST
    # ------------------------------------------------------------------

    @staticmethod
    def _blob_request(
        method: str,
        url: str,
        token: str,
        extra_headers: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
    ) -> Tuple[int, bytes, Any]:
        """Send a request to the Azure Blob Storage REST API.

        Returns ``(status_code, response_body_bytes, response_headers)``.
        """
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("x-ms-version", _API_VERSION)
        if extra_headers:
            for k, v in extra_headers.items():
                req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as exc:
            raw = b""
            try:
                raw = exc.read()
            except Exception:  # noqa: BLE001
                pass
            return exc.code, raw, exc.headers or {}
        except urllib.error.URLError as exc:
            raise LockBackendError(f"AzurermLockBackend: network error: {exc}") from exc

    # ------------------------------------------------------------------
    # Blob helpers
    # ------------------------------------------------------------------

    def _ensure_blob(self, blob_url: str, token: str, initial_body: bytes) -> None:
        """Create the blob with conditional PUT (no-op if it already exists)."""
        self._blob_request(
            "PUT",
            blob_url,
            token,
            extra_headers={
                "Content-Type": "application/json",
                "x-ms-blob-type": "BlockBlob",
                "If-None-Match": "*",
            },
            body=initial_body,
        )
        # 201 = created, 412 = already exists — both are fine here

    def _write_blob_with_lease(self, blob_url: str, token: str, lease_id: str, body: bytes) -> None:
        """Overwrite blob content while holding the lease."""
        self._blob_request(
            "PUT",
            blob_url,
            token,
            extra_headers={
                "Content-Type": "application/json",
                "x-ms-blob-type": "BlockBlob",
                "x-ms-lease-id": lease_id,
            },
            body=body,
        )

    def _read_current_holder(self, blob_url: str, token: str) -> str:
        status, body, _ = self._blob_request("GET", blob_url, token)
        if status == 200 and body:
            try:
                return json.loads(body).get("holder", "unknown")
            except (json.JSONDecodeError, AttributeError):
                pass
        return "unknown"

    # ------------------------------------------------------------------
    # History helpers (separate blob, best-effort)
    # ------------------------------------------------------------------

    def _history_path(self, deployment_name: str) -> Path:
        return self._locks_dir / f"azurerm-{deployment_name}.lock.history"

    def _append_history(self, deployment_name: str, entry: LockEntry) -> None:
        try:
            self._locks_dir.mkdir(parents=True, exist_ok=True)
            with open(self._history_path(deployment_name), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(dataclasses.asdict(entry)) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.warning("azurerm_lock.history_write_failed", error=str(exc))

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
        """Acquire an infinite blob lease.  Polls every 5s until free or timeout."""
        token = self._get_token()
        blob_url = self._blob_url(deployment_name)
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
            expires_at="",  # infinite lease — no TTL in Phase 2
            reason=reason,
        )
        entry_json = json.dumps(dataclasses.asdict(entry)).encode()

        while True:
            # Try to acquire an infinite lease on the blob
            status, _, resp_headers = self._blob_request(
                "PUT",
                f"{blob_url}?comp=lease",
                token,
                extra_headers={
                    "x-ms-lease-action": "acquire",
                    "x-ms-lease-duration": "-1",
                },
            )

            if status in (200, 201):
                lease_id = ""
                if resp_headers:
                    lease_id = resp_headers.get("x-ms-lease-id") or resp_headers.get("X-Ms-Lease-Id") or ""
                # Write lock entry to blob body (we hold the lease)
                self._write_blob_with_lease(blob_url, token, lease_id, entry_json)
                self._append_history(deployment_name, entry)
                logger.info(
                    "azurerm_lock.acquired",
                    deployment=deployment_name,
                    lock_id=lock_id,
                )
                return LockHandle(
                    lock_id=lock_id,
                    backend_type=self.BACKEND_TYPE,
                    acquired_at=acquired_at,
                    _backend_data={"blob_url": blob_url, "lease_id": lease_id},
                )

            if status == 404:
                # Blob doesn't exist yet — create it and retry lease
                self._ensure_blob(blob_url, token, entry_json)
                continue

            if status == 409:
                # Blob is already leased by another holder
                current_holder = self._read_current_holder(blob_url, token)
                if time.monotonic() >= deadline:
                    raise LockTimeoutError(deployment_name, timeout_seconds, current_holder)
                logger.debug(
                    "azurerm_lock.waiting",
                    deployment=deployment_name,
                    holder=current_holder,
                    remaining_seconds=int(deadline - time.monotonic()),
                )
                time.sleep(_POLL_INTERVAL)
                continue

            raise LockBackendError(f"AzurermLockBackend.acquire: unexpected HTTP {status}")

    def release(self, handle: LockHandle) -> None:
        """Release the blob lease.  Safe to call in a ``finally`` block."""
        blob_url = handle._backend_data.get("blob_url")
        lease_id = handle._backend_data.get("lease_id")
        if not blob_url or not lease_id:
            logger.warning("azurerm_lock.release_missing_data", lock_id=handle.lock_id)
            return
        try:
            token = self._get_token()
            status, _, _ = self._blob_request(
                "PUT",
                f"{blob_url}?comp=lease",
                token,
                extra_headers={
                    "x-ms-lease-action": "release",
                    "x-ms-lease-id": lease_id,
                },
            )
            if status not in (200, 201, 202):
                logger.warning(
                    "azurerm_lock.release_http_error",
                    lock_id=handle.lock_id,
                    status=status,
                )
            else:
                logger.info("azurerm_lock.released", lock_id=handle.lock_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "azurerm_lock.release_failed",
                lock_id=handle.lock_id,
                error=str(exc),
            )

    def status(self, deployment_name: str) -> Optional[LockEntry]:
        """Return a ``LockEntry`` if the blob is leased, or ``None``."""
        try:
            token = self._get_token()
            blob_url = self._blob_url(deployment_name)
            status_code, _, resp_headers = self._blob_request("HEAD", blob_url, token)
            if status_code == 404:
                return None
            if status_code not in (200, 206):
                raise LockBackendError(f"AzurermLockBackend.status: HTTP {status_code}")
            lease_state = ""
            if resp_headers:
                lease_state = resp_headers.get("x-ms-lease-state") or resp_headers.get("X-Ms-Lease-State") or ""
            if lease_state.lower() != "leased":
                return None
            # Blob is leased — GET body to read the LockEntry
            body_status, body, _ = self._blob_request("GET", blob_url, token)
            if body_status == 200 and body:
                try:
                    return LockEntry(**json.loads(body))
                except (json.JSONDecodeError, TypeError):
                    pass
            # Return minimal entry if body can't be parsed
            return LockEntry(
                lock_id="unknown",
                deployment=deployment_name,
                holder="unknown",
                hostname="unknown",
                pid=0,
                acquired_at="",
                expires_at="",
                reason="azurerm blob lease",
            )
        except LockBackendError:
            raise
        except Exception as exc:
            raise LockBackendError(f"AzurermLockBackend.status: {exc}") from exc

    def force_release(self, deployment_name: str) -> None:
        """Break the blob lease regardless of the current holder."""
        try:
            token = self._get_token()
            blob_url = self._blob_url(deployment_name)
            status_code, _, _ = self._blob_request(
                "PUT",
                f"{blob_url}?comp=lease",
                token,
                extra_headers={
                    "x-ms-lease-action": "break",
                    "x-ms-lease-break-period": "0",
                },
            )
            if status_code == 404:
                return  # nothing to release
            if status_code not in (200, 201, 202):
                raise LockBackendError(f"AzurermLockBackend.force_release: HTTP {status_code}")
            logger.warning("azurerm_lock.force_released", deployment=deployment_name)
        except LockBackendError:
            raise
        except Exception as exc:
            raise LockBackendError(f"AzurermLockBackend.force_release: {exc}") from exc

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
            logger.warning("azurerm_lock.history_read_failed", error=str(exc))
        return list(reversed(entries))[:limit]
