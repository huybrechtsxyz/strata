"""Tests for strata.logger.decorators — @log_performance and @trace_operation."""

import asyncio

import pytest
import structlog.contextvars

from strata.logger.decorators import log_performance, trace_operation


@pytest.fixture(autouse=True)
def clean_context():
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


# ---------------------------------------------------------------------------
# @log_performance — sync
# ---------------------------------------------------------------------------


class TestLogPerformanceSync:
    def test_returns_value_on_success(self):
        @log_performance
        def add(a, b):
            return a + b

        assert add(2, 3) == 5

    def test_reraises_exception(self):
        @log_performance
        def boom():
            raise ValueError("oops")

        with pytest.raises(ValueError, match="oops"):
            boom()

    def test_preserves_function_name(self):
        @log_performance
        def my_func():
            pass

        assert my_func.__name__ == "my_func"

    def test_wraps_non_coroutine_with_sync_wrapper(self):
        @log_performance
        def sync_fn():
            return "sync"

        assert not asyncio.iscoroutinefunction(sync_fn)


# ---------------------------------------------------------------------------
# @log_performance — async
# ---------------------------------------------------------------------------


class TestLogPerformanceAsync:
    def test_returns_value_on_success(self):
        @log_performance
        async def async_add(a, b):
            return a + b

        result = asyncio.get_event_loop().run_until_complete(async_add(2, 3))
        assert result == 5

    def test_reraises_exception(self):
        @log_performance
        async def async_boom():
            raise RuntimeError("async-oops")

        with pytest.raises(RuntimeError, match="async-oops"):
            asyncio.get_event_loop().run_until_complete(async_boom())

    def test_wraps_coroutine_with_async_wrapper(self):
        @log_performance
        async def async_fn():
            pass

        assert asyncio.iscoroutinefunction(async_fn)


# ---------------------------------------------------------------------------
# @trace_operation — sync
# ---------------------------------------------------------------------------


class TestTraceOperationSync:
    def test_binds_operation_during_call(self):
        observed = {}

        @trace_operation("provision-vm")
        def capture():
            observed.update(structlog.contextvars.get_contextvars())

        capture()
        assert observed.get("operation") == "provision-vm"

    def test_removes_operation_after_call(self):
        @trace_operation("provision-vm")
        def noop():
            pass

        noop()
        assert "operation" not in structlog.contextvars.get_contextvars()

    def test_removes_operation_even_on_exception(self):
        @trace_operation("provision-vm")
        def boom():
            raise ValueError("fail")

        with pytest.raises(ValueError):
            boom()

        assert "operation" not in structlog.contextvars.get_contextvars()

    def test_returns_value(self):
        @trace_operation("build")
        def returns_42():
            return 42

        assert returns_42() == 42

    def test_wraps_non_coroutine_with_sync_wrapper(self):
        @trace_operation("op")
        def sync_fn():
            pass

        assert not asyncio.iscoroutinefunction(sync_fn)


# ---------------------------------------------------------------------------
# @trace_operation — async
# ---------------------------------------------------------------------------


class TestTraceOperationAsync:
    def test_binds_operation_during_call(self):
        observed = {}

        @trace_operation("deploy-cluster")
        async def capture():
            observed.update(structlog.contextvars.get_contextvars())

        asyncio.get_event_loop().run_until_complete(capture())
        assert observed.get("operation") == "deploy-cluster"

    def test_removes_operation_after_call(self):
        @trace_operation("deploy-cluster")
        async def noop():
            pass

        asyncio.get_event_loop().run_until_complete(noop())
        assert "operation" not in structlog.contextvars.get_contextvars()

    def test_removes_operation_even_on_exception(self):
        @trace_operation("deploy-cluster")
        async def boom():
            raise ValueError("async-fail")

        with pytest.raises(ValueError):
            asyncio.get_event_loop().run_until_complete(boom())

        assert "operation" not in structlog.contextvars.get_contextvars()

    def test_wraps_coroutine_with_async_wrapper(self):
        @trace_operation("op")
        async def async_fn():
            pass

        assert asyncio.iscoroutinefunction(async_fn)
