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
from strata.logger import get_logger

logger = get_logger(__name__)


def _get_identity() -> str:
    """Resolve the current operator identity — delegates to the shared actor-resolution chain."""
    return resolve_actor()


class WorkItemController:
    """Orchestrates work-item creation, resolution, and lifecycle management."""

    def __init__(self, backend: BaseWorkItemBackend, work_path: Optional[Path] = None) -> None:
        self._backend = backend
        # Needed to construct AuditController for forward()ing resolution events
        # (ADR-0066 gap A) — optional only for backward compatibility with any
        # direct, non-factory construction; forwarding is skipped (logged debug)
        # if absent rather than raising.
        self._work_path = work_path

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def local(cls, work_path: Path) -> "WorkItemController":
        """Create a controller backed by the local file-system backend."""
        from strata.integrations.workitem.workitem_local import LocalWorkItemBackend

        return cls(LocalWorkItemBackend(work_path), work_path=work_path)

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
        return cls(backend, work_path=work_path)

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
        # No raw audit() call here (unlike pre-ADR-0066): request() is only ever
        # invoked via WorkItemGateController.evaluate_and_create(), itself only
        # ever called from RunDeployCommand's gate-evaluation flow, which already
        # forwards "workitem.created" properly through AuditController.forward()
        # via _forward_workitem_event() right after this returns. A raw journal
        # write here would double-fire the same event through two separate,
        # divergent code paths (one gated/enveloped/sink-forwarded, one not).
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
        self._forward_resolution_event(status, result, note)
        return result

    def _forward_resolution_event(self, status: str, item: WorkItem, note: Optional[str]) -> None:
        """Forward a ``workitem.<status>`` event through the ADR-0066 pipeline.

        Best-effort — resolution must never fail because auditing failed. Resolves
        ``AuditConfigModel`` from the already-populated ``ConfigurationService``
        singleton (matching ``forward_policy_violation()`` / ``_forward_lock_audit_event()``),
        since approve/reject/complete/cancel are independent CLI invocations with no
        command object to pass a resolved config through. No ``execution_id`` is
        available or expected here — these are not tied to a parent deploy's
        execution; ``AuditController._build_envelope()`` already falls back to a
        fresh UUID when it is absent.
        """
        if self._work_path is None:
            logger.debug(f"Skipping workitem.{status} forward — no work_path on this controller")
            return
        try:
            from strata.controllers.audit_controller import AuditController
            from strata.services.configuration_service import ConfigurationService

            audit_cfg = None
            try:
                config_model = ConfigurationService.get_instance().model
                audit_cfg = getattr(getattr(config_model, "spec", None), "audit", None)
            except Exception as e:
                logger.debug(f"Failed to resolve spec.audit for workitem.{status} (non-fatal): {e}")

            payload = {
                "item_id": item.id,
                "type": item.type,
                "deployment": item.deployment,
                "commit": item.commit[:8] if item.commit else None,
                "resolved_by": item.resolved_by,
                "note": note,
            }
            AuditController(work_path=self._work_path).forward(f"workitem.{status}", payload, audit_config=audit_cfg)
        except Exception as e:
            logger.debug(f"Failed to forward workitem.{status} audit event (non-fatal): {e}")

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
