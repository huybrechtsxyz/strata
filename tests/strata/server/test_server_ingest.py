"""Tests for extract_row() — CloudEvents envelope to `events` row mapping (ADR-0065 Step 2.3)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

import pytest

from strata.server.db.ingest import extract_row


def _make_envelope(**overrides: Any) -> Dict[str, Any]:
    envelope: Dict[str, Any] = {
        "specversion": "1.0",
        "type": "xyz.huybrechts.strata.deployment.completed",
        "source": "/strata/my-workspace/my-deploy",
        "id": "11111111-1111-1111-1111-111111111111",
        "time": "2026-08-10T12:00:00+00:00",
        "datacontenttype": "application/json",
        "subject": "my-deploy",
        "data": {
            "event": {"kind": "event", "action": "deployment-completed", "outcome": "success"},
            "user": {"name": "actor"},
            "labels": {
                "execution_id": "exec-123",
                "workspace": "my-workspace",
                "environment": "prd",
                "deployment": "my-deploy",
                "tenant": "acme",
            },
            "strata": {"execution_id": "exec-123", "deployment": "my-deploy"},
        },
    }
    envelope.update(overrides)
    return envelope


class TestExtractRow:
    def test_maps_required_and_dimension_fields(self) -> None:
        row = extract_row(_make_envelope())
        assert row["execution_id"] == "exec-123"
        assert row["record_type"] == "xyz.huybrechts.strata.deployment.completed"
        assert row["deployment"] == "my-deploy"
        assert row["workspace"] == "my-workspace"
        assert row["environment"] == "prd"
        assert row["tenant"] == "acme"
        assert row["action"] == "deployment-completed"
        assert row["outcome"] == "success"

    def test_stores_whole_envelope_verbatim_as_payload(self) -> None:
        envelope = _make_envelope()
        row = extract_row(envelope)
        assert row["payload"] == envelope

    def test_parses_recorded_at_from_time_field(self) -> None:
        row = extract_row(_make_envelope())
        assert row["recorded_at"] == datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)

    def test_missing_time_falls_back_to_now(self) -> None:
        envelope = _make_envelope()
        del envelope["time"]
        row = extract_row(envelope)
        assert isinstance(row["recorded_at"], datetime)

    def test_unparseable_time_falls_back_to_now(self) -> None:
        row = extract_row(_make_envelope(time="not-a-timestamp"))
        assert isinstance(row["recorded_at"], datetime)

    def test_missing_type_raises_value_error(self) -> None:
        envelope = _make_envelope()
        del envelope["type"]
        with pytest.raises(ValueError, match="type"):
            extract_row(envelope)

    def test_missing_execution_id_raises_value_error(self) -> None:
        envelope = _make_envelope()
        del envelope["data"]["labels"]["execution_id"]
        with pytest.raises(ValueError, match="execution_id"):
            extract_row(envelope)

    def test_missing_data_raises_value_error(self) -> None:
        envelope = _make_envelope()
        del envelope["data"]
        with pytest.raises(ValueError, match="execution_id"):
            extract_row(envelope)

    def test_ring_and_strata_version_default_to_none(self) -> None:
        """Neither is populated by _build_envelope() yet — future producer-side work."""
        row = extract_row(_make_envelope())
        assert row["ring"] is None
        assert row["strata_version"] is None

    def test_missing_optional_dimensions_default_to_none(self) -> None:
        envelope = _make_envelope()
        envelope["data"]["labels"] = {"execution_id": "exec-123"}
        row = extract_row(envelope)
        assert row["deployment"] is None
        assert row["workspace"] is None
        assert row["environment"] is None
        assert row["tenant"] is None
