"""Tests for token create/list/revoke/verify (ADR-0065 Step 2.4)."""

from __future__ import annotations

from typing import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from strata.server.db.schema import metadata
from strata.server.db.tokens import create_token, list_tokens, revoke_token, verify_token


@pytest.fixture
def sqlite_engine() -> Generator[Engine, None, None]:
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine, checkfirst=True)
    yield engine
    engine.dispose()


class TestCreateToken:
    def test_returns_token_id_and_secret(self, sqlite_engine: Engine) -> None:
        result = create_token(sqlite_engine, "my-workspace")
        assert result["token_id"]
        assert result["token"]

    def test_secret_is_not_the_stored_value(self, sqlite_engine: Engine) -> None:
        """Only the hash is persisted — the plaintext secret must never round-trip from storage."""
        from strata.server.db.schema import tokens

        result = create_token(sqlite_engine, "my-workspace")
        with sqlite_engine.connect() as conn:
            row = conn.execute(tokens.select()).mappings().first()
        assert row is not None
        assert row["token_hash"] != result["token"]

    def test_two_tokens_are_unique(self, sqlite_engine: Engine) -> None:
        first = create_token(sqlite_engine, "my-workspace")
        second = create_token(sqlite_engine, "my-workspace")
        assert first["token_id"] != second["token_id"]
        assert first["token"] != second["token"]


class TestVerifyToken:
    def test_valid_token_returns_workspace(self, sqlite_engine: Engine) -> None:
        created = create_token(sqlite_engine, "my-workspace")
        assert verify_token(sqlite_engine, created["token"]) == "my-workspace"

    def test_unknown_token_returns_none(self, sqlite_engine: Engine) -> None:
        assert verify_token(sqlite_engine, "not-a-real-token") is None

    def test_revoked_token_returns_none(self, sqlite_engine: Engine) -> None:
        created = create_token(sqlite_engine, "my-workspace")
        revoke_token(sqlite_engine, created["token_id"])
        assert verify_token(sqlite_engine, created["token"]) is None


class TestListTokens:
    def test_lists_all_tokens_when_no_filter(self, sqlite_engine: Engine) -> None:
        create_token(sqlite_engine, "workspace-a")
        create_token(sqlite_engine, "workspace-b")
        assert len(list_tokens(sqlite_engine)) == 2

    def test_filters_by_workspace(self, sqlite_engine: Engine) -> None:
        create_token(sqlite_engine, "workspace-a")
        create_token(sqlite_engine, "workspace-b")
        result = list_tokens(sqlite_engine, workspace="workspace-a")
        assert len(result) == 1
        assert result[0]["workspace"] == "workspace-a"

    def test_never_includes_hash_or_secret(self, sqlite_engine: Engine) -> None:
        create_token(sqlite_engine, "my-workspace")
        result = list_tokens(sqlite_engine)
        assert "token_hash" not in result[0]
        assert "token" not in result[0]

    def test_includes_revoked_at_when_revoked(self, sqlite_engine: Engine) -> None:
        created = create_token(sqlite_engine, "my-workspace")
        revoke_token(sqlite_engine, created["token_id"])
        result = list_tokens(sqlite_engine)
        assert result[0]["revoked_at"] is not None

    def test_active_token_has_no_revoked_at(self, sqlite_engine: Engine) -> None:
        create_token(sqlite_engine, "my-workspace")
        result = list_tokens(sqlite_engine)
        assert result[0]["revoked_at"] is None


class TestRevokeToken:
    def test_revoking_active_token_returns_true(self, sqlite_engine: Engine) -> None:
        created = create_token(sqlite_engine, "my-workspace")
        assert revoke_token(sqlite_engine, created["token_id"]) is True

    def test_revoking_unknown_token_returns_false(self, sqlite_engine: Engine) -> None:
        assert revoke_token(sqlite_engine, "does-not-exist") is False

    def test_revoking_already_revoked_token_returns_false(self, sqlite_engine: Engine) -> None:
        created = create_token(sqlite_engine, "my-workspace")
        revoke_token(sqlite_engine, created["token_id"])
        assert revoke_token(sqlite_engine, created["token_id"]) is False
