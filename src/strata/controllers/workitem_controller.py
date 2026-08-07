"""WorkItemController — orchestrates work-item lifecycle. ADR-0057."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from strata.controllers.actor_controller import resolve_actor
from strata.integrations.workitem.base_workitem_backend import (
    WORKITEM_STATUS_APPROVED,
    WORKITEM_STATUS_CANCELLED,
    WORKITEM_STATUS_COMPLETED,
    WORKITEM_STATUS_EXPIRED,
    WORKITEM_STATUS_PENDING,
    WORKITEM_STATUS_REJECTED,
    BaseWorkItemBackend,
    WorkItem,
    WorkItemCommitMismatchError,
    WorkItemNotFoundError,
    WorkItemStateError,
)
from strata.logger import audit, get_logger

logger = get_logger(__name__)


def _get_identity() -> str:
    """Resolve the current operator identity — delegates to the shared actor-resolution chain."""
    return resolve_actor()


class WorkItemController:
    """Orchestrates work-item creation, resolution, and lifecycle management."""

    def __init__(self, backend: BaseWorkItemBackend) -> None:
        self._backend = backend

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def local(cls, work_path: Path) -> "WorkItemController":
        """Create a controller backed by the local file-system backend."""
        from strata.integrations.workitem.workitem_local import LocalWorkItemBackend

        return cls(LocalWorkItemBackend(work_path))

    @classmethod
    def from_config(
        cls,
        work_path: Path,
        backend_type: Optional[str] = None,
        configuration: Optional[dict] = None,
    ) -> "WorkItemController":
        """Create a controller using the factory — resolves backend from type + config."""
        from strata.integrations.workitem.workitem_factory import WorkItemBackendFactory

        backend = WorkItemBackendFactory.create(work_path, backend_type, configuration)
        return cls(backend)

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def request(
        self,
        type: str,
        deployment: str,
        commit: str,
        requester: Optional[str] = None,
        context: Optional[dict] = None,
        expires_minutes: Optional[int] = None,
    ) -> WorkItem:
        """Create a new pending work item and return it."""
        now = datetime.now(timezone.utc)
        identity = requester or _get_identity()

        # Composite ID: <type>/<stem>-<short-commit>-<timestamp>
        stem = Path(deployment).stem
        short_commit = (commit or "unknown")[:8]
        ts = now.strftime("%Y%m%dT%H%M")
        item_id = f"{type}/{stem}-{short_commit}-{ts}"

        expires_at: Optional[str] = None
        if expires_minutes:
            expires_at = (now + timedelta(minutes=expires_minutes)).isoformat()

        item = WorkItem(
            id=item_id,
            type=type,
            status=WORKITEM_STATUS_PENDING,
            deployment=deployment,
            commit=commit or "",
            created_by=identity,
            created_at=now.isoformat(),
            expires_at=expires_at,
            context=context or {},
        )
        item = self._backend.create(item)
        audit(
            "workitem.created",
            outcome="success",
            target=item.id,
            detail={"type": item.type, "deployment": item.deployment, "commit": item.commit[:8]},
        )
        return item

    def resolve(
        self,
        item_id: str,
        status: str,
        resolver: Optional[str] = None,
        note: Optional[str] = None,
    ) -> WorkItem:
        """Transition a work item to a terminal status."""
        result = self._backend.resolve(
            item_id=item_id,
            status=status,
            resolved_by=resolver or _get_identity(),
            note=note,
        )
        audit(
            f"workitem.{status}",
            outcome="success",
            target=item_id,
            detail={"resolved_by": result.resolved_by, "note": note},
        )
        return result

    def approve(self, item_id: str, note: Optional[str] = None) -> WorkItem:
        return self.resolve(item_id, WORKITEM_STATUS_APPROVED, note=note)

    def reject(self, item_id: str, reason: Optional[str] = None) -> WorkItem:
        return self.resolve(item_id, WORKITEM_STATUS_REJECTED, note=reason)

    def complete(self, item_id: str, comment: Optional[str] = None) -> WorkItem:
        return self.resolve(item_id, WORKITEM_STATUS_COMPLETED, note=comment)

    def cancel(self, item_id: str, reason: Optional[str] = None) -> WorkItem:
        return self.resolve(item_id, WORKITEM_STATUS_CANCELLED, note=reason)

    def get(self, item_id: str) -> Optional[WorkItem]:
        return self._backend.get(item_id)

    def list_pending(self, type: Optional[str] = None) -> List[WorkItem]:
        return self._backend.list_items(type=type, status=WORKITEM_STATUS_PENDING)

    def list_items(
        self,
        type: Optional[str] = None,
        status: Optional[str] = None,
        deployment: Optional[str] = None,
    ) -> List[WorkItem]:
        return self._backend.list_items(type=type, status=status, deployment=deployment)

    def expire_stale(self) -> int:
        return self._backend.expire_stale()

    # ------------------------------------------------------------------
    # Resume verification — called by deploy run --resume
    # ------------------------------------------------------------------

    def verify_resolved(
        self,
        item_id: str,
        expected_type: str,
        expected_commit: str,
    ) -> WorkItem:
        """Verify a work item is validly resolved for deploy-resume. Raises on any problem."""
        item = self._backend.get(item_id)
        if item is None:
            raise WorkItemNotFoundError(item_id)

        if item.status == WORKITEM_STATUS_PENDING:
            raise WorkItemStateError(
                f"Work item {item_id!r} is still pending — it must be approved before resuming.",
                item_id=item_id,
            )
        if item.status == WORKITEM_STATUS_EXPIRED:
            raise WorkItemStateError(
                f"Work item {item_id!r} has expired. Create a new deployment run.",
                item_id=item_id,
            )
        if item.status in (WORKITEM_STATUS_REJECTED, WORKITEM_STATUS_CANCELLED):
            raise WorkItemStateError(
                f"Work item {item_id!r} was {item.status}. Cannot resume this deployment.",
                item_id=item_id,
            )
        if item.type != expected_type:
            raise WorkItemStateError(
                f"Work item {item_id!r} type mismatch: expected {expected_type!r}, got {item.type!r}",
                item_id=item_id,
            )
        if expected_commit and not item.commit.startswith(expected_commit[:8]):
            raise WorkItemCommitMismatchError(item_id, expected_commit, item.commit)

        return item
