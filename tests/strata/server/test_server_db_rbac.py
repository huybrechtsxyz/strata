"""Tests for role-binding persistence and access resolution (ADR-0067 Step 9)."""

from __future__ import annotations

from typing import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from strata.server.auth.rbac import CAPABILITY_DEPLOYER, Tier
from strata.server.db.rbac import create_binding, list_bindings, resolve_access, revoke_binding
from strata.server.db.schema import metadata


@pytest.fixture
def sqlite_engine() -> Generator[Engine, None, None]:
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine, checkfirst=True)
    yield engine
    engine.dispose()


class TestCreateBinding:
    def test_creates_a_tier_binding(self, sqlite_engine: Engine) -> None:
        binding_id = create_binding(sqlite_engine, subject_type="user", subject="user-123", tier="admin")
        assert binding_id

    def test_creates_a_capability_only_binding(self, sqlite_engine: Engine) -> None:
        binding_id = create_binding(
            sqlite_engine, subject_type="group", subject="repo:acme/widgets", capabilities=[CAPABILITY_DEPLOYER]
        )
        assert binding_id

    def test_rejects_unknown_subject_type(self, sqlite_engine: Engine) -> None:
        with pytest.raises(ValueError, match="subject_type"):
            create_binding(sqlite_engine, subject_type="robot", subject="x", tier="admin")

    def test_rejects_unknown_tier(self, sqlite_engine: Engine) -> None:
        with pytest.raises(ValueError, match="unknown tier"):
            create_binding(sqlite_engine, subject_type="user", subject="x", tier="superadmin")

    def test_rejects_unknown_capability(self, sqlite_engine: Engine) -> None:
        with pytest.raises(ValueError, match="unknown capability"):
            create_binding(sqlite_engine, subject_type="user", subject="x", capabilities=["approve-everything"])

    def test_rejects_binding_granting_nothing(self, sqlite_engine: Engine) -> None:
        with pytest.raises(ValueError, match="must grant"):
            create_binding(sqlite_engine, subject_type="user", subject="x")

    def test_two_bindings_are_unique(self, sqlite_engine: Engine) -> None:
        first = create_binding(sqlite_engine, subject_type="user", subject="x", tier="viewer")
        second = create_binding(sqlite_engine, subject_type="user", subject="x", tier="viewer")
        assert first != second


class TestListBindings:
    def test_lists_created_bindings(self, sqlite_engine: Engine) -> None:
        create_binding(sqlite_engine, subject_type="user", subject="a", tier="viewer")
        create_binding(sqlite_engine, subject_type="user", subject="b", tier="admin", workspace="prod")

        bindings = list_bindings(sqlite_engine)

        assert len(bindings) == 2
        assert {b["subject"] for b in bindings} == {"a", "b"}

    def test_filters_by_workspace(self, sqlite_engine: Engine) -> None:
        create_binding(sqlite_engine, subject_type="user", subject="a", tier="viewer", workspace="prod")
        create_binding(sqlite_engine, subject_type="user", subject="b", tier="viewer", workspace="staging")

        bindings = list_bindings(sqlite_engine, workspace="prod")

        assert len(bindings) == 1
        assert bindings[0]["subject"] == "a"


class TestRevokeBinding:
    def test_revoke_active_binding_returns_true(self, sqlite_engine: Engine) -> None:
        binding_id = create_binding(sqlite_engine, subject_type="user", subject="a", tier="viewer")
        assert revoke_binding(sqlite_engine, binding_id) is True

    def test_revoked_binding_no_longer_grants_access(self, sqlite_engine: Engine) -> None:
        binding_id = create_binding(sqlite_engine, subject_type="user", subject="a", tier="admin")
        revoke_binding(sqlite_engine, binding_id)

        access = resolve_access(sqlite_engine, [("user", "a")])

        assert access.tier is None

    def test_revoke_unknown_binding_returns_false(self, sqlite_engine: Engine) -> None:
        assert revoke_binding(sqlite_engine, "does-not-exist") is False

    def test_revoke_already_revoked_binding_returns_false(self, sqlite_engine: Engine) -> None:
        binding_id = create_binding(sqlite_engine, subject_type="user", subject="a", tier="viewer")
        revoke_binding(sqlite_engine, binding_id)
        assert revoke_binding(sqlite_engine, binding_id) is False


