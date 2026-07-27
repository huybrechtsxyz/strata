"""AWS S3 work-item backend — stores each item as a JSON object in S3.

Uses the `aws` CLI subprocess (no boto3 dependency — same pattern as S3LockBackend).
Auth: standard AWS credential chain, resolved by the `aws` CLI.

Object key pattern: {key_prefix}/strata-workitems/{safe_item_id}.json
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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

_ITEM_PREFIX = "strata-workitems"
_CLI_TIMEOUT = 30


class S3WorkItemBackend(BaseWorkItemBackend):
    """Work-item backend backed by AWS S3 objects.

    Configuration fields:
      bucket  (required) — S3 bucket name
      key     (optional) — key prefix within the bucket
      region  (optional) — AWS region (falls back to CLI default)
    """

    BACKEND_TYPE = "s3"

    def __init__(self, configuration: Dict[str, Any], work_path: Path) -> None:
        self._configuration = configuration
        self._work_path = Path(work_path)

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def _get_bucket(self) -> str:
        bucket = self._configuration.get("bucket")
        if not bucket:
            raise WorkItemBackendError("S3WorkItemBackend: 'bucket' missing from backend configuration")
        return str(bucket)

    def _region_args(self) -> List[str]:
        region = self._configuration.get("region")
        return ["--region", str(region)] if region else []

    def _object_key(self, item_id: str) -> str:
        safe_id = item_id.replace("/", "--")
        prefix = self._configuration.get("key", "").strip("/")
        if prefix:
            return f"{prefix}/{_ITEM_PREFIX}/{safe_id}.json"
        return f"{_ITEM_PREFIX}/{safe_id}.json"

    def _item_id_from_key(self, key: str) -> str:
        """Extract item_id from an S3 object key."""
        basename = key.rsplit("/", 1)[-1]
        if basename.endswith(".json"):
            basename = basename[:-5]
        return basename.replace("--", "/", 1)

    # ------------------------------------------------------------------
    # AWS CLI subprocess helper
    # ------------------------------------------------------------------

    def _aws(self, args: List[str], input_bytes: Optional[bytes] = None) -> subprocess.CompletedProcess:
        cmd = ["aws", "--output", "json"] + self._region_args() + args
        try:
            return subprocess.run(cmd, input=input_bytes, capture_output=True, timeout=_CLI_TIMEOUT)
        except FileNotFoundError as exc:
            raise WorkItemBackendError("S3WorkItemBackend: 'aws' CLI not found in PATH", cause=exc) from exc
        except subprocess.TimeoutExpired as exc:
            raise WorkItemBackendError(
                f"S3WorkItemBackend: aws CLI timed out after {_CLI_TIMEOUT}s", cause=exc
            ) from exc

    # ------------------------------------------------------------------
    # Read/write helpers
    # ------------------------------------------------------------------

    def _read_object(self, bucket: str, key: str) -> Optional[WorkItem]:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            result = self._aws(["s3api", "get-object", "--bucket", bucket, "--key", key, tmp_path])
            if result.returncode != 0:
                return None
            with open(tmp_path, encoding="utf-8") as fh:
                return WorkItem.from_dict(json.load(fh))
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("s3_workitem.read_error", bucket=bucket, key=key, error=str(exc))
            return None
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _write_object(self, bucket: str, key: str, item: WorkItem) -> None:
        body = json.dumps(item.to_dict(), indent=2).encode()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp.write(body)
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
                ]
            )
            if result.returncode != 0:
                raise WorkItemBackendError(
                    f"S3WorkItemBackend: put-object failed: {result.stderr.decode(errors='replace')[:200]}"
                )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _list_keys(self, bucket: str) -> List[str]:
        """List all object keys under the strata-workitems prefix."""
        prefix_parts = [p for p in [self._configuration.get("key", "").strip("/"), _ITEM_PREFIX] if p]
        prefix = "/".join(prefix_parts) + "/"

        keys: List[str] = []
        continuation_token: Optional[str] = None

        while True:
            args = ["s3api", "list-objects-v2", "--bucket", bucket, "--prefix", prefix]
            if continuation_token:
                args += ["--starting-token", continuation_token]

            result = self._aws(args)
            if result.returncode != 0:
                break

            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError:
                break

            for obj in data.get("Contents", []):
                if k := obj.get("Key"):
                    keys.append(k)

            if data.get("IsTruncated"):
                continuation_token = data.get("NextContinuationToken")
            else:
                break

        return keys

    # ------------------------------------------------------------------
    # BaseWorkItemBackend implementation
    # ------------------------------------------------------------------

    def create(self, item: WorkItem) -> WorkItem:
        bucket = self._get_bucket()
        key = self._object_key(item.id)

        # Check for duplicate (conditional create is not atomic in S3 for this use case;
        # we accept last-write-wins for Phase 3 — Phase 4 can add conditional PUT)
        existing = self._read_object(bucket, key)
        if existing is not None:
            raise WorkItemStateError(
                f"Work item {item.id!r} already exists with status {existing.status!r}",
                item_id=item.id,
            )
        self._write_object(bucket, key, item)
        logger.debug("s3_workitem.created", item_id=item.id, bucket=bucket, key=key)
        return item

    def get(self, item_id: str) -> Optional[WorkItem]:
        bucket = self._get_bucket()
        return self._read_object(bucket, self._object_key(item_id))

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
            raise WorkItemStateError(
                f"Cannot resolve {item_id!r}: already {item.status!r}",
                item_id=item_id,
            )
        if status not in WORKITEM_TERMINAL_STATUSES:
            raise WorkItemStateError(f"Invalid resolution status {status!r}", item_id=item_id)

        item.status = status
        item.resolved_by = resolved_by
        item.resolved_at = datetime.now(timezone.utc).isoformat()
        item.resolution_note = note

        bucket = self._get_bucket()
        self._write_object(bucket, self._object_key(item_id), item)
        logger.debug("s3_workitem.resolved", item_id=item_id, status=status)
        return item

    def list_items(
        self,
        type: Optional[str] = None,
        status: Optional[str] = None,
        deployment: Optional[str] = None,
    ) -> List[WorkItem]:
        bucket = self._get_bucket()
        items: List[WorkItem] = []

        for key in self._list_keys(bucket):
            item = self._read_object(bucket, key)
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
                    bucket = self._get_bucket()
                    self._write_object(bucket, self._object_key(item.id), item)
                    count += 1
            except ValueError:
                pass
        return count
