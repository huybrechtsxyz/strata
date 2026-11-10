#!/usr/bin/env python3
"""Tests for `DeploymentOutputStore` (docs/work/cross-run-step-output-delivery.md's
Implementation Plan steps 2-3, and ADR-0057: one key per step)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from strata.controllers.deployment_output_store import (
    _MAX_CAS_RETRIES,
    SIZE_WARNING_BYTES,
    DeploymentOutputStore,
    OutputRecordNotFoundError,
    OutputRecordOmittedError,
    OutputRecordStaleError,
    OutputRecordTooLargeError,
)
from strata.integrations.capabilities import NEW_KEY_VERSION, KeyValueStoreIntegration
from strata.integrations.errors import IntegrationError, KeyValueConflictError
from strata.integrations.resolved_context import KeyValueEntry
from strata.utils.errors import SystemError

_DEPLOYMENT = "prd-deployment"
_LEGACY_KEY = f"strata/deployment-outputs/{_DEPLOYMENT}"


def _step_key(step: str, deployment: str = _DEPLOYMENT) -> str:
    return f"strata/step-outputs/{deployment}/{step}"


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
        self.put_keys: list[str] = []

    def get(self, key: str) -> KeyValueEntry | None:
        if key not in self._data:
            return None
        value, version = self._data[key]
        return KeyValueEntry(value=value, version=version)

    def put(self, key: str, value: bytes, *, if_version: str | None = None) -> str:
        self.put_call_count += 1
        self.put_keys.append(key)
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


class _LimitedStore(_FakeKeyValueStore):
    """A backend with App Configuration's per-key limit."""

    MAX_VALUE_BYTES = 10_000


class _AlwaysConflictingStore(_FakeKeyValueStore):
    """Every conditional put() raises KeyValueConflictError; an unconditional one succeeds."""

    def put(self, key: str, value: bytes, *, if_version: str | None = None) -> str:
        if if_version is None:
            return super().put(key, value)
        self.put_call_count += 1
        raise KeyValueConflictError("always busy")


class _LegacyWriteFailsStore(_FakeKeyValueStore):
    """Writes to the old per-deployment key fail as if the backend were unreachable."""

    def put(self, key: str, value: bytes, *, if_version: str | None = None) -> str:
        if key == _LEGACY_KEY:
            raise IntegrationError("unreachable")
        return super().put(key, value, if_version=if_version)


class _ConflictOnNextPutStore(_FakeKeyValueStore):
    """After `arm()`, the next put() loses a race exactly once: `rival` (when set) is applied to the stored
    record first, as a concurrent writer's update would be, and then KeyValueConflictError is raised."""

    def __init__(self) -> None:
        super().__init__()
        self._armed = False
        self.rival: dict | None = None
        self.arm_for_key: str | None = None

    def arm(self) -> None:
        self._armed = True

    def put(self, key: str, value: bytes, *, if_version: str | None = None) -> str:
        if self.arm_for_key == key:
            self.arm_for_key = None
            self._armed = True
        if self._armed:
            self._armed = False
            self.put_call_count += 1
            if self.rival is not None:
                stored, _version = self._data[key]
                record = json.loads(stored)
                record.update(self.rival)
                self._version_counter += 1
                self._data[key] = (json.dumps(record).encode("utf-8"), str(self._version_counter))
            raise KeyValueConflictError("lost the race this time")
        return super().put(key, value, if_version=if_version)


def _seed_legacy(backend: _FakeKeyValueStore, steps: dict[str, dict[str, str]], *, collected_at: str | None = None):
    """Write the old per-deployment record, as a strata version before ADR-0057 did."""
    stamp = collected_at or datetime.now(timezone.utc).isoformat()
    record = {step: {"collected_at": stamp, "values": values} for step, values in steps.items()}
    backend._data[_LEGACY_KEY] = (json.dumps(record).encode("utf-8"), "legacy-1")