class TestResolveAccess:
    def test_no_candidates_yields_no_access(self, sqlite_engine: Engine) -> None:
        access = resolve_access(sqlite_engine, [])
        assert access.tier is None

    def test_matches_user_binding(self, sqlite_engine: Engine) -> None:
        create_binding(sqlite_engine, subject_type="user", subject="user-123", tier="contributor")
        access = resolve_access(sqlite_engine, [("user", "user-123")])
        assert access.tier == Tier.CONTRIBUTOR

    def test_matches_group_binding(self, sqlite_engine: Engine) -> None:
        create_binding(sqlite_engine, subject_type="group", subject="platform-team", tier="approver")
        access = resolve_access(sqlite_engine, [("user", "user-123"), ("group", "platform-team")])
        assert access.tier == Tier.APPROVER

    def test_unions_multiple_matching_bindings(self, sqlite_engine: Engine) -> None:
        create_binding(sqlite_engine, subject_type="user", subject="user-123", tier="viewer")
        create_binding(sqlite_engine, subject_type="group", subject="deployers", capabilities=[CAPABILITY_DEPLOYER])

        access = resolve_access(sqlite_engine, [("user", "user-123"), ("group", "deployers")])

        assert access.tier == Tier.VIEWER
        assert access.has_capability(CAPABILITY_DEPLOYER)

    def test_global_binding_applies_everywhere(self, sqlite_engine: Engine) -> None:
        create_binding(sqlite_engine, subject_type="user", subject="user-123", tier="admin")  # workspace=None
        access = resolve_access(sqlite_engine, [("user", "user-123")], workspace="prod", environment="live")
        assert access.tier == Tier.ADMIN

    def test_workspace_scoped_binding_does_not_apply_to_a_different_workspace(self, sqlite_engine: Engine) -> None:
        create_binding(sqlite_engine, subject_type="user", subject="user-123", tier="admin", workspace="staging")
        access = resolve_access(sqlite_engine, [("user", "user-123")], workspace="prod")
        assert access.tier is None

    def test_workspace_scoped_binding_does_not_satisfy_a_global_check(self, sqlite_engine: Engine) -> None:
        """Managing RBAC bindings is platform-wide — a workspace-scoped admin must not qualify."""
        create_binding(sqlite_engine, subject_type="user", subject="user-123", tier="admin", workspace="prod")
        access = resolve_access(sqlite_engine, [("user", "user-123")])  # workspace=None -> global check
        assert access.tier is None

    def test_environment_scoped_binding_requires_matching_environment(self, sqlite_engine: Engine) -> None:
        create_binding(
            sqlite_engine,
            subject_type="user",
            subject="user-123",
            tier="contributor",
            workspace="prod",
            environment="staging-env",
        )
        access_matching = resolve_access(
            sqlite_engine, [("user", "user-123")], workspace="prod", environment="staging-env"
        )
        access_different = resolve_access(
            sqlite_engine, [("user", "user-123")], workspace="prod", environment="prod-env"
        )

        assert access_matching.tier == Tier.CONTRIBUTOR
        assert access_different.tier is None

    def test_admin_tier_implies_deployer_capability_via_resolution(self, sqlite_engine: Engine) -> None:
        create_binding(sqlite_engine, subject_type="user", subject="user-123", tier="admin")
        access = resolve_access(sqlite_engine, [("user", "user-123")])
        assert access.has_capability(CAPABILITY_DEPLOYER)

    def test_non_admin_tier_does_not_imply_deployer_capability(self, sqlite_engine: Engine) -> None:
        create_binding(sqlite_engine, subject_type="user", subject="user-123", tier="contributor")
        access = resolve_access(sqlite_engine, [("user", "user-123")])
        assert not access.has_capability(CAPABILITY_DEPLOYER)
