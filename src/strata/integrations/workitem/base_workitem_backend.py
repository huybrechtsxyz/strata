"""Abstract base for work-item backends — ADR-0057."""

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from strata.exceptions.base_exception import PlatformError

# ---------------------------------------------------------------------------
# Work-item types
# ---------------------------------------------------------------------------

WORKITEM_TYPE_APPROVAL = "approval"
WORKITEM_TYPE_COST_REVIEW = "cost_review"
WORKITEM_TYPE_SECURITY_REVIEW = "security_review"
WORKITEM_TYPE_VERIFY = "verify"
WORKITEM_TYPE_SCHEDULED = "scheduled"
WORKITEM_TYPE_INCIDENT = "incident"
WORKITEM_TYPE_CAB = "cab"
WORKITEM_TYPE_PROMOTION_GATE = "promotion_gate"
WORKITEM_TYPE_DRIFT_DECISION = "drift_decision"
WORKITEM_TYPE_ROLLBACK = "rollback"

WORKITEM_TYPES = [
    WORKITEM_TYPE_APPROVAL,
    WORKITEM_TYPE_COST_REVIEW,
    WORKITEM_TYPE_SECURITY_REVIEW,
    WORKITEM_TYPE_VERIFY,
    WORKITEM_TYPE_SCHEDULED,
    WORKITEM_TYPE_INCIDENT,
    WORKITEM_TYPE_CAB,
    WORKITEM_TYPE_PROMOTION_GATE,
    WORKITEM_TYPE_DRIFT_DECISION,
    WORKITEM_TYPE_ROLLBACK,
]

# ---------------------------------------------------------------------------
# Work-item statuses
# ---------------------------------------------------------------------------

WORKITEM_STATUS_PENDING = "pending"
WORKITEM_STATUS_APPROVED = "approved"
WORKITEM_STATUS_REJECTED = "rejected"
WORKITEM_STATUS_COMPLETED = "completed"
WORKITEM_STATUS_EXPIRED = "expired"
WORKITEM_STATUS_CANCELLED = "cancelled"

WORKITEM_TERMINAL_STATUSES = {
    WORKITEM_STATUS_APPROVED,
    WORKITEM_STATUS_REJECTED,
    WORKITEM_STATUS_COMPLETED,
    WORKITEM_STATUS_EXPIRED,
    WORKITEM_STATUS_CANCELLED,
}

# ---------------------------------------------------------------------------
# Work-item dataclass
# ---------------------------------------------------------------------------


@dataclass
class WorkItem:
    """A pending hand-off point in the deployment pipeline."""

    id: str  # e.g. "approval/haven-prd-a1b2c3d-20260727T1430"
    type: str  # one of WORKITEM_TYPES
    status: str  # one of WORKITEM_STATUS_*
    deployment: str  # path to deployment YAML
    commit: str  # git commit SHA that triggered this item
    created_by: str  # identity of the requester
    created_at: str  # ISO 8601 UTC
    expires_at: Optional[str] = None  # ISO 8601 UTC — None means no expiry
    resolved_by: Optional[str] = None
    resolved_at: Optional[str] = None
    resolution_note: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkItem":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    @property
    def is_pending(self) -> bool:
        return self.status == WORKITEM_STATUS_PENDING

    @property
    def is_terminal(self) -> bool:
        return self.status in WORKITEM_TERMINAL_STATUSES

    @property
    def short_id(self) -> str:
        """Last segment of the composite ID for display purposes."""
        return self.id.split("/")[-1] if "/" in self.id else self.id


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class WorkItemError(PlatformError):
    """Base error for all work-item backend operations."""


class WorkItemNotFoundError(WorkItemError):
    def __init__(self, item_id: str) -> None:
        super().__init__(
            message=f"Work item not found: {item_id!r}",
            error_code="WORKITEM_NOT_FOUND",
            details={"item_id": item_id},
        )


class WorkItemStateError(WorkItemError):
    def __init__(self, message: str, item_id: Optional[str] = None) -> None:
        super().__init__(
            message=message,
            error_code="WORKITEM_STATE_ERROR",
            details={"item_id": item_id} if item_id else {},
        )


class WorkItemCommitMismatchError(WorkItemError):
    def __init__(self, item_id: str, expected: str, actual: str) -> None:
        super().__init__(
            message=(
                f"Work item {item_id!r} commit mismatch — replay attack prevented. "
                f"Expected commit starting with {expected[:8]!r}, got {actual[:8]!r}."
            ),
            error_code="WORKITEM_COMMIT_MISMATCH",
            details={"item_id": item_id, "expected": expected[:8], "actual": actual[:8]},
        )


class WorkItemBackendError(WorkItemError):
    def __init__(self, message: str, *, cause: Optional[Exception] = None) -> None:
        super().__init__(message=message, error_code="WORKITEM_BACKEND_ERROR", cause=cause)


# ---------------------------------------------------------------------------
# Abstract backend
# ---------------------------------------------------------------------------


class BaseWorkItemBackend(ABC):
    """Pluggable storage for work items — same pattern as lock backends."""

    BACKEND_TYPE: str = "base"

    @abstractmethod
    def create(self, item: WorkItem) -> WorkItem:
        """Persist a new work item and return it."""
        ...

    @abstractmethod
    def get(self, item_id: str) -> Optional[WorkItem]:
        """Return the work item by ID, or None if not found."""
        ...

    @abstractmethod
    def resolve(
        self,
        item_id: str,
        status: str,
        resolved_by: str,
        note: Optional[str] = None,
    ) -> WorkItem:
        """Transition a pending work item to a terminal status."""
        ...

    @abstractmethod
    def list_items(
        self,
        type: Optional[str] = None,
        status: Optional[str] = None,
        deployment: Optional[str] = None,
    ) -> List[WorkItem]:
        """Return work items matching the given filters."""
        ...

    @abstractmethod
    def expire_stale(self) -> int:
        """Mark all expired-but-still-pending items as 'expired'. Returns count."""
        ...