def _legacy(backend: _FakeKeyValueStore) -> dict:
    return json.loads(backend._data[_LEGACY_KEY][0])


def _backdate(backend: _FakeKeyValueStore, step: str, collected_at: datetime) -> None:
    """Rewrite a step key's collected_at, bypassing record()'s always-now() timestamp."""
    value, version = backend._data[_step_key(step)]
    record = json.loads(value)
    record["collected_at"] = collected_at.isoformat()
    backend._data[_step_key(step)] = (json.dumps(record).encode("utf-8"), version)


# ---------------------------------------------------------------------------
# record() and read() — one key per step
# ---------------------------------------------------------------------------


def test_each_step_gets_its_own_key():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    store.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())
    store.record("step2", {"db_host": "db.internal"}, sensitive_keys=set())

    assert set(backend._data) == {_step_key("step1"), _step_key("step2")}


def test_a_fresh_deployment_never_gets_the_old_key():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    store.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())

    assert _LEGACY_KEY not in backend._data


def test_record_then_read_round_trips_the_values():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    warnings = store.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())

    assert warnings == []
    assert store.read("step1") == {"vm_ip": "10.0.0.5"}


def test_record_writes_compact_json():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    store.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())

    stored = backend._data[_step_key("step1")][0]
    assert b": " not in stored and b", " not in stored


def test_record_filters_sensitive_keys_entirely():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    store.record("step1", {"vm_ip": "10.0.0.5", "admin_password": "hunter2"}, sensitive_keys={"admin_password"})

    assert store.read("step1") == {"vm_ip": "10.0.0.5"}
    assert b"hunter2" not in backend._data[_step_key("step1")][0]


def test_recording_one_step_leaves_another_step_untouched():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, _DEPLOYMENT)
    store.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())
    before = backend._data[_step_key("step1")]

    store.record("step2", {"db_host": "db.internal"}, sensitive_keys=set())

    assert backend._data[_step_key("step1")] == before
    assert store.read("step2") == {"db_host": "db.internal"}


def test_record_overwrites_the_same_step_on_rerun():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    store.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())
    store.record("step1", {"vm_ip": "10.0.0.6"}, sensitive_keys=set())

    assert store.read("step1") == {"vm_ip": "10.0.0.6"}


def test_two_deployments_never_share_a_key():
    backend = _FakeKeyValueStore()
    mine = DeploymentOutputStore(backend, _DEPLOYMENT)
    other = DeploymentOutputStore(backend, "dev-deployment")

    mine.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())
    other.record("step1", {"vm_ip": "10.9.9.9"}, sensitive_keys=set())

    assert mine.read("step1") == {"vm_ip": "10.0.0.5"}
    assert other.read("step1") == {"vm_ip": "10.9.9.9"}


def test_read_raises_not_found_when_nothing_is_recorded():
    store = DeploymentOutputStore(_FakeKeyValueStore(), _DEPLOYMENT)

    with pytest.raises(OutputRecordNotFoundError, match="No durable output recorded for step 'step1'"):
        store.read("step1")


def test_read_raises_not_found_for_a_step_that_never_ran():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, _DEPLOYMENT)
    store.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())

    with pytest.raises(OutputRecordNotFoundError, match="No durable output recorded for step 'step2'"):
        store.read("step2")


@pytest.mark.parametrize("stored", [b"not json", b"[1, 2]"])
def test_read_raises_a_clear_error_for_a_record_that_is_not_a_json_object(stored: bytes):
    backend = _FakeKeyValueStore()
    backend._data[_step_key("step1")] = (stored, "1")
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    with pytest.raises(SystemError, match="not a valid JSON object"):
        store.read("step1")


def test_read_raises_stale_when_past_the_trust_window():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, _DEPLOYMENT, max_age=timedelta(hours=1))
    store.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())
    _backdate(backend, "step1", datetime.now(timezone.utc) - timedelta(hours=2))

    with pytest.raises(OutputRecordStaleError, match="older than the"):
        store.read("step1")


