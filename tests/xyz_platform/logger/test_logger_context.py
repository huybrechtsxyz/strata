"""Tests for xyz_platform.logger.context — correlation ID and context binding."""

import pytest
import structlog.contextvars

from xyz_platform.logger.context import (
    clear_context,
    get_context,
    get_correlation_id,
    set_context,
    set_correlation_id,
)


@pytest.fixture(autouse=True)
def clean_context():
    """Ensure structlog context is clear before and after every test."""
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


class TestCorrelationId:
    def test_set_and_get_round_trip(self):
        set_correlation_id("req-abc-123")
        assert get_correlation_id() == "req-abc-123"

    def test_get_returns_none_when_not_set(self):
        assert get_correlation_id() is None

    def test_overwrite_replaces_previous_value(self):
        set_correlation_id("first")
        set_correlation_id("second")
        assert get_correlation_id() == "second"

    def test_clear_context_removes_correlation_id(self):
        set_correlation_id("req-xyz")
        clear_context()
        assert get_correlation_id() is None


class TestSetContext:
    def test_binds_arbitrary_keys(self):
        set_context({"component": "builder", "stage": "plan"})
        ctx = get_context()
        assert ctx["component"] == "builder"
        assert ctx["stage"] == "plan"

    def test_merges_with_existing_context(self):
        set_context({"a": 1})
        set_context({"b": 2})
        ctx = get_context()
        assert ctx["a"] == 1
        assert ctx["b"] == 2

    def test_none_clears_context(self):
        set_context({"key": "value"})
        set_context(None)
        assert get_context() == {}

    def test_empty_dict_does_not_raise(self):
        set_context({})
        assert get_context() == {}


class TestClearContext:
    def test_removes_all_keys(self):
        set_context({"x": 1, "y": 2})
        set_correlation_id("cid-1")
        clear_context()
        assert get_context() == {}
        assert get_correlation_id() is None

    def test_idempotent_on_empty_context(self):
        clear_context()
        clear_context()
        assert get_context() == {}
