#!/usr/bin/env python3
"""Tests for `DeploymentReportStore` (docs/work/deployment-documentation.md, section 1: the report record)."""

import json
from datetime import datetime, timezone

import pytest

from strata.controllers.deployment_output_store import SIZE_WARNING_BYTES, OutputRecordTooLargeError
from strata.controllers.deployment_report_store import (
    SHORT_DESCRIPTION_LENGTH,
    DeploymentReportStore,
    SecretUse,
    StepReport,
)
from strata.integrations.capabilities import KeyValueStoreIntegration
from strata.integrations.resolved_context import KeyValueEntry
from strata.utils.errors import SystemError

_KEY = "strata/deployment-report/prd/infra"
_AT = datetime(2026, 11, 9, 14, 28, tzinfo=timezone.utc)


class _Store(KeyValueStoreIntegration):
    TYPE = "fake"
    CAPABILITIES = frozenset({"key_value"})
    TRANSPORTS = frozenset({"sdk"})

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.puts: list[tuple[str, str | None]] = []

    def get(self, key: str) -> KeyValueEntry | None:
        return KeyValueEntry(value=self.data[key], version="1") if key in self.data else None

    def put(self, key: str, value: bytes, *, if_version: str | None = None) -> str:
        self.puts.append((key, if_version))
        self.data[key] = value
        return "1"


class _LimitedStore(_Store):
    MAX_VALUE_BYTES = 10_000


def _record(store: _Store) -> dict:
    return json.loads(store.data[_KEY])


def test_record_writes_compact_names_and_descriptions_with_a_plain_put():
    store = _Store()
    warnings = DeploymentReportStore(store, "prd").record(
        "infra", sensitive_keys={"pwd", "conn"}, descriptions={"pwd": "In kv-web"}, collected_at=_AT
    )

    assert warnings == []
    assert store.puts == [(_KEY, None)]
    assert store.data[_KEY] == (
        b'{"collected_at":"2026-11-09T14:28:00+00:00","sensitive":["conn","pwd"],"d":{"pwd":"In kv-web"},"secrets":[]}'
    )


def test_empty_fields_are_left_out_but_secrets_is_always_written():
    """An empty `secrets` list means "none used"; a missing one means "not recorded" (an older record)."""
    store = _Store()
    DeploymentReportStore(store, "prd").record("infra", sensitive_keys=[], descriptions={}, collected_at=_AT)
    assert _record(store) == {"collected_at": "2026-11-09T14:28:00+00:00", "secrets": []}


def test_read_returns_the_record_and_none_without_one():
    store = _Store()
    reports = DeploymentReportStore(store, "prd")
    assert reports.read("infra") is None

    reports.record("infra", sensitive_keys=["pwd"], descriptions={"pwd": "In kv-web"}, collected_at=_AT)
    assert reports.read("infra") == StepReport(
        collected_at=_AT, sensitive_keys=("pwd",), descriptions={"pwd": "In kv-web"}, secrets=()
    )


def test_secrets_round_trip_by_location_with_short_field_names():
    store = _Store()
    reports = DeploymentReportStore(store, "prd")
    use = SecretUse(
        key="SQL_PWD",
        store="azure-keyvault",
        location={"vault": "https://kv.vault.azure.net/", "name": "sql-pwd"},
        description="SQL admin",
        declared_in="environments/prd.yaml",
    )
    reports.record("infra", sensitive_keys=[], descriptions={}, collected_at=_AT, secrets=[use], all_secrets=True)

    assert _record(store)["secrets"] == [
        {
            "k": "SQL_PWD",
            "s": "azure-keyvault",
            "loc": {"vault": "https://kv.vault.azure.net/", "name": "sql-pwd"},
            "d": "SQL admin",
            "in": "environments/prd.yaml",
        }
    ]
    report = reports.read("infra")
    assert report is not None and report.secrets == (use,) and report.all_secrets is True


def test_a_record_without_a_secrets_field_reads_as_not_recorded():
    store = _Store()
    store.data[_KEY] = b'{"collected_at":"2026-11-09T14:28:00+00:00"}'
    report = DeploymentReportStore(store, "prd").read("infra")
    assert report is not None and report.secrets is None


