"""Terraform Cloud lock backend — uses the TFC Workspace Lock API."""

import dataclasses
import json
import os
import re
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

_BASE_URL = "https://app.terraform.io"
_POLL_INTERVAL = 5  # seconds between acquire retries
_HTTP_TIMEOUT = 30  # seconds for each HTTP call


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TfcLockBackend(BaseLockBackend):
    """Terraform Cloud lock backend.

    Uses the TFC Workspace Lock API to serialise deploys that share a TFC
    remote backend.  No new dependencies — plain ``urllib.request`` with a
    Bearer token.

    **Phase 2 (no heartbeat):** The workspace lock is held until explicitly
    released.  TFC does not impose a TTL on workspace locks.  Phase 3 will
    add a heartbeat mechanism for stale-lock detection.

    Auth:
        1. ``TF_TOKEN_app_terraform_io`` environment variable (preferred)
        2. ``~/.terraformrc`` credentials block for ``app.terraform.io``

    Configuration keys read from ``backend_model.configuration``:
        - ``organization`` (required)
        - ``workspaces.name`` **or** ``workspace`` (required)
    """

    BACKEND_TYPE = "terraform_cloud"

    def __init__(
        self,
        configuration: Dict[str, Any],
        work_path: Path,
    ) -> None:
        self._configuration = configuration
        self._work_path = work_path
        self._locks_dir = work_path / ".strata" / "locks"
        self._workspace_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    def _get_org(self) -> str:
        org = self._configuration.get("organization")
        if not org:
            raise LockBackendError("TfcLockBackend: 'organization' missing from backend configuration")
        return str(org)

    def _get_workspace_name(self) -> str:
        workspaces = self._configuration.get("workspaces")
        if isinstance(workspaces, dict):
            name = workspaces.get("name")
            if name:
                return str(name)
        if isinstance(workspaces, str) and workspaces:
            return workspaces
        name = self._configuration.get("workspace")
        if name:
            return str(name)
        raise LockBackendError(
            "TfcLockBackend: workspace name missing. Set configuration.workspaces.name or configuration.workspace"
        )

    def _get_token(self) -> str:
        token = os.environ.get("TF_TOKEN_app_terraform_io")
        if token:
            return token
        rc_path = Path.home() / ".terraformrc"
        if rc_path.exists():
            try:
                content = rc_path.read_text(encoding="utf-8")
                m = re.search(
                    r'credentials\s+"app\.terraform\.io"\s*\{[^}]*token\s*=\s*"([^"]+)"',
                    content,
                    re.DOTALL,
                )
                if m:
                    return m.group(1)
            except OSError:
                pass
        raise LockBackendError(
            "TfcLockBackend: TFC token not found. Set TF_TOKEN_app_terraform_io or add credentials to ~/.terraformrc"
        )

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    @staticmethod
    def _http(
        method: str,
        url: str,
        token: str,
        body: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, Any]:
        """Execute an HTTP request against the TFC API.

        Returns ``(status_code, parsed_body_or_empty_dict)``.
        """
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/vnd.api+json")
        try:
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = b""
            try:
                raw = exc.read()
            except Exception:  # noqa: BLE001
                pass
            try:
                body_data: Any = json.loads(raw) if raw else {}
            except Exception:  # noqa: BLE001
                body_data = {}
            return exc.code, body_data
        except urllib.error.URLError as exc:
            raise LockBackendError(f"TfcLockBackend: network error: {exc}") from exc

    # ------------------------------------------------------------------
    # Workspace ID resolution (cached)
    # ------------------------------------------------------------------

    def _resolve_workspace_id(self, token: str) -> str:
        if self._workspace_id:
            return self._workspace_id
        org = self._get_org()
        ws_name = self._get_workspace_name()
        status, data = self._http(
            "GET",
            f"{_BASE_URL}/api/v2/organizations/{org}/workspaces/{ws_name}",
            token=token,
        )
        if status != 200:
            raise LockBackendError(f"TfcLockBackend: workspace '{ws_name}' in org '{org}' not found (HTTP {status})")
        self._workspace_id = data["data"]["id"]
        return self._workspace_id

    # ------------------------------------------------------------------
    # Current holder extraction
    # ------------------------------------------------------------------

    def _current_holder(self, ws_id: str, token: str) -> str:
        status, data = self._http("GET", f"{_BASE_URL}/api/v2/workspaces/{ws_id}", token=token)
        if status == 200:
            locked_by = data.get("data", {}).get("attributes", {}).get("locked-by", {})
            if isinstance(locked_by, dict):
                return str(locked_by.get("username") or locked_by.get("name") or "unknown")
            return str(locked_by) if locked_by else "unknown"
        return "unknown"

    # ------------------------------------------------------------------
    # History helpers (local NDJSON — TFC has no lock history API)
    # ------------------------------------------------------------------

    def _history_path(self, deployment_name: str) -> Path:
        return self._locks_dir / f"tfc-{deployment_name}.lock.history"

    def _append_history(self, deployment_name: str, entry: LockEntry) -> None:
        try:
            self._locks_dir.mkdir(parents=True, exist_ok=True)
            with open(self._history_path(deployment_name), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(dataclasses.asdict(entry)) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.warning("tfc_lock.history_write_failed", error=str(exc))

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
        """Lock the TFC workspace.  Polls every 5s until free or timeout."""
        token = self._get_token()
        ws_id = self._resolve_workspace_id(token)
        lock_id = str(uuid.uuid4())
        acquired_at = _now_iso()
        deadline = time.monotonic() + timeout_seconds

        while True:
            status, data = self._http(
                "POST",
                f"{_BASE_URL}/api/v2/workspaces/{ws_id}/actions/lock",
                token=token,
                body={"reason": f"{reason} — {deployment_name} by {holder} [{lock_id}]"},
            )

            if status in (200, 201):
                entry = LockEntry(
                    lock_id=lock_id,
                    deployment=deployment_name,
                    holder=holder,
                    hostname=socket.gethostname(),
                    pid=os.getpid(),
                    acquired_at=acquired_at,
                    expires_at="",  # TFC locks have no TTL
                    reason=reason,
                )
                self._append_history(deployment_name, entry)
                logger.info(
                    "tfc_lock.acquired",
                    deployment=deployment_name,
                    lock_id=lock_id,
                    workspace_id=ws_id,
                )
                return LockHandle(
                    lock_id=lock_id,
                    backend_type=self.BACKEND_TYPE,
                    acquired_at=acquired_at,
                    _backend_data={"workspace_id": ws_id},
                )

            if status == 409:
                current_holder = self._current_holder(ws_id, token)
                if time.monotonic() >= deadline:
                    raise LockTimeoutError(deployment_name, timeout_seconds, current_holder)
                logger.debug(
                    "tfc_lock.waiting",
                    deployment=deployment_name,
                    holder=current_holder,
                    remaining_seconds=int(deadline - time.monotonic()),
                )
                time.sleep(_POLL_INTERVAL)
                continue

            raise LockBackendError(f"TfcLockBackend.acquire: unexpected HTTP {status}")

    def release(self, handle: LockHandle) -> None:
        """Unlock the TFC workspace. Safe to call in a ``finally`` block."""
        ws_id = handle._backend_data.get("workspace_id")
        if not ws_id:
            logger.warning("tfc_lock.release_no_workspace_id", lock_id=handle.lock_id)
            return
        try:
            token = self._get_token()
            status, _ = self._http(
                "POST",
                f"{_BASE_URL}/api/v2/workspaces/{ws_id}/actions/unlock",
                token=token,
            )
            if status not in (200, 201):
                logger.warning(
                    "tfc_lock.release_http_error",
                    lock_id=handle.lock_id,
                    status=status,
                )
            else:
                logger.info("tfc_lock.released", lock_id=handle.lock_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "tfc_lock.release_failed",
                lock_id=handle.lock_id,
                error=str(exc),
            )

    def status(self, deployment_name: str) -> Optional[LockEntry]:
        """Return a ``LockEntry`` if the workspace is locked, or ``None``."""
        try:
            token = self._get_token()
            ws_id = self._resolve_workspace_id(token)
            status_code, data = self._http(
                "GET",
                f"{_BASE_URL}/api/v2/workspaces/{ws_id}",
                token=token,
            )
            if status_code != 200:
                raise LockBackendError(f"TfcLockBackend.status: HTTP {status_code}")
            attrs = data.get("data", {}).get("attributes", {})
            if not attrs.get("locked", False):
                return None
            locked_by = attrs.get("locked-by", {})
            if isinstance(locked_by, dict):
                holder = str(locked_by.get("username") or locked_by.get("name") or "unknown")
            else:
                holder = str(locked_by) if locked_by else "unknown"
            return LockEntry(
                lock_id="tfc-managed",
                deployment=deployment_name,
                holder=holder,
                hostname="app.terraform.io",
                pid=0,
                acquired_at=attrs.get("updated-at", _now_iso()),
                expires_at="",
                reason="TFC workspace lock",
            )
        except LockBackendError:
            raise
        except Exception as exc:
            raise LockBackendError(f"TfcLockBackend.status: {exc}") from exc

    def force_release(self, deployment_name: str) -> None:
        """Force-unlock the TFC workspace (requires owner/admin permissions)."""
        try:
            token = self._get_token()
            ws_id = self._resolve_workspace_id(token)
            # Try force-unlock first; fall back to regular unlock
            status_code, _ = self._http(
                "POST",
                f"{_BASE_URL}/api/v2/workspaces/{ws_id}/actions/force-unlock",
                token=token,
            )
            if status_code not in (200, 201):
                status_code, _ = self._http(
                    "POST",
                    f"{_BASE_URL}/api/v2/workspaces/{ws_id}/actions/unlock",
                    token=token,
                )
            if status_code not in (200, 201):
                raise LockBackendError(f"TfcLockBackend.force_release: HTTP {status_code}")
            logger.warning("tfc_lock.force_released", deployment=deployment_name)
        except LockBackendError:
            raise
        except Exception as exc:
            raise LockBackendError(f"TfcLockBackend.force_release: {exc}") from exc

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
            logger.warning("tfc_lock.history_read_failed", error=str(exc))
        return list(reversed(entries))[:limit]