def test_read_succeeds_when_within_the_trust_window():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, _DEPLOYMENT, max_age=timedelta(hours=1))
    store.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())
    _backdate(backend, "step1", datetime.now(timezone.utc) - timedelta(minutes=5))

    assert store.read("step1") == {"vm_ip": "10.0.0.5"}


def test_read_ignores_staleness_when_max_age_is_none():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, _DEPLOYMENT)
    store.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())
    _backdate(backend, "step1", datetime.now(timezone.utc) - timedelta(days=365))

    assert store.read("step1") == {"vm_ip": "10.0.0.5"}


# ---------------------------------------------------------------------------
# Reading the old per-deployment key (ADR-0057's transition)
# ---------------------------------------------------------------------------


def test_read_falls_back_to_the_old_key_when_the_step_has_no_key_of_its_own():
    backend = _FakeKeyValueStore()
    _seed_legacy(backend, {"step1": {"vm_ip": "10.0.0.5"}})
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    assert store.read("step1") == {"vm_ip": "10.0.0.5"}


def test_the_step_key_wins_over_the_old_key():
    backend = _FakeKeyValueStore()
    _seed_legacy(backend, {"step1": {"vm_ip": "10.0.0.5"}})
    backend._data[_step_key("step1")] = (
        json.dumps({"collected_at": datetime.now(timezone.utc).isoformat(), "values": {"vm_ip": "10.0.0.6"}}).encode(),
        "1",
    )
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    assert store.read("step1") == {"vm_ip": "10.0.0.6"}


def test_staleness_applies_to_a_record_read_from_the_old_key():
    backend = _FakeKeyValueStore()
    _seed_legacy(backend, {"step1": {"vm_ip": "10.0.0.5"}}, collected_at="2020-01-01T00:00:00+00:00")
    store = DeploymentOutputStore(backend, _DEPLOYMENT, max_age=timedelta(hours=1))

    with pytest.raises(OutputRecordStaleError):
        store.read("step1")


def test_read_raises_not_found_when_the_old_key_does_not_have_the_step():
    backend = _FakeKeyValueStore()
    _seed_legacy(backend, {"step1": {"vm_ip": "10.0.0.5"}})
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    with pytest.raises(OutputRecordNotFoundError, match="step 'step2'"):
        store.read("step2")


# ---------------------------------------------------------------------------
# Mirroring into the old key while older strata versions may still read it
# ---------------------------------------------------------------------------


def test_record_mirrors_into_an_existing_old_key():
    backend = _FakeKeyValueStore()
    _seed_legacy(backend, {"step1": {"vm_ip": "10.0.0.5"}, "step2": {"k": "v"}})
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    warnings = store.record("step1", {"vm_ip": "10.0.0.6"}, sensitive_keys={"secret"})

    assert warnings == []
    assert _legacy(backend)["step1"]["values"] == {"vm_ip": "10.0.0.6"}
    assert _legacy(backend)["step2"]["values"] == {"k": "v"}
    assert store.read("step1") == {"vm_ip": "10.0.0.6"}


def test_the_mirror_never_carries_a_sensitive_value():
    backend = _FakeKeyValueStore()
    _seed_legacy(backend, {})
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    store.record("step1", {"vm_ip": "10.0.0.5", "admin_password": "hunter2"}, sensitive_keys={"admin_password"})

    assert b"hunter2" not in backend._data[_LEGACY_KEY][0]


def test_a_mirror_that_would_be_too_large_removes_the_step_from_the_old_key_instead():
    """An older strata then finds nothing for the step, which fails loudly, instead of an outdated value."""
    backend = _LimitedStore()
    _seed_legacy(backend, {"step1": {"vm_ip": "10.0.0.5"}, "step2": {"big": "x" * 6_000}})
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    store.record("step1", {"blob": "y" * 5_000}, sensitive_keys=set())

    assert set(_legacy(backend)) == {"step2"}
    assert store.read("step1") == {"blob": "y" * 5_000}