def test_secret_descriptions_are_shortened_and_locations_kept():
    store = _LimitedStore()
    uses = [
        SecretUse(key=f"S{i}", store="environment", location={"env": f"S{i}"}, description="d" * 600) for i in range(20)
    ]
    DeploymentReportStore(store, "prd").record(
        "infra", sensitive_keys=[], descriptions={}, collected_at=_AT, secrets=uses
    )
    record = _record(store)
    assert record["truncated"] is True
    assert all(entry["loc"] == {"env": entry["k"]} for entry in record["secrets"])
    assert all(len(entry.get("d", "")) <= SHORT_DESCRIPTION_LENGTH for entry in record["secrets"])


def test_a_corrupt_record_is_a_system_error():
    store = _Store()
    store.data[_KEY] = b"[1, 2]"
    with pytest.raises(SystemError, match="not a valid JSON object"):
        DeploymentReportStore(store, "prd").read("infra")


def test_clear_writes_the_destroyed_marker_only_to_existing_keys():
    store = _Store()
    reports = DeploymentReportStore(store, "prd")
    reports.record("infra", sensitive_keys=["pwd"], descriptions={}, collected_at=_AT)
    reports.clear(["infra", "never_ran"])

    assert set(store.data) == {_KEY}
    report = reports.read("infra")
    assert report is not None and report.destroyed_at is not None and report.sensitive_keys == ()


def test_no_size_warning_on_a_backend_without_a_limit():
    store = _Store()
    warnings = DeploymentReportStore(store, "prd").record(
        "infra", sensitive_keys=[], descriptions={"a": "x" * 50_000}, collected_at=_AT
    )
    assert warnings == []
    assert _record(store)["d"]["a"] == "x" * 50_000


def test_warns_above_8_kb_on_a_backend_with_a_limit():
    store = _LimitedStore()
    warnings = DeploymentReportStore(store, "prd").record(
        "infra", sensitive_keys=[], descriptions={"a": "x" * (SIZE_WARNING_BYTES + 100)}, collected_at=_AT
    )
    assert len(warnings) == 1 and "close to the 10000 bytes" in warnings[0]
    assert "truncated" not in _record(store)


def test_above_the_limit_descriptions_are_shortened_and_marked_truncated():
    store = _LimitedStore()
    descriptions = {f"out_{i}": "d" * 500 for i in range(25)}  # ~12.8 KB before shortening
    warnings = DeploymentReportStore(store, "prd").record(
        "infra", sensitive_keys=["out_0"], descriptions=descriptions, collected_at=_AT
    )

    record = _record(store)
    assert record["truncated"] is True
    assert all(len(text) == SHORT_DESCRIPTION_LENGTH and text.endswith("...") for text in record["d"].values())
    assert record["sensitive"] == ["out_0"]
    assert any("shortened to 200 characters" in warning for warning in warnings)


def test_when_shortening_is_not_enough_descriptions_are_left_out_and_names_kept():
    store = _LimitedStore()
    descriptions = {f"out_{i}": "d" * 300 for i in range(60)}  # still ~13 KB at 200 characters each
    warnings = DeploymentReportStore(store, "prd").record(
        "infra", sensitive_keys=sorted(descriptions), descriptions=descriptions, collected_at=_AT
    )

    record = _record(store)
    assert "d" not in record and record["truncated"] is True
    assert record["sensitive"] == sorted(descriptions)
    assert any("descriptions are left out" in warning for warning in warnings)


def test_names_alone_over_the_limit_raise_and_write_nothing():
    store = _LimitedStore()
    with pytest.raises(OutputRecordTooLargeError, match="even without descriptions"):
        DeploymentReportStore(store, "prd").record(
            "infra", sensitive_keys=[f"name_{i:05d}_{'n' * 80}" for i in range(200)], descriptions={}
        )
    assert store.data == {}


def test_prepare_sizes_without_writing():
    store = _LimitedStore()
    prepared = DeploymentReportStore(store, "prd").prepare(
        "infra", sensitive_keys=["pwd"], descriptions={"pwd": "In kv-web"}, collected_at=_AT
    )
    assert store.data == {}
    assert prepared.key == _KEY
    assert prepared.size == len(_KEY) + len(prepared.data)
