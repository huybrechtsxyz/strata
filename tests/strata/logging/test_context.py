#!/usr/bin/env python3
"""Tests for strata.logging.context."""

from strata.logging.context import LogContext, bind_run, clear_context, get_context, get_run_id


def teardown_function() -> None:
    clear_context()


def test_bind_run_sets_run_id():
    bind_run("run-123")
    assert get_run_id() == "run-123"


def test_get_run_id_none_when_unbound():
    assert get_run_id() is None


def test_clear_context_removes_everything():
    bind_run("run-123")
    clear_context()
    assert get_context() == {}


def test_log_context_restores_previous_value_on_exit():
    """Nested LogContext blocks restore the shadowed outer value, not delete it."""
    with LogContext(stage="outer"):
        assert get_context()["stage"] == "outer"
        with LogContext(stage="inner"):
            assert get_context()["stage"] == "inner"
        assert get_context()["stage"] == "outer"
    assert get_context() == {}


def test_log_context_removes_key_on_exit_when_not_previously_bound():
    with LogContext(item="12345"):
        assert get_context()["item"] == "12345"
    assert "item" not in get_context()
