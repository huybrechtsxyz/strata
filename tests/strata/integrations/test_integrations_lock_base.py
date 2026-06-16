"""Unit tests for BaseLockBackend, LockEntry, LockHandle, and exception classes."""

import pytest

from strata.integrations.lock.base_lock_backend import (
    BaseLockBackend,
    LockBackendError,
    LockEntry,
    LockHandle,
    LockTimeoutError,
)

# ---------------------------------------------------------------------------
# LockEntry
# ---------------------------------------------------------------------------


class TestLockEntry:
    def test_all_required_fields(self):
        entry = LockEntry(
            lock_id="abc-123",
            deployment="prod-platform",
            holder="alice@company.com",
            hostname="WORKSTATION-A",
            pid=12345,
            acquired_at="2026-06-16T14:02:01Z",
            expires_at="2026-06-16T22:02:01Z",
            reason="strata deploy run",
        )
        assert entry.lock_id == "abc-123"
        assert entry.deployment == "prod-platform"
        assert entry.holder == "alice@company.com"
        assert entry.hostname == "WORKSTATION-A"
        assert entry.pid == 12345
        assert entry.acquired_at == "2026-06-16T14:02:01Z"
        assert entry.expires_at == "2026-06-16T22:02:01Z"
        assert entry.reason == "strata deploy run"
        assert entry.stage is None

    def test_stage_optional(self):
        entry = LockEntry(
            lock_id="x",
            deployment="d",
            holder="h",
            hostname="host",
            pid=1,
            acquired_at="2026-06-16T00:00:00Z",
            expires_at="2026-06-16T08:00:00Z",
            reason="r",
            stage="provision",
        )
        assert entry.stage == "provision"

    def test_is_dataclass(self):
        import dataclasses

        assert dataclasses.is_dataclass(LockEntry)


# ---------------------------------------------------------------------------
# LockHandle
# ---------------------------------------------------------------------------


class TestLockHandle:
    def test_minimal(self):
        handle = LockHandle(
            lock_id="abc-123",
            backend_type="local",
            acquired_at="2026-06-16T14:02:01Z",
        )
        assert handle.lock_id == "abc-123"
        assert handle.backend_type == "local"
        assert handle.acquired_at == "2026-06-16T14:02:01Z"
        assert handle._backend_data == {}

    def test_backend_data(self):
        handle = LockHandle(
            lock_id="abc-123",
            backend_type="azurerm",
            acquired_at="2026-06-16T14:02:01Z",
            _backend_data={"lease_id": "lease-xyz"},
        )
        assert handle._backend_data["lease_id"] == "lease-xyz"

    def test_is_dataclass(self):
        import dataclasses

        assert dataclasses.is_dataclass(LockHandle)


# ---------------------------------------------------------------------------
# BaseLockBackend — ABC enforcement
# ---------------------------------------------------------------------------


class TestBaseLockBackendABC:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            BaseLockBackend()  # type: ignore[abstract]

    def test_concrete_subclass_must_implement_all_methods(self):
        """A subclass that only overrides some methods is still abstract."""

        class PartialBackend(BaseLockBackend):
            def acquire(self, deployment_name, holder, reason, timeout_seconds):
                return LockHandle(lock_id="x", backend_type="local", acquired_at="now")

        with pytest.raises(TypeError):
            PartialBackend()  # type: ignore[abstract]

    def test_fully_concrete_subclass_can_be_instantiated(self):
        class ConcreteBackend(BaseLockBackend):
            def acquire(self, deployment_name, holder, reason, timeout_seconds):
                return LockHandle(lock_id="x", backend_type="test", acquired_at="now")

            def release(self, handle):
                pass

            def status(self, deployment_name):
                return None

            def force_release(self, deployment_name):
                pass

            def history(self, deployment_name, limit=10):
                return []

        backend = ConcreteBackend()
        assert isinstance(backend, BaseLockBackend)

    def test_concrete_subclass_acquire_returns_handle(self):
        class ConcreteBackend(BaseLockBackend):
            def acquire(self, deployment_name, holder, reason, timeout_seconds):
                return LockHandle(
                    lock_id="test-id",
                    backend_type="test",
                    acquired_at="2026-06-16T14:00:00Z",
                )

            def release(self, handle):
                pass

            def status(self, deployment_name):
                return None

            def force_release(self, deployment_name):
                pass

            def history(self, deployment_name, limit=10):
                return []

        backend = ConcreteBackend()
        handle = backend.acquire("prod", "alice", "test", 30)
        assert handle.lock_id == "test-id"
        assert handle.backend_type == "test"


# ---------------------------------------------------------------------------
# LockTimeoutError
# ---------------------------------------------------------------------------


class TestLockTimeoutError:
    def test_message_contains_deployment_and_timeout(self):
        err = LockTimeoutError("prod-platform", 1800, "bob@company.com")
        msg = str(err)
        assert "prod-platform" in msg
        assert "1800" in msg
        assert "bob@company.com" in msg

    def test_attributes(self):
        err = LockTimeoutError("prod-platform", 1800, "bob@company.com")
        assert err.deployment_name == "prod-platform"
        assert err.timeout_seconds == 1800
        assert err.holder == "bob@company.com"

    def test_is_exception(self):
        err = LockTimeoutError("d", 60, "h")
        assert isinstance(err, Exception)


# ---------------------------------------------------------------------------
# LockBackendError
# ---------------------------------------------------------------------------


class TestLockBackendError:
    def test_is_exception(self):
        err = LockBackendError("backend unreachable")
        assert isinstance(err, Exception)
        assert "backend unreachable" in str(err)
