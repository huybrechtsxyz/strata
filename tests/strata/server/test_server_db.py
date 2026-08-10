"""Tests for the strata state-service event-store schema, engine, and idempotent
insert (ADR-0065 Step 2.2).

`sqlalchemy` is a real dev dependency (needs no external service for sqlite),
so these tests run against real (in-memory or temp-file) SQLite databases —
no fakes needed.
"""

from __future__ import annotations

from typing import Generator
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine

from strata.server.db.engine import check_connection, create_engine_from_url
from strata.server.db.schema import events, metadata
from strata.server.db.store import insert_event


@pytest.fixture
def sqlite_engine() -> Generator[Engine, None, None]:
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine, checkfirst=True)
    yield engine
    engine.dispose()


class TestCreateEngineFromUrl:
    def test_sqlite_url_is_supported(self) -> None:
        engine = create_engine_from_url("sqlite:///:memory:")
        try:
            assert engine.dialect.name == "sqlite"
        finally:
            engine.dispose()

    def test_postgresql_url_is_supported(self) -> None:
        # psycopg is a separate opt-in extra (server-postgres), deliberately not
        # installed in dev — patch sqlalchemy.create_engine itself so the dialect
        # driver is never actually imported, while our own validation still runs.
        with patch("sqlalchemy.create_engine") as mock_create_engine:
            create_engine_from_url("postgresql+psycopg://user:pass@localhost/db")
        mock_create_engine.assert_called_once_with("postgresql+psycopg://user:pass@localhost/db")

    def test_mssql_url_is_supported(self) -> None:
        # pyodbc is a separate opt-in extra (server-mssql), deliberately not
        # installed in dev — same patching approach as the postgresql case above.
        with patch("sqlalchemy.create_engine") as mock_create_engine:
            create_engine_from_url("mssql+pyodbc://user:pass@localhost/db")
        mock_create_engine.assert_called_once_with("mssql+pyodbc://user:pass@localhost/db")

    def test_unsupported_backend_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unsupported database backend"):
            create_engine_from_url("mysql://user:pass@localhost/db")


class TestCheckConnection:
    def test_reachable_database_returns_true(self, sqlite_engine: Engine) -> None:
        ok, detail = check_connection(sqlite_engine)
        assert ok is True
        assert detail == ""

    def test_unreachable_database_returns_false(self) -> None:
        from unittest.mock import MagicMock

        broken_engine = MagicMock()
        broken_engine.connect.side_effect = RuntimeError("connection refused")
        ok, detail = check_connection(broken_engine)
        assert ok is False
        assert "connection refused" in detail


class TestSchema:
    def test_events_table_has_expected_columns(self) -> None:
        column_names = {c.name for c in events.columns}
        assert column_names == {
            "execution_id",
            "record_type",
            "recorded_at",
            "received_at",
            "deployment",
            "workspace",
            "environment",
            "tenant",
            "ring",
            "action",
            "outcome",
            "strata_version",
            "payload",
        }

    def test_primary_key_is_execution_id_and_record_type(self) -> None:
        pk_columns = {c.name for c in events.primary_key.columns}
        assert pk_columns == {"execution_id", "record_type"}

    def test_create_all_creates_table_and_indexes(self, sqlite_engine: Engine) -> None:
        inspector = inspect(sqlite_engine)
        assert "events" in inspector.get_table_names()
        index_names = {ix["name"] for ix in inspector.get_indexes("events")}
        assert "idx_events_recorded_at" in index_names
        assert "idx_events_slice" in index_names

    def test_create_all_is_idempotent(self, sqlite_engine: Engine) -> None:
        # checkfirst=True must make a second call a no-op, not an error.
        metadata.create_all(sqlite_engine, checkfirst=True)
        inspector = inspect(sqlite_engine)
        assert "events" in inspector.get_table_names()


class TestInsertEvent:
    def _row(self, execution_id: str = "exec-1", record_type: str = "deploy-log") -> dict:
        import datetime

        return {
            "execution_id": execution_id,
            "record_type": record_type,
            "recorded_at": datetime.datetime.now(datetime.timezone.utc),
            "deployment": "my-deploy",
            "payload": {"foo": "bar"},
        }

    def test_insert_new_row_returns_true(self, sqlite_engine: Engine) -> None:
        assert insert_event(sqlite_engine, self._row()) is True

    def test_duplicate_insert_returns_false_and_is_a_noop(self, sqlite_engine: Engine) -> None:
        row = self._row()
        assert insert_event(sqlite_engine, row) is True
        assert insert_event(sqlite_engine, dict(row)) is False

        with sqlite_engine.connect() as conn:
            count = conn.execute(events.select()).fetchall()
        assert len(count) == 1

    def test_different_record_type_same_execution_id_both_insert(self, sqlite_engine: Engine) -> None:
        assert insert_event(sqlite_engine, self._row(record_type="deploy-log")) is True
        assert insert_event(sqlite_engine, self._row(record_type="cost-history")) is True

        with sqlite_engine.connect() as conn:
            rows = conn.execute(events.select()).fetchall()
        assert len(rows) == 2
