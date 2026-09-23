#!/usr/bin/env python3
"""Tests for the capability ABCs and the pairing checker (ADR-0021 D1/D9)."""

from strata.integrations.base import Integration
from strata.integrations.capabilities import (
    CAPABILITY_ABCS,
    InfraIntegration,
    StoreIntegration,
    find_capability_mismatches,
)


def test_store_integration_requires_resolve():
    """A StoreIntegration subclass that doesn't implement resolve() can't be instantiated."""

    class _Incomplete(StoreIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"secrets"})
        TRANSPORTS = frozenset({"http"})

    import pytest

    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]


def test_infra_integration_requires_all_three_methods():
    class _Incomplete(InfraIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"infrastructure"})
        TRANSPORTS = frozenset({"cli"})

        def plan(self, path, **kwargs):  # only one of three
            raise NotImplementedError

    import pytest

    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]


def test_store_integration_can_be_instantiated_when_complete():
    class _Complete(StoreIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"secrets"})
        TRANSPORTS = frozenset({"http"})

        def resolve(self, key: str) -> str:
            return "value"

    assert _Complete().resolve("k") == "value"


# ---------------------------------------------------------------------------
# find_capability_mismatches — the pairing checker Phase 4's registry test
# reuses against real classes
# ---------------------------------------------------------------------------


def test_no_mismatch_for_a_compliant_store():
    class _Compliant(StoreIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"secrets", "variables"})
        TRANSPORTS = frozenset({"http"})

        def resolve(self, key: str) -> str:
            return "value"

    assert find_capability_mismatches(_Compliant) == []


def test_no_mismatch_for_a_compliant_infra_integration():
    class _Compliant(InfraIntegration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"infrastructure"})
        TRANSPORTS = frozenset({"cli"})

        def plan(self, path, **kwargs):
            raise NotImplementedError

        def deploy(self, path, **kwargs):
            raise NotImplementedError

        def destroy(self, path, **kwargs):
            raise NotImplementedError

    assert find_capability_mismatches(_Compliant) == []


def test_mismatch_when_a_plain_integration_declares_a_core_capability():
    """A bare Integration (no ABC) declaring 'secrets' is the exact programming
    error this function exists to catch."""

    class _Wrong(Integration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"secrets"})
        TRANSPORTS = frozenset({"http"})

    mismatches = find_capability_mismatches(_Wrong)
    assert len(mismatches) == 1
    assert "secrets" in mismatches[0]
    assert "StoreIntegration" in mismatches[0]


def test_no_mismatch_for_a_capability_with_no_abc_yet():
    """'sources' has no ABC (ADR-0021 D9) — declaring it alone is not an error."""

    class _SourceOnly(Integration):
        TYPE = "widget"
        CAPABILITIES = frozenset({"sources"})
        TRANSPORTS = frozenset({"http"})

    assert find_capability_mismatches(_SourceOnly) == []


def test_capability_abcs_covers_every_core_capability_with_a_real_consumer():
    """variables/secrets/features -> StoreIntegration; infrastructure/container -> InfraIntegration."""
    assert CAPABILITY_ABCS == {
        "variables": StoreIntegration,
        "secrets": StoreIntegration,
        "features": StoreIntegration,
        "infrastructure": InfraIntegration,
        "container": InfraIntegration,
    }


def test_every_registered_class_is_compliant():
    """The first real exercise of this checker (Phase 3 could only test it against
    fakes — `_KNOWN` didn't exist yet). Every class Phase 4 registers must comply."""
    from strata.integrations.registry import _KNOWN, get

    for integration_type in _KNOWN:
        instance = get(integration_type)
        assert find_capability_mismatches(type(instance)) == [], integration_type
