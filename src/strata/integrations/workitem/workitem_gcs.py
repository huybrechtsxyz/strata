"""GCP Cloud Storage work-item backend — stores each item as a JSON object in GCS.

Uses gcloud CLI for token acquisition; REST calls via urllib.request.
No google-cloud-storage SDK dependency — same pattern as GcsLockBackend.

Object path: gs://{bucket}/{prefix}/strata-workitems/{safe_item_id}.json
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
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

_HTTP_TIMEOUT = 30
_CLI_TIMEOUT = 30
_ITEM_PREFIX = "strata-workitems"
_GCS_UPLOAD_URL = "https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
_GCS_OBJECT_URL = "https://storage.googleapis.com/storage/v1/b/{bucket}/o/{object}"
_GCS_LIST_URL = "https://storage.googleapis.com/storage/v1/b/{bucket}/o"


def _encode(name: str) -> str:
    return urllib.parse.quote(name, safe="")


class GCSWorkItemBackend(BaseWorkItemBackend):
    """Work-item backend backed by GCP Cloud Storage.

    Configuration fields:
      bucket  (required) — GCS bucket name
      prefix  (optional) — key prefix within the bucket
    """

    BACKEND_TYPE = "gcs"

    def __init__(self, configuration: Dict[str, Any], work_path: Path) -> None:
        self._configuration = configuration
        self._work_path = Path(work_path)
        self._token_cache: Optional[Tuple[str, float]] = None  # (token, expiry_ts)

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def _get_bucket(self) -> str:
        bucket = self._configuration.get("bucket")
        if not bucket:
            raise WorkItemBackendError("GCSWorkItemBackend: 'bucket' missing from backend configuration")
        return str(bucket)

    def _object_name(self, item_id: str) -> str:
        safe_id = item_id.replace("/", "--")
        prefix = self._configuration.get("prefix", "").strip("/")
        if prefix:
            return f"{prefix}/{_ITEM_PREFIX}/{safe_id}.json"
        return f"{_ITEM_PREFIX}/{safe_id}.json"

    def _item_prefix(self) -> str:
        prefix = self._configuration.get("prefix", "").strip("/")
        if prefix:
            return f"{prefix}/{_ITEM_PREFIX}/"
        return f"{_ITEM_PREFIX}/"

    # ------------------------------------------------------------------
    # Auth: cached gcloud token
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
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
            raise WorkItemBackendError("GCSWorkItemBackend: 'gcloud' CLI not found in PATH", cause=exc) from exc
        except subprocess.TimeoutExpired as exc:
            raise WorkItemBackendError(
                f"GCSWorkItemBackend: gcloud timed out after {_CLI_TIMEOUT}s", cause=exc
            ) from exc
        if result.returncode != 0:
            raise WorkItemBackendError(f"GCSWorkItemBackend: gcloud auth failed: {result.stderr.strip()}")
        token = result.stdout.strip()
        if not token:
            raise WorkItemBackendError("GCSWorkItemBackend: empty token from gcloud auth print-access-token")
        self._token_cache = (token, time.time() + 3300)  # cache ~55 min
        return token

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    def _gcs_request(
        self,
        method: str,
        url: str,
        token: str,
        body: Optional[bytes] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, bytes]:
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
            except Exception:
                pass
            return exc.code, raw
        except urllib.error.URLError as exc:
            raise WorkItemBackendError(f"GCSWorkItemBackend: network error: {exc}", cause=exc) from exc

    # ------------------------------------------------------------------
    # Read/write helpers
    # ------------------------------------------------------------------

    def _read_object(self, bucket: str, obj_name: str, token: str) -> Optional[WorkItem]:
        url = _GCS_OBJECT_URL.format(bucket=bucket, object=_encode(obj_name)) + "?alt=media"
        status, body = self._gcs_request("GET", url, token)
        if status == 404:
            return None
        if status != 200 or not body:
            return None
        try:
            return WorkItem.from_dict(json.loads(body))
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def _write_object(self, bucket: str, obj_name: str, item: WorkItem, token: str) -> None:
        body = json.dumps(item.to_dict(), indent=2).encode()
        upload_url = _GCS_UPLOAD_URL.format(bucket=bucket) + f"?uploadType=media&name={_encode(obj_name)}"
        status, resp = self._gcs_request(
            "POST",
            upload_url,
            token,
            body=body,
            extra_headers={"Content-Type": "application/json"},
        )
        if status not in (200, 201):
            raise WorkItemBackendError(
                f"GCSWorkItemBackend: upload failed HTTP {status}: {resp.decode(errors='replace')[:200]}"
            )

    # ------------------------------------------------------------------
    # BaseWorkItemBackend implementation
    # ------------------------------------------------------------------

    def create(self, item: WorkItem) -> WorkItem:
        token = self._get_token()
        bucket = self._get_bucket()
        obj_name = self._object_name(item.id)

        # Check for duplicate
        existing = self._read_object(bucket, obj_name, token)
        if existing is not None:
            raise WorkItemStateError(
                f"Work item {item.id!r} already exists with status {existing.status!r}",
                item_id=item.id,
            )
        self._write_object(bucket, obj_name, item, token)
        logger.debug("gcs_workitem.created", item_id=item.id, bucket=bucket)
        return item

    def get(self, item_id: str) -> Optional[WorkItem]:
        try:
            token = self._get_token()
            bucket = self._get_bucket()
            return self._read_object(bucket, self._object_name(item_id), token)
        except WorkItemBackendError:
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
        bucket = self._get_bucket()
        self._write_object(bucket, self._object_name(item_id), item, token)
        logger.debug("gcs_workitem.resolved", item_id=item_id, status=status)
        return item

    def list_items(
        self,
        type: Optional[str] = None,
        status: Optional[str] = None,
        deployment: Optional[str] = None,
    ) -> List[WorkItem]:
        try:
            token = self._get_token()
            bucket = self._get_bucket()
        except WorkItemBackendError:
            return []

        list_url = (
            _GCS_LIST_URL.format(bucket=bucket)
            + f"?prefix={_encode(self._item_prefix())}&fields=items(name),nextPageToken"
        )

        names: List[str] = []
        page_token: Optional[str] = None

        while True:
            url = list_url + (f"&pageToken={_encode(page_token)}" if page_token else "")
            http_status, body = self._gcs_request("GET", url, token)
            if http_status != 200:
                break
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                break

            for obj in data.get("items", []):
                if name := obj.get("name"):
                    names.append(name)

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        items: List[WorkItem] = []
        for name in names:
            # Extract item_id from object name
            basename = name.rsplit("/", 1)[-1]
            if basename.endswith(".json"):
                basename = basename[:-5]
            item_id = basename.replace("--", "/", 1)

            item = self._read_object(bucket, name, token)
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
                    bucket = self._get_bucket()
                    self._write_object(bucket, self._object_name(item.id), item, token)
                    count += 1
            except (ValueError, WorkItemBackendError):
                pass
        return count
