"""Tests for the human-login session store (ADR-0067 Step 8)."""

from __future__ import annotations

from typing import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from strata.server.db.schema import metadata
from strata.server.db.sessions import create_session, get_session, list_sessions, revoke_session, touch_session


@pytest.fixture
def sqlite_engine() -> Generator[Engine, None, None]:
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine, checkfirst=True)
    yield engine
    engine.dispose()


class TestCreateSession:
    def test_returns_a_session_id(self, sqlite_engine: Engine) -> None:
        session_id = create_session(sqlite_engine, subject="user-1", encrypted_refresh_token="enc-blob")
        assert session_id

    def test_two_sessions_are_unique(self, sqlite_engine: Engine) -> None:
        first = create_session(sqlite_engine, subject="user-1", encrypted_refresh_token="enc-blob")
        second = create_session(sqlite_engine, subject="user-1", encrypted_refresh_token="enc-blob")
        assert first != second


class TestGetSession:
    def test_returns_active_session(self, sqlite_engine: Engine) -> None:
        session_id = create_session(
            sqlite_engine, subject="user-1", encrypted_refresh_token="enc-blob", email="user@example.test"
        )
        row = get_session(sqlite_engine, session_id)
        assert row is not None
        assert row["subject"] == "user-1"
        assert row["email"] == "user@example.test"
        assert row["encrypted_refresh_token"] == "enc-blob"

    def test_unknown_session_returns_none(self, sqlite_engine: Engine) -> None:
        assert get_session(sqlite_engine, "does-not-exist") is None

    def test_revoked_session_returns_none(self, sqlite_engine: Engine) -> None:
        session_id = create_session(sqlite_engine, subject="user-1", encrypted_refresh_token="enc-blob")
        revoke_session(sqlite_engine, session_id)
        assert get_session(sqlite_engine, session_id) is None


class TestListSessions:
    def test_lists_all_sessions_never_the_encrypted_token(self, sqlite_engine: Engine) -> None:
        create_session(sqlite_engine, subject="user-1", encrypted_refresh_token="enc-blob-1")
        create_session(sqlite_engine, subject="user-2", encrypted_refresh_token="enc-blob-2")

        rows = list_sessions(sqlite_engine)

        assert len(rows) == 2
        subjects = {row["subject"] for row in rows}
        assert subjects == {"user-1", "user-2"}
        for row in rows:
            assert "encrypted_refresh_token" not in row

    def test_includes_revoked_sessions_with_revoked_at_set(self, sqlite_engine: Engine) -> None:
        session_id = create_session(sqlite_engine, subject="user-1", encrypted_refresh_token="enc-blob")
        revoke_session(sqlite_engine, session_id)

        rows = list_sessions(sqlite_engine)

        assert len(rows) == 1
        assert rows[0]["revoked_at"] is not None

    def test_empty_store_returns_empty_list(self, sqlite_engine: Engine) -> None:
        assert list_sessions(sqlite_engine) == []


class TestRevokeSession:
    def test_revoking_active_session_returns_true(self, sqlite_engine: Engine) -> None:
        session_id = create_session(sqlite_engine, subject="user-1", encrypted_refresh_token="enc-blob")
        assert revoke_session(sqlite_engine, session_id) is True

    def test_revoking_unknown_session_returns_false(self, sqlite_engine: Engine) -> None:
        assert revoke_session(sqlite_engine, "does-not-exist") is False

    def test_revoking_already_revoked_session_returns_false(self, sqlite_engine: Engine) -> None:
        session_id = create_session(sqlite_engine, subject="user-1", encrypted_refresh_token="enc-blob")
        assert revoke_session(sqlite_engine, session_id) is True
        assert revoke_session(sqlite_engine, session_id) is False


class TestTouchSession:
    def test_updates_last_refreshed_at(self, sqlite_engine: Engine) -> None:
        session_id = create_session(sqlite_engine, subject="user-1", encrypted_refresh_token="enc-blob")
        row = get_session(sqlite_engine, session_id)
        assert row is not None
        assert row["last_refreshed_at"] is None

        touch_session(sqlite_engine, session_id)

        row = get_session(sqlite_engine, session_id)
        assert row is not None
        assert row["last_refreshed_at"] is not None

    def test_replaces_encrypted_refresh_token_when_rotated(self, sqlite_engine: Engine) -> None:
        session_id = create_session(sqlite_engine, subject="user-1", encrypted_refresh_token="enc-blob-old")

        touch_session(sqlite_engine, session_id, encrypted_refresh_token="enc-blob-new")

        row = get_session(sqlite_engine, session_id)
        assert row is not None
        assert row["encrypted_refresh_token"] == "enc-blob-new"

    def test_keeps_existing_refresh_token_when_not_rotated(self, sqlite_engine: Engine) -> None:
        session_id = create_session(sqlite_engine, subject="user-1", encrypted_refresh_token="enc-blob")

        touch_session(sqlite_engine, session_id)

        row = get_session(sqlite_engine, session_id)
        assert row is not None
        assert row["encrypted_refresh_token"] == "enc-blob"
