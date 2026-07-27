"""Git-tag work-item backend — stores each work item as an annotated git tag.

Tag naming: strata-workitem/<type>--<stem>-<commit>-<timestamp>
            (slashes in item IDs replaced with -- for valid tag names)

Each annotated tag's message is the JSON-serialised WorkItem dict.

Multi-user usage: push tags after create/resolve with:
    git push origin refs/tags/strata-workitem/*
"""

from __future__ import annotations

import json
import subprocess
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

_TAG_PREFIX = "strata-workitem/"


def _tag_name(item_id: str) -> str:
    """Convert a work-item ID to a valid git tag name."""
    return _TAG_PREFIX + item_id.replace("/", "--")


def _item_id_from_tag(tag: str) -> str:
    """Reverse _tag_name."""
    return tag.removeprefix(_TAG_PREFIX).replace("--", "/", 1)


class GitTagWorkItemBackend(BaseWorkItemBackend):
    """Work-item backend backed by annotated git tags.

    Items are stored in the git repository at `work_path`. This backend is
    suitable for small teams that want a tamper-evident, distributed record
    without cloud storage infrastructure.

    Limitations vs. LocalWorkItemBackend:
    - Resolving (approve/reject) requires deleting + re-creating the tag.
    - Multi-user access requires pushing tags to a shared remote.
    - GPG signing is not enforced in Phase 2 (tags are annotated but unsigned).
    """

    BACKEND_TYPE = "git_tag"

    def __init__(self, work_path: Path) -> None:
        self._repo = Path(work_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                ["git", *args],
                capture_output=True,
                text=True,
                cwd=str(self._repo),
                check=check,
            )
        except FileNotFoundError as exc:
            raise WorkItemBackendError("git executable not found", cause=exc) from exc
        except subprocess.CalledProcessError as exc:
            raise WorkItemBackendError(
                f"git command failed: {exc.stderr.strip() or exc.stdout.strip()}",
                cause=exc,
            ) from exc

    def _write_tag(self, item: WorkItem) -> None:
        """Create or overwrite the annotated tag for a work item."""
        tag = _tag_name(item.id)
        msg = json.dumps(item.to_dict(), indent=2)

        # Delete existing tag if present (needed for resolve — update in place)
        self._run_git("tag", "-d", tag, check=False)

        # Create new annotated tag at HEAD
        self._run_git("tag", "-a", tag, "-m", msg)
        logger.debug("workitem.git_tag_written", tag=tag, status=item.status)

    def _read_tag(self, tag: str) -> Optional[WorkItem]:
        """Read a work item from an annotated git tag message."""
        result = self._run_git("cat-file", "tag", tag, check=False)
        if result.returncode != 0:
            return None
        # Annotated tag format: header lines then blank line then message
        lines = result.stdout.split("\n")
        try:
            blank_idx = lines.index("")
        except ValueError:
            blank_idx = 0
        payload = "\n".join(lines[blank_idx + 1 :]).strip()
        if not payload:
            return None
        try:
            data = json.loads(payload)
            return WorkItem.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("workitem.git_tag_parse_error", tag=tag, error=str(exc))
            return None

    def _list_tags(self) -> List[str]:
        result = self._run_git("tag", "-l", f"{_TAG_PREFIX}*", check=False)
        if result.returncode != 0:
            return []
        return [t.strip() for t in result.stdout.splitlines() if t.strip()]

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
        self._write_tag(item)
        logger.debug("workitem.created", item_id=item.id, backend=self.BACKEND_TYPE)
        return item

    def get(self, item_id: str) -> Optional[WorkItem]:
        return self._read_tag(_tag_name(item_id))

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
            raise WorkItemStateError(
                f"Invalid resolution status {status!r}.",
                item_id=item_id,
            )
        item.status = status
        item.resolved_by = resolved_by
        item.resolved_at = datetime.now(timezone.utc).isoformat()
        item.resolution_note = note
        self._write_tag(item)
        return item

    def list_items(
        self,
        type: Optional[str] = None,
        status: Optional[str] = None,
        deployment: Optional[str] = None,
    ) -> List[WorkItem]:
        items: List[WorkItem] = []
        for tag in self._list_tags():
            item = self._read_tag(tag)
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
                    self._write_tag(item)
                    count += 1
            except ValueError:
                pass
        return count
