#!/usr/bin/env python3
"""
Logging decorators for performance tracking and operation tracing.

Both decorators are async-aware: they detect whether the wrapped function
is a coroutine and return the appropriate wrapper automatically.
"""

import asyncio
import functools
import time
from typing import Any, Callable

import structlog
import structlog.contextvars


def log_performance(func: Callable) -> Callable:
    """
    Decorator that logs execution time of sync and async functions.

    Emits an ``INFO`` event on success with ``duration_ms``, or an
    ``ERROR`` event (with ``exc_info``) if the function raises.

    Example::

        @log_performance
        def load_config(path: str) -> dict: ...

        @log_performance
        async def fetch_data(url: str) -> bytes: ...
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        log = structlog.get_logger(func.__module__)
        start = time.perf_counter()
        try:
            result = func(*args, **kwargs)
            log.info("performance", function=func.__name__, duration_ms=round((time.perf_counter() - start) * 1000, 2))
            return result
        except Exception:
            log.error("performance_error", function=func.__name__, duration_ms=round((time.perf_counter() - start) * 1000, 2), exc_info=True)
            raise

    @functools.wraps(func)
    async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
        log = structlog.get_logger(func.__module__)
        start = time.perf_counter()
        try:
            result = await func(*args, **kwargs)
            log.info("performance", function=func.__name__, duration_ms=round((time.perf_counter() - start) * 1000, 2))
            return result
        except Exception:
            log.error("performance_error", function=func.__name__, duration_ms=round((time.perf_counter() - start) * 1000, 2), exc_info=True)
            raise

    return async_wrapper if asyncio.iscoroutinefunction(func) else wrapper


def trace_operation(operation: str) -> Callable:
    """
    Decorator that binds an ``operation`` key to the log context for the
    duration of the call and removes it cleanly on exit.

    Example::

        @trace_operation("provision-vm")
        def provision(config: dict) -> None: ...

        @trace_operation("deploy-cluster")
        async def deploy(cluster_id: str) -> None: ...
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            structlog.contextvars.bind_contextvars(operation=operation)
            try:
                return func(*args, **kwargs)
            finally:
                structlog.contextvars.unbind_contextvars("operation")

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            structlog.contextvars.bind_contextvars(operation=operation)
            try:
                return await func(*args, **kwargs)
            finally:
                structlog.contextvars.unbind_contextvars("operation")

        return async_wrapper if asyncio.iscoroutinefunction(func) else wrapper

    return decorator
