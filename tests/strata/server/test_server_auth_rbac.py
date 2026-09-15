"""Tests for the RBAC data model — tiers, capabilities, principals, and access
resolution (ADR-0067 Step 9). Pure logic, no DB/FastAPI involved.
"""

from __future__ import annotations

import pytest

from strata.server.auth.rbac import (
    CAPABILITY_DEPLOYER,
    Principal,
    ResolvedAccess,
    Tier,
    parse_tier,
    union_access,
)


class TestTierOrdering:
    def test_tiers_are_ordered(self) -> None:
        assert Tier.VIEWER < Tier.APPROVER < Tier.CONTRIBUTOR < Tier.ADMIN

    def test_parse_tier_is_case_insensitive(self) -> None:
        assert parse_tier("Admin") == Tier.ADMIN
        assert parse_tier("VIEWER") == Tier.VIEWER

    def test_parse_unknown_tier_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown tier"):
            parse_tier("superadmin")


class TestPrincipalCandidateBindings:
    def test_session_principal_matches_sub_as_user(self) -> None:
        principal = Principal(subject="user-123", auth_method="session")
        assert ("user", "user-123") in principal.candidate_bindings()

    def test_session_principal_matches_email_as_user_when_different_from_subject(self) -> None:
        principal = Principal(subject="user-123", email="user@example.test", auth_method="session")
        candidates = principal.candidate_bindings()
        assert ("user", "user-123") in candidates
        assert ("user", "user@example.test") in candidates

    def test_session_principal_does_not_duplicate_when_email_equals_subject(self) -> None:
        principal = Principal(subject="same@example.test", email="same@example.test", auth_method="session")
        candidates = principal.candidate_bindings()
        assert candidates.count(("user", "same@example.test")) == 1

    def test_session_principal_includes_groups(self) -> None:
        principal = Principal(subject="user-123", groups=("group-a", "group-b"), auth_method="session")
        candidates = principal.candidate_bindings()
        assert ("group", "group-a") in candidates
        assert ("group", "group-b") in candidates

    def test_m2m_principal_matches_only_as_group(self) -> None:
        principal = Principal(subject="repo:acme/widgets:ref:refs/heads/main", auth_method="m2m")
        assert principal.candidate_bindings() == [("group", "repo:acme/widgets:ref:refs/heads/main")]

    def test_m2m_principal_with_no_subject_has_no_candidates(self) -> None:
        principal = Principal(subject="", auth_method="m2m")
        assert principal.candidate_bindings() == []


class TestResolvedAccess:
    def test_no_tier_has_no_tier_access(self) -> None:
        access = ResolvedAccess()
        assert access.has_tier(Tier.VIEWER) is False

    def test_higher_tier_satisfies_lower_requirement(self) -> None:
        access = ResolvedAccess(tier=Tier.ADMIN)
        assert access.has_tier(Tier.VIEWER) is True
        assert access.has_tier(Tier.ADMIN) is True

    def test_lower_tier_does_not_satisfy_higher_requirement(self) -> None:
        access = ResolvedAccess(tier=Tier.VIEWER)
        assert access.has_tier(Tier.ADMIN) is False

    def test_capability_not_granted_by_default(self) -> None:
        access = ResolvedAccess(tier=Tier.CONTRIBUTOR)
        assert access.has_capability(CAPABILITY_DEPLOYER) is False

    def test_explicit_capability_grant(self) -> None:
        access = ResolvedAccess(capabilities=frozenset({CAPABILITY_DEPLOYER}))
        assert access.has_capability(CAPABILITY_DEPLOYER) is True

    def test_admin_tier_implies_every_capability(self) -> None:
        access = ResolvedAccess(tier=Tier.ADMIN)
        assert access.has_capability(CAPABILITY_DEPLOYER) is True
        assert access.has_capability("some-future-capability") is True


class TestUnionAccess:
    def test_empty_input_yields_no_access(self) -> None:
        access = union_access([], [])
        assert access.tier is None
        assert access.capabilities == frozenset()

    def test_takes_the_highest_tier(self) -> None:
        access = union_access([Tier.VIEWER, Tier.CONTRIBUTOR, Tier.APPROVER], [])
        assert access.tier == Tier.CONTRIBUTOR

    def test_unions_capabilities(self) -> None:
        access = union_access([], [CAPABILITY_DEPLOYER, CAPABILITY_DEPLOYER])
        assert access.capabilities == frozenset({CAPABILITY_DEPLOYER})

    def test_admin_tier_adds_deployer_capability_automatically(self) -> None:
        access = union_access([Tier.ADMIN], [])
        assert CAPABILITY_DEPLOYER in access.capabilities