def test_a_failed_mirror_is_a_warning_and_the_step_key_is_still_written():
    backend = _LegacyWriteFailsStore()
    _seed_legacy(backend, {"step1": {"vm_ip": "10.0.0.5"}})
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    warnings = store.record("step1", {"vm_ip": "10.0.0.6"}, sensitive_keys=set())

    assert len(warnings) == 1 and "older strata version may read an outdated value" in warnings[0]
    assert store.read("step1") == {"vm_ip": "10.0.0.6"}


def test_a_mirror_that_keeps_conflicting_is_a_warning():
    backend = _AlwaysConflictingStore()
    _seed_legacy(backend, {"step1": {"vm_ip": "10.0.0.5"}})
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    warnings = store.record("step1", {"vm_ip": "10.0.0.6"}, sensitive_keys=set())

    assert len(warnings) == 1 and "conflicting writes" in warnings[0]
    assert store.read("step1") == {"vm_ip": "10.0.0.6"}


def test_a_mirror_into_a_corrupt_old_key_is_a_warning():
    backend = _FakeKeyValueStore()
    backend._data[_LEGACY_KEY] = (b"not json", "1")
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    warnings = store.record("step1", {"vm_ip": "10.0.0.6"}, sensitive_keys=set())

    assert len(warnings) == 1 and "not a valid JSON object" in warnings[0]


def test_a_mirror_retries_once_on_a_conflict_and_keeps_the_rival_update():
    backend = _ConflictOnNextPutStore()
    _seed_legacy(backend, {"step1": {"vm_ip": "10.0.0.5"}})
    backend.rival = {"step3": {"collected_at": "2026-11-04T00:00:00+00:00", "values": {"k": "v"}}}
    backend.arm_for_key = _LEGACY_KEY
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    warnings = store.record("step1", {"vm_ip": "10.0.0.6"}, sensitive_keys=set())

    assert warnings == []
    assert set(_legacy(backend)) == {"step1", "step3"}
    assert _legacy(backend)["step1"]["values"] == {"vm_ip": "10.0.0.6"}


# ---------------------------------------------------------------------------
# Size (ADR-0057): a warning above 8 KB, an error above the backend's limit
# ---------------------------------------------------------------------------


def test_a_record_above_8_kb_on_a_limited_backend_is_written_with_a_warning():
    backend = _LimitedStore()
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    warnings = store.record("step1", {"kubeconfig": "x" * 8_500}, sensitive_keys=set())

    assert len(warnings) == 1
    assert "step1" in warnings[0] and "10000 bytes" in warnings[0]
    assert store.read("step1") == {"kubeconfig": "x" * 8_500}


def test_a_record_above_the_limit_raises_and_writes_only_the_omitted_marker():
    backend = _LimitedStore()
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    with pytest.raises(OutputRecordTooLargeError, match="Step 'step1'.*more than the 10000 bytes"):
        store.record("step1", {"kubeconfig": "x" * 10_500}, sensitive_keys=set())

    assert set(backend._data) == {_step_key("step1")}
    marker = json.loads(backend._data[_step_key("step1")][0])
    assert set(marker) == {"omitted_at", "size"}
    with pytest.raises(OutputRecordOmittedError) as raised:
        store.read("step1")
    assert raised.value.omitted_at == marker["omitted_at"]


def test_a_too_large_record_hides_the_previous_deploys_values():
    """The step's own key held the previous deploy's values: after an oversized deploy they are not read."""
    backend = _LimitedStore()
    store = DeploymentOutputStore(backend, _DEPLOYMENT)
    store.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())

    with pytest.raises(OutputRecordTooLargeError):
        store.record("step1", {"vm_ip": "10.0.0.6", "kubeconfig": "x" * 10_500}, sensitive_keys=set())

    with pytest.raises(OutputRecordOmittedError, match="larger than the output store accepts"):
        store.read("step1")
    with pytest.raises(OutputRecordNotFoundError):  # what a consuming deploy catches
        store.read_record("step1")


