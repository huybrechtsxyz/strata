#!/usr/bin/env python3
"""Unit tests for BasePolicy._missing_data() (ADR-0082 Phase 2)."""

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult


class _DummyPolicy(BasePolicy):
    """Minimal concrete BasePolicy for exercising _missing_data() in isolation."""

    def evaluate(self, context: PolicyContext) -> PolicyResult:  # pragma: no cover - unused
        raise NotImplementedError


def _make_policy(on_missing_data=None, enforcement="deny") -> _DummyPolicy:
    kwargs = {}
    if on_missing_data is not None:
        kwargs["on_missing_data"] = on_missing_data
    model = PolicyModel(
        name="dummy_policy",
        type="dummy",
        phase="build",
        enforcement=enforcement,
        **kwargs,
    )
    return _DummyPolicy(model)


class TestBasePolicyMissingData:
    def test_default_skip_passes_with_skipped_detail(self):
        policy = _make_policy()
        result = policy._missing_data("thing not found")
        assert result.passed is True
        assert result.violations == []
        assert result.warnings == []
        assert result.details == {"skipped": "thing not found"}

    def test_warn_passes_with_surfaced_warning(self):
        policy = _make_policy(on_missing_data="warn")
        result = policy._missing_data("thing not found")
        assert result.passed is True
        assert result.warnings == ["thing not found"]

    def test_block_fails_with_violation(self):
        policy = _make_policy(on_missing_data="block")
        result = policy._missing_data("thing not found")
        assert result.passed is False
        assert result.violations == ["Required data unavailable: thing not found"]
        assert result.warnings == []
        assert result.details is None

    def test_block_respects_configured_enforcement_level(self):
        """on_missing_data and enforcement are orthogonal knobs — a warn-enforcement
        policy with on_missing_data: block still only reports the enforcement it
        was configured with; it doesn't escalate to deny."""
        policy = _make_policy(on_missing_data="block", enforcement="warn")
        result = policy._missing_data("thing not found")
        assert result.passed is False
        assert result.enforcement == "warn"

    def test_result_carries_policy_identity(self):
        policy = _make_policy()
        result = policy._missing_data("thing not found")
        assert result.policy_name == "dummy_policy"
        assert result.policy_type == "dummy"
