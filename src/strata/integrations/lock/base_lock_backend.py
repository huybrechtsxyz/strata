"""Abstract base for deployment lock backends, plus shared dataclasses."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from strata.exceptions.base_exception import PlatformError


@dataclass
class LockEntry:
    """Snapshot of a held or historical lock.

    Written to the lock backend (blob body, file content, DynamoDB item, etc.)
    and read back by ``status()`` and ``history()``.
    """

    lock_id: str
    deployment: str
    holder: str
    hostname: str
    pid: int
    acquired_at: str  # ISO-8601
    expires_at: str  # ISO-8601
    reason: str
    stage: Optional[str] = None


@dataclass
class LockHandle:
    """Opaque handle returned by ``acquire()``.

    Passed back to ``release()`` to identify exactly which lock to free.
    ``_backend_data`` carries backend-specific identifiers (e.g. blob lease ID,
    TFC lock run ID, Consul session ID) without leaking them to callers.
    """

    lock_id: str
    backend_type: str
    acquired_at: str  # ISO-8601
    _backend_data: Dict[str, Any] = field(default_factory=dict)


class BaseLockBackend(ABC):
    """Abstract base for deployment lock backends.

    Instantiated by ``LockFactory`` from a resolved ``WorkspaceIacBackendModel``.
    Subclasses implement backend-specific acquire/release/status semantics.

    All public methods are blocking. Callers are responsible for running them
    on a background thread if non-blocking behaviour is needed.
    """

    @abstractmethod
    def acquire(
        self,
        deployment_name: str,
        holder: str,
        reason: str,
        timeout_seconds: int,
    ) -> LockHandle:
        """Acquire the deployment lock.

        Polls until the lock is free or ``timeout_seconds`` elapses.

        Args:
            deployment_name: Name of the deployment being locked.
            holder: Identity of the lock requester (user or CI actor).
            reason: Human-readable reason for acquiring the lock.
            timeout_seconds: Maximum time to wait for a held lock to be released.

        Returns:
            A ``LockHandle`` that must be passed to ``release()`` when done.

        Raises:
            LockTimeoutError: If the lock could not be acquired within
                ``timeout_seconds``.
            LockBackendError: If the backend is unreachable or returns an error.
        """

    @abstractmethod
    def release(self, handle: LockHandle) -> None:
        """Release a previously acquired lock.

        Safe to call in a ``finally`` block — implementations must not raise
        for transient errors (they should log and swallow).

        Args:
            handle: The handle returned by ``acquire()``.
        """

    @abstractmethod
    def status(self, deployment_name: str) -> Optional[LockEntry]:
        """Return the current lock entry, or ``None`` if the deployment is unlocked.

        Args:
            deployment_name: Name of the deployment to query.

        Returns:
            A ``LockEntry`` if a lock is held, otherwise ``None``.
        """

    @abstractmethod
    def force_release(self, deployment_name: str) -> None:
        """Force-release a lock regardless of its holder.

        Emergency use only — intended for ``strata deploy lock release --force``.
        Implementations should write an audit entry before releasing.

        Args:
            deployment_name: Name of the deployment whose lock to force-release.
        """

    @abstractmethod
    def history(self, deployment_name: str, limit: int = 10) -> List[LockEntry]:
        """Return recent lock events for the deployment.

        Args:
            deployment_name: Name of the deployment to query.
            limit: Maximum number of events to return (most recent first).

        Returns:
            A list of ``LockEntry`` objects, most recent first.
        """


class LockTimeoutError(PlatformError):
    """Raised when ``acquire()`` cannot obtain the lock within the timeout."""

    def __init__(self, deployment_name: str, timeout_seconds: int, holder: str) -> None:
        self.deployment_name = deployment_name
        self.timeout_seconds = timeout_seconds
        self.holder = holder
        super().__init__(
            message=f"Could not acquire lock for '{deployment_name}' within {timeout_seconds}s (currently held by {holder!r})",
            error_code="LOCK_TIMEOUT",
            details={
                "deployment": deployment_name,
                "timeout_seconds": timeout_seconds,
                "holder": holder,
            },
        )


class LockBackendError(PlatformError):
    """Raised when the lock backend returns an unexpected error."""

    def __init__(self, message: str, *, cause: Exception | None = None) -> None:
        super().__init__(message=message, error_code="LOCK_BACKEND_ERROR", cause=cause)