def test_a_too_large_record_removes_the_step_from_the_old_key():
    """Only the old per-deployment key held the step: without the marker, read() would fall back to it."""
    backend = _LimitedStore()
    _seed_legacy(backend, {"step1": {"vm_ip": "10.0.0.5"}, "step2": {"x": "1"}})
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    with pytest.raises(OutputRecordTooLargeError):
        store.record("step1", {"kubeconfig": "x" * 10_500}, sensitive_keys=set())

    assert set(_legacy(backend)) == {"step2"}
    with pytest.raises(OutputRecordOmittedError):
        store.read("step1")
    assert store.read("step2") == {"x": "1"}


def test_the_size_counts_the_key_too():
    """App Configuration counts the key towards its limit: a value that fits alone can still be too large."""
    backend = _LimitedStore()
    store = DeploymentOutputStore(backend, _DEPLOYMENT)
    overhead = len(_step_key("step1")) + len(b'{"collected_at":"2026-11-09T00:00:00.000000+00:00","values":{"k":""}}')
    value = "x" * (10_000 - overhead + 5)

    with pytest.raises(OutputRecordTooLargeError):
        store.record("step1", {"k": value}, sensitive_keys=set())


def test_sensitive_values_do_not_count_towards_the_size():
    backend = _LimitedStore()
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    warnings = store.record("step1", {"vm_ip": "10.0.0.5", "cert": "x" * 20_000}, sensitive_keys={"cert"})

    assert warnings == []


def test_a_backend_without_a_limit_never_warns_about_size():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    warnings = store.record("step1", {"kubeconfig": "x" * (SIZE_WARNING_BYTES * 4)}, sensitive_keys=set())

    assert warnings == []


# ---------------------------------------------------------------------------
# clear() (docs/design/deploy-destroy.md) — forget a destroyed step's outputs
# ---------------------------------------------------------------------------


def test_clear_marks_the_step_key_destroyed():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, _DEPLOYMENT)
    store.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())
    store.record("step2", {"db_host": "db.internal"}, sensitive_keys=set())

    store.clear(["step1"])

    assert "destroyed_at" in json.loads(backend._data[_step_key("step1")][0])
    with pytest.raises(OutputRecordNotFoundError, match="step 'step1': it was destroyed"):
        store.read("step1")
    assert store.read("step2") == {"db_host": "db.internal"}


def test_the_destroyed_marker_stops_the_fallback_to_the_old_key():
    """Otherwise a destroyed step's values from before the upgrade would come back."""
    backend = _FakeKeyValueStore()
    _seed_legacy(backend, {"step1": {"vm_ip": "10.0.0.5"}})
    backend._data[_step_key("step1")] = (json.dumps({"destroyed_at": "2026-11-09T00:00:00+00:00"}).encode(), "1")
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    with pytest.raises(OutputRecordNotFoundError, match="destroyed"):
        store.read("step1")


def test_clear_writes_no_marker_for_a_step_without_a_key():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    store.clear(["step1"])

    assert backend.put_call_count == 0
    assert backend._data == {}


def test_clear_with_no_steps_does_not_even_read_the_store():
    backend = _FakeKeyValueStore()
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    store.clear([])

    assert backend.put_call_count == 0
    assert backend._data == {}


def test_clear_removes_only_the_named_steps_from_the_old_key():
    backend = _FakeKeyValueStore()
    _seed_legacy(backend, {"step1": {"vm_ip": "10.0.0.5"}, "step2": {"db_host": "db.internal"}})
    before = _legacy(backend)
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    store.clear(["step1"])

    assert _legacy(backend) == {"step2": before["step2"]}
    with pytest.raises(OutputRecordNotFoundError):
        store.read("step1")
    assert store.read("step2") == {"db_host": "db.internal"}


