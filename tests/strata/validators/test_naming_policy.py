"""Tests for NamingPolicy — regex pattern enforcement on configuration names.

The policy runs at the ``validate`` phase and verifies that the name on
``context.configuration_service.model.meta.name`` matches the configured regex.
"""

from unittest.mock import MagicMock

import pytest

try:
    from strata.models.policy_model import PolicyModel
    from strata.validators.policies.base_policy import PolicyContext, PolicyResult
    from strata.validators.policies.naming_policy import NamingPolicy

    IMPL_MISSING = False
except ImportError:
    NamingPolicy = None  # type: ignore[assignment,misc]
    PolicyContext = None  # type: ignore[assignment,misc]
    PolicyResult = None  # type: ignore[assignment,misc]
    PolicyModel = None  # type: ignore[assignment,misc]
    IMPL_MISSING = True

pytestmark = pytest.mark.skipif(IMPL_MISSING, reason="NamingPolicy not yet implemented")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_PATTERN = "^[a-z][a-z0-9-]*$"


def make_policy(pattern: str = _DEFAULT_PATTERN, configuration=None) -> "PolicyModel":
    cfg = configuration if configuration is not None else {"pattern": pattern}
    return PolicyModel.model_validate(
        {
            "name": "naming-policy",
            "type": "naming_pattern",
            "phase": "validate",
            "enforcement": "deny",
            "configuration": cfg,
        }
    )


def make_context(name: str | None = None, service: bool = True) -> "PolicyContext":
    svc = None
    if service:
        svc = MagicMock()
        if name is not None:
            svc.model.meta.name = name
        else:
            svc.model = None
    return PolicyContext(phase="validate", work_path=None, configuration_service=svc)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNamingPolicy:
    def test_passes_when_name_matches_pattern(self):
        """name='my-config' matches '^[a-z][a-z0-9-]*$' → passed=True, no violations."""
        policy = NamingPolicy(make_policy())
        ctx = make_context(name="my-config")

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.violations == []

    def test_fails_when_name_does_not_match(self):
        """name='MyConfig' does not match lowercase pattern → passed=False, violation names the value."""
        policy = NamingPolicy(make_policy())
        ctx = make_context(name="MyConfig")

        result = policy.evaluate(ctx)

        assert result.passed is False
        assert len(result.violations) == 1
        assert "MyConfig" in result.violations[0]
        assert _DEFAULT_PATTERN in result.violations[0]

    def test_skip_when_no_configuration_service(self):
        """context.configuration_service is None → passed=True, details['skipped'] set."""
        policy = NamingPolicy(make_policy())
        ctx = make_context(service=False)

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.details is not None
        assert result.details.get("skipped")

    def test_skip_when_no_pattern_configured(self):
        """configuration={} (no 'pattern' key) → passed=True, skipped."""
        policy = NamingPolicy(make_policy(configuration={}))
        ctx = make_context(name="my-config")

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.details is not None
        assert result.details.get("skipped")

    def test_skip_when_model_is_none(self):
        """configuration_service.model is None → passed=True, skipped."""
        policy = NamingPolicy(make_policy())
        ctx = make_context(name=None, service=True)  # service=True but name=None → model=None

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.details is not None
        assert result.details.get("skipped")

    def test_fails_with_underscore_in_name(self):
        """name='my_config' contains underscore not allowed by pattern → fails."""
        policy = NamingPolicy(make_policy())
        ctx = make_context(name="my_config")

        result = policy.evaluate(ctx)

        assert result.passed is False
        assert len(result.violations) == 1
        assert "my_config" in result.violations[0]

    def test_passes_with_alphanumeric_name(self):
        """name='abc123' is purely alphanumeric — valid against '^[a-z][a-z0-9-]*$'."""
        policy = NamingPolicy(make_policy())
        ctx = make_context(name="abc123")

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.violations == []
