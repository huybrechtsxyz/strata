#!/usr/bin/env python3
"""Tests for `DeploymentOutputStore` (docs/work/cross-run-step-output-delivery.md's
Implementation Plan steps 2-3)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from strata.controllers.deployment_output_store import (
    DeploymentOutputStore,
    OutputRecordNotFoundError,
    OutputRecordStaleError,
)
from strata.integrations.capabilities import NEW_KEY_VERSION, KeyValueStoreIntegration
from strata.integrations.errors import KeyValueConflictError
from strata.integrations.resolved_context import KeyValueEntry
from strata.utils.errors import SystemError


class _FakeKeyValueStore(KeyValueStoreIntegration):
    """An in-memory, fully-CAS-correct fake — exercises `DeploymentOutputStore`
    exactly the way a real backend would, without any SDK dependency."""

    TYPE = "fake"
    CAPABILITIES = frozenset({"key_value"})
    TRANSPORTS = frozenset({"sdk"})

    def __init__(self) -> None:
        self._data: dict[str, tuple[bytes, str]] = {}
        self._version_counter = 0
        self.put_call_count = 0

    def get(self, key: str) -> KeyValueEntry | None:
        if key not in self._data:
            return None
        value, version = self._data[key]
        return KeyValueEntry(value=value, version=version)

    def put(self, key: str, value: bytes, *, if_version: str | None = None) -> str:
        self.put_call_count += 1
        existing = self._data.get(key)
        if if_version == NEW_KEY_VERSION:
            if existing is not None:
                raise KeyValueConflictError(f"'{key}' already exists")
        elif if_version is not None:
            current_version = existing[1] if existing is not None else None
            if current_version != if_version:
                raise KeyValueConflictError(f"'{key}' was modified concurrently")
        self._version_counter += 1
        new_version = str(self._version_counter)
        self._data[key] = (value, new_version)
        return new_version


class _AlwaysConflictingStore(_FakeKeyValueStore):
    """Every put() call raises KeyValueConflictError, unconditionally —
    for the retry-exhaustion test."""

    def put(self, key: str, value: bytes, *, if_version: str | None = None) -> str:
        self.put_call_count += 1
        raise KeyValueConflictError("always busy")


class _ConflictOnceStore(_FakeKeyValueStore):
    """The first put() call raises KeyValueConflictError, every call after
    that behaves normally — simulates exactly one concurrent writer
    winning the race first."""

    def __init__(self) -> None:
        super().__init__()
        self._first_call_done = False

    def put(self, key: str, value: bytes, *, if_version: str | None = None) -> str:
        if not self._first_call_done:
            self._first_call_done = True
            self.put_call_count += 1
            raise KeyValueConflictError("lost the race this time")
        return super().put(key, value, if_version=if_version)


def test_record_key_is_namespaced_by_deployment_name():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, "prd-deployment")

    store.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())

    assert "strata/deployment-outputs/prd-deployment" in backend._data


def test_record_then_read_round_trips_the_values():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, "prd-deployment")

    store.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())

    assert store.read("step1") == {"vm_ip": "10.0.0.5"}


def test_record_filters_sensitive_keys_entirely():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, "prd-deployment")

    store.record("step1", {"vm_ip": "10.0.0.5", "admin_password": "hunter2"}, sensitive_keys={"admin_password"})

    assert store.read("step1") == {"vm_ip": "10.0.0.5"}


def test_record_merges_with_an_existing_different_step():
    """One shared record per deployment, not one per step — recording
    step2 must not erase step1's own already-recorded entry."""
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, "prd-deployment")

    store.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())
    store.record("step2", {"db_host": "db.internal"}, sensitive_keys=set())

    assert store.read("step1") == {"vm_ip": "10.0.0.5"}
    assert store.read("step2") == {"db_host": "db.internal"}


def test_record_overwrites_the_same_step_on_rerun():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, "prd-deployment")

    store.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())
    store.record("step1", {"vm_ip": "10.0.0.6"}, sensitive_keys=set())

    assert store.read("step1") == {"vm_ip": "10.0.0.6"}


def test_record_retries_once_on_a_single_conflict_and_still_succeeds():
    backend = _ConflictOnceStore()
    store = DeploymentOutputStore(backend, "prd-deployment")

    store.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())

    assert store.read("step1") == {"vm_ip": "10.0.0.5"}
    assert backend.put_call_count == 2


def test_record_raises_system_error_after_max_retries_exhausted():
    backend = _AlwaysConflictingStore()
    store = DeploymentOutputStore(backend, "prd-deployment")

    with pytest.raises(SystemError, match="conflicting writes"):
        store.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())


def test_read_raises_not_found_when_no_record_exists_at_all():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, "prd-deployment")

    with pytest.raises(OutputRecordNotFoundError, match="No durable output record exists"):
        store.read("step1")


def test_read_raises_not_found_when_step_not_in_an_existing_record():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, "prd-deployment")
    store.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())

    with pytest.raises(OutputRecordNotFoundError, match="No durable output recorded for step 'step2'"):
        store.read("step2")


def _backdate_step_record(backend: _FakeKeyValueStore, deployment_name: str, step: str, collected_at: datetime) -> None:
    """Directly rewrite a step's collected_at, bypassing record()'s own
    always-now() timestamp — the only way to construct a genuinely stale
    fixture for the staleness tests."""
    key = f"strata/deployment-outputs/{deployment_name}"
    value, version = backend._data[key]
    record = json.loads(value)
    record[step]["collected_at"] = collected_at.isoformat()
    backend._data[key] = (json.dumps(record).encode("utf-8"), version)


def test_read_raises_stale_when_past_the_trust_window():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, "prd-deployment", max_age=timedelta(hours=1))
    store.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())
    _backdate_step_record(backend, "prd-deployment", "step1", datetime.now(timezone.utc) - timedelta(hours=2))

    with pytest.raises(OutputRecordStaleError, match="older than the"):
        store.read("step1")


def test_read_succeeds_when_within_the_trust_window():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, "prd-deployment", max_age=timedelta(hours=1))
    store.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())
    _backdate_step_record(backend, "prd-deployment", "step1", datetime.now(timezone.utc) - timedelta(minutes=5))

    assert store.read("step1") == {"vm_ip": "10.0.0.5"}


def test_read_ignores_staleness_when_max_age_is_none():
    """max_age unset (None) means no staleness check at all — not even
    for a record collected far in the past."""
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, "prd-deployment")
    store.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())
    _backdate_step_record(backend, "prd-deployment", "step1", datetime.now(timezone.utc) - timedelta(days=365))

    assert store.read("step1") == {"vm_ip": "10.0.0.5"}
