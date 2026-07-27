"""Local file-system work-item backend — stores items as JSON in .strata/workitems/."""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

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


class LocalWorkItemBackend(BaseWorkItemBackend):
    """Work-item backend that stores each item as a JSON file under
    ``<work_path>/.strata/workitems/``.

    File permissions are set to 0600 (owner read/write only) to protect
    sensitive context data (plan summaries, cost deltas, CVE lists).
    """

    BACKEND_TYPE = "local"

    def __init__(self, work_path: Path) -> None:
        self._dir = Path(work_path) / ".strata" / "workitems"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _item_path(self, item_id: str) -> Path:
        # Replace slashes in composite IDs with dashes for safe filenames
        safe = item_id.replace("/", "--")
        return self._dir / f"{safe}.json"

    def _write(self, item: WorkItem) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._item_path(item.id)
        try:
            path.write_text(json.dumps(item.to_dict(), indent=2), encoding="utf-8")
            # Restrict to owner read/write; ignore on Windows where chmod is a no-op
            try:
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            except NotImplementedError:
                pass
        except OSError as exc:
            raise WorkItemBackendError(f"Failed to write work item {item.id!r}: {exc}", cause=exc) from exc

    def _read(self, path: Path) -> WorkItem:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return WorkItem.from_dict(data)
        except (OSError, json.JSONDecodeError, TypeError, KeyError) as exc:
            raise WorkItemBackendError(f"Failed to read work item from {path}: {exc}", cause=exc) from exc

    # ------------------------------------------------------------------
    # BaseWorkItemBackend implementation
    # ------------------------------------------------------------------

    def create(self, item: WorkItem) -> WorkItem:
        existing = self.get(item.id)
        if existing is not None:
            raise WorkItemStateError(
                f"Work item {item.id!r} already exists with status {existing.status!r}",
                item_id=item.id,
            )
        self._write(item)
        logger.debug("workitem.created", item_id=item.id, type=item.type, deployment=item.deployment)
        return item

    def get(self, item_id: str) -> Optional[WorkItem]:
        path = self._item_path(item_id)
        if not path.exists():
            return None
        return self._read(path)

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
                f"Cannot resolve work item {item_id!r}: already in terminal state {item.status!r}",
                item_id=item_id,
            )
        if status not in WORKITEM_TERMINAL_STATUSES:
            raise WorkItemStateError(
                f"Invalid resolution status {status!r}. Must be one of: {sorted(WORKITEM_TERMINAL_STATUSES)}",
                item_id=item_id,
            )
        item.status = status
        item.resolved_by = resolved_by
        item.resolved_at = datetime.now(timezone.utc).isoformat()
        item.resolution_note = note
        self._write(item)
        logger.debug("workitem.resolved", item_id=item_id, status=status, resolved_by=resolved_by)
        return item

    def list_items(
        self,
        type: Optional[str] = None,
        status: Optional[str] = None,
        deployment: Optional[str] = None,
    ) -> List[WorkItem]:
        if not self._dir.exists():
            return []

        items: List[WorkItem] = []
        for path in sorted(self._dir.glob("*.json")):
            try:
                item = self._read(path)
            except WorkItemBackendError:
                logger.warning("workitem.read_error", path=str(path))
                continue

            if type is not None and item.type != type:
                continue
            if status is not None and item.status != status:
                continue
            if deployment is not None and item.deployment != deployment:
                continue

            items.append(item)

        # Most-recent first
        items.sort(key=lambda i: i.created_at, reverse=True)
        return items

    def expire_stale(self) -> int:
        """Mark pending items whose expires_at is in the past as 'expired'."""
        now = datetime.now(timezone.utc)
        count = 0
        for item in self.list_items(status=WORKITEM_STATUS_PENDING):
            if item.expires_at is None:
                continue
            try:
                expires = datetime.fromisoformat(item.expires_at)
                if expires <= now:
                    item.status = WORKITEM_STATUS_EXPIRED
                    item.resolved_at = now.isoformat()
                    item.resolution_note = "Automatically expired"
                    self._write(item)
                    count += 1
                    logger.debug("workitem.expired", item_id=item.id)
            except ValueError:
                logger.warning("workitem.invalid_expires_at", item_id=item.id, expires_at=item.expires_at)
        return count
