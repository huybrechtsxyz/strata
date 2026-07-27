"""Azure Blob Storage work-item backend — stores each item as a JSON blob.

Uses az CLI for token acquisition; REST calls via urllib.request.
No azure-storage-blob SDK dependency — same pattern as AzurermLockBackend.

Blob path: {container}/strata-workitems/{safe_item_id}.json
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from strata.integrations.workitem.base_workitem_backend import (
    WORKITEM_STATUS_EXPIRED,
    WORKITEM_STATUS_PENDING,
    WORKITEM_TERMINAL_STATUSES,
    BaseWorkItemBackend,
    WorkItem,
    WorkItemBackendError,
    WorkItemNotFoundError,
    WorkItemStateError,
)
from strata.logger import get_logger

logger = get_logger(__name__)

_STORAGE_RESOURCE = "https://storage.azure.com"
_API_VERSION = "2020-08-04"
_HTTP_TIMEOUT = 30
_ITEM_PREFIX = "strata-workitems"


class AzureBlobWorkItemBackend(BaseWorkItemBackend):
    """Work-item backend backed by Azure Blob Storage.

    Configuration fields:
      storage_account_name  (required) — Azure Storage account name
      container_name        (required) — blob container name
    """

    BACKEND_TYPE = "azblob"

    def __init__(self, configuration: Dict[str, Any], work_path: Path) -> None:
        self._configuration = configuration
        self._work_path = Path(work_path)
        self._token_cache: Optional[Tuple[str, float]] = None  # (token, expiry_ts)

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def _get_storage_account(self) -> str:
        sa = self._configuration.get("storage_account_name")
        if not sa:
            raise WorkItemBackendError("AzureBlobWorkItemBackend: 'storage_account_name' missing")
        return str(sa)

    def _get_container(self) -> str:
        container = self._configuration.get("container_name")
        if not container:
            raise WorkItemBackendError("AzureBlobWorkItemBackend: 'container_name' missing")
        return str(container)

    def _blob_url(self, item_id: str) -> str:
        sa = self._get_storage_account()
        container = self._get_container()
        safe_id = item_id.replace("/", "--")
        return f"https://{sa}.blob.core.windows.net/{container}/{_ITEM_PREFIX}/{safe_id}.json"

    def _container_url(self) -> str:
        sa = self._get_storage_account()
        container = self._get_container()
        return f"https://{sa}.blob.core.windows.net/{container}"

    # ------------------------------------------------------------------
    # Auth: cached az CLI token
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
        if self._token_cache:
            token, expiry = self._token_cache
            if time.time() < expiry - 60:
                return token
        try:
            result = subprocess.run(
                ["az", "account", "get-access-token", "--resource", _STORAGE_RESOURCE, "--output", "json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError as exc:
            raise WorkItemBackendError("AzureBlobWorkItemBackend: 'az' CLI not found in PATH", cause=exc) from exc
        if result.returncode != 0:
            raise WorkItemBackendError(f"AzureBlobWorkItemBackend: az get-access-token failed: {result.stderr.strip()}")
        data = json.loads(result.stdout)
        token = data.get("accessToken", "")
        if not token:
            raise WorkItemBackendError("AzureBlobWorkItemBackend: empty accessToken from az CLI")
        expires_on = data.get("expiresOn", "")
        try:
            expiry = datetime.strptime(expires_on[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
        except (ValueError, IndexError):
            expiry = time.time() + 3600
        self._token_cache = (token, expiry)
        return token

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        url: str,
        token: str,
        body: Optional[bytes] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, bytes]:
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("x-ms-version", _API_VERSION)
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
            except Exception:
                pass
            return exc.code, raw
        except urllib.error.URLError as exc:
            raise WorkItemBackendError(f"AzureBlobWorkItemBackend: network error: {exc}", cause=exc) from exc

    # ------------------------------------------------------------------
    # BaseWorkItemBackend implementation
    # ------------------------------------------------------------------

    def create(self, item: WorkItem) -> WorkItem:
        token = self._get_token()
        url = self._blob_url(item.id)

        # Check for duplicate
        status, _ = self._request("HEAD", url, token)
        if status == 200:
            existing = self.get(item.id)
            raise WorkItemStateError(
                f"Work item {item.id!r} already exists with status {existing.status!r if existing else '?'}",
                item_id=item.id,
            )

        body = json.dumps(item.to_dict(), indent=2).encode()
        status, resp = self._request(
            "PUT",
            url,
            token,
            body=body,
            extra_headers={
                "Content-Type": "application/json",
                "x-ms-blob-type": "BlockBlob",
            },
        )
        if status not in (200, 201):
            raise WorkItemBackendError(
                f"AzureBlobWorkItemBackend.create: HTTP {status}: {resp.decode(errors='replace')[:200]}"
            )
        logger.debug("azblob_workitem.created", item_id=item.id)
        return item

    def get(self, item_id: str) -> Optional[WorkItem]:
        try:
            token = self._get_token()
            url = self._blob_url(item_id)
            status, body = self._request("GET", url, token)
            if status == 404:
                return None
            if status != 200 or not body:
                return None
            return WorkItem.from_dict(json.loads(body))
        except (json.JSONDecodeError, KeyError, TypeError, WorkItemBackendError):
            return None

    def resolve(
        self,
        item_id: str,
        status: str,
        resolved_by: str,
        note: Optional[str] = None,
    ) -> WorkItem:
        item = self.get(item_id)
        if item is None:
            raise WorkItemNotFoundError(item_id)
        if item.status != WORKITEM_STATUS_PENDING:
            raise WorkItemStateError(f"Cannot resolve {item_id!r}: already {item.status!r}", item_id=item_id)
        if status not in WORKITEM_TERMINAL_STATUSES:
            raise WorkItemStateError(f"Invalid resolution status {status!r}", item_id=item_id)

        item.status = status
        item.resolved_by = resolved_by
        item.resolved_at = datetime.now(timezone.utc).isoformat()
        item.resolution_note = note

        token = self._get_token()
        url = self._blob_url(item_id)
        body = json.dumps(item.to_dict(), indent=2).encode()
        http_status, resp = self._request(
            "PUT",
            url,
            token,
            body=body,
            extra_headers={
                "Content-Type": "application/json",
                "x-ms-blob-type": "BlockBlob",
            },
        )
        if http_status not in (200, 201):
            raise WorkItemBackendError(
                f"AzureBlobWorkItemBackend.resolve: HTTP {http_status}: {resp.decode(errors='replace')[:200]}"
            )
        logger.debug("azblob_workitem.resolved", item_id=item_id, status=status)
        return item

    def list_items(
        self,
        type: Optional[str] = None,
        status: Optional[str] = None,
        deployment: Optional[str] = None,
    ) -> List[WorkItem]:
        token = self._get_token()
        # List blobs with the strata-workitems/ prefix
        list_url = (
            f"{self._container_url()}?restype=container&comp=list"
            f"&prefix={urllib.parse.quote(_ITEM_PREFIX + '/', safe='')}"
        )
        http_status, body = self._request("GET", list_url, token)
        if http_status != 200:
            return []

        # Parse XML response to extract blob names
        names: List[str] = []
        try:
            root = ET.fromstring(body.decode("utf-8", errors="replace"))
            for blob in root.findall(".//Blob/Name"):
                if blob.text:
                    names.append(blob.text)
        except ET.ParseError:
            return []

        items: List[WorkItem] = []
        for name in names:
            # Extract item_id from blob name: strata-workitems/{safe_id}.json
            basename = name.rsplit("/", 1)[-1]
            if basename.endswith(".json"):
                basename = basename[:-5]
            item_id = basename.replace("--", "/", 1)

            item = self.get(item_id)
            if item is None:
                continue
            if type is not None and item.type != type:
                continue
            if status is not None and item.status != status:
                continue
            if deployment is not None and item.deployment != deployment:
                continue
            items.append(item)

        items.sort(key=lambda i: i.created_at, reverse=True)
        return items

    def expire_stale(self) -> int:
        now = datetime.now(timezone.utc)
        count = 0
        for item in self.list_items(status=WORKITEM_STATUS_PENDING):
            if item.expires_at is None:
                continue
            try:
                if datetime.fromisoformat(item.expires_at) <= now:
                    item.status = WORKITEM_STATUS_EXPIRED
                    item.resolved_at = now.isoformat()
                    item.resolution_note = "Automatically expired"
                    token = self._get_token()
                    url = self._blob_url(item.id)
                    body = json.dumps(item.to_dict(), indent=2).encode()
                    self._request(
                        "PUT",
                        url,
                        token,
                        body=body,
                        extra_headers={
                            "Content-Type": "application/json",
                            "x-ms-blob-type": "BlockBlob",
                        },
                    )
                    count += 1
            except (ValueError, WorkItemBackendError):
                pass
        return count