def test_clear_rewrites_the_old_key_once_for_every_named_step():
    backend = _FakeKeyValueStore()
    _seed_legacy(backend, {"step1": {"k": "1"}, "step2": {"k": "2"}, "step3": {"k": "3"}})
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    store.clear(["step1", "step3"])

    assert backend.put_keys == [_LEGACY_KEY]
    assert set(_legacy(backend)) == {"step2"}


def test_clear_the_last_step_leaves_an_empty_old_record_not_a_deleted_one():
    backend = _FakeKeyValueStore()
    _seed_legacy(backend, {"step1": {"vm_ip": "10.0.0.5"}})
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    store.clear(["step1"])

    assert backend._data[_LEGACY_KEY][0] == b"{}"


def test_clear_a_name_that_is_not_recorded_writes_nothing():
    backend = _FakeKeyValueStore()
    _seed_legacy(backend, {"step1": {"vm_ip": "10.0.0.5"}})
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    store.clear(["ghost"])

    assert backend.put_call_count == 0


def test_clear_retries_on_a_conflict_and_still_removes():
    backend = _ConflictOnNextPutStore()
    _seed_legacy(backend, {"step1": {"vm_ip": "10.0.0.5"}, "step2": {"db_host": "db.internal"}})
    store = DeploymentOutputStore(backend, _DEPLOYMENT)
    backend.arm()

    store.clear(["step1"])

    assert backend.put_call_count == 2
    assert set(_legacy(backend)) == {"step2"}


def test_clear_a_one_shot_iterable_still_removes_everything_after_a_retry():
    """A generator can be consumed only once, and the retry loop reads its argument on every pass."""
    backend = _ConflictOnNextPutStore()
    _seed_legacy(backend, {"step1": {"k": "1"}, "step2": {"k": "2"}, "step3": {"k": "3"}})
    store = DeploymentOutputStore(backend, _DEPLOYMENT)
    backend.arm()

    store.clear(step for step in ("step1", "step2"))

    assert set(_legacy(backend)) == {"step3"}


def test_clear_keeps_a_concurrent_writers_update_instead_of_overwriting_it():
    backend = _ConflictOnNextPutStore()
    _seed_legacy(backend, {"step1": {"vm_ip": "10.0.0.5"}})
    backend.rival = {"step3": {"collected_at": "2026-11-04T00:00:00+00:00", "values": {"k": "v"}}}
    store = DeploymentOutputStore(backend, _DEPLOYMENT)
    backend.arm()

    store.clear(["step1"])

    assert set(_legacy(backend)) == {"step3"}


def test_clear_raises_system_error_after_max_retries_exhausted():
    backend = _AlwaysConflictingStore()
    _seed_legacy(backend, {"step1": {"vm_ip": "10.0.0.5"}})
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    with pytest.raises(SystemError, match="conflicting writes"):
        store.clear(["step1"])

    assert backend.put_call_count == _MAX_CAS_RETRIES


@pytest.mark.parametrize("stored", [b"not json", b"[1, 2]"])
def test_clear_raises_a_clear_error_for_an_old_record_that_is_not_a_json_object(stored: bytes):
    backend = _FakeKeyValueStore()
    backend._data[_LEGACY_KEY] = (stored, "1")
    store = DeploymentOutputStore(backend, _DEPLOYMENT)

    with pytest.raises(SystemError, match="not a valid JSON object"):
        store.clear(["step1"])

    assert backend.put_call_count == 0


def test_clear_never_touches_another_deployments_records():
    backend = _FakeKeyValueStore()
    mine = DeploymentOutputStore(backend, _DEPLOYMENT)
    other = DeploymentOutputStore(backend, "dev-deployment")
    mine.record("step1", {"vm_ip": "10.0.0.5"}, sensitive_keys=set())
    other.record("step1", {"vm_ip": "10.9.9.9"}, sensitive_keys=set())

    mine.clear(["step1"])

    assert other.read("step1") == {"vm_ip": "10.9.9.9"}
