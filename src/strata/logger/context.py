#!/usr/bin/env python3
"""
Context management for structured logging.

Wraps structlog.contextvars so callers never import structlog directly.
All bound values are automatically merged into every log entry within the
same async task / thread via the ``merge_contextvars`` processor in
SHARED_PROCESSORS.
"""

from typing import Any, Optional

import structlog.contextvars


def set_correlation_id(correlation_id: str) -> None:
    """
    Bind a correlation ID to all log entries in the current context.

    Args:
        correlation_id: Unique identifier (e.g. request ID, job ID).

    Example::

        set_correlation_id("req-abc-123")
        log.info("processing")  # → {..., "correlation_id": "req-abc-123"}
    """
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)


def get_correlation_id() -> Optional[str]:
    """Return the correlation ID bound to the current context, or ``None``."""
    return structlog.contextvars.get_contextvars().get("correlation_id")


def set_context(context: Optional[dict[str, Any]]) -> None:
    """
    Bind arbitrary key-value pairs to the current logging context.

    Merges with any existing context. Pass ``None`` to clear everything.

    Args:
        context: Key-value pairs to include in all subsequent log entries.
    """
    if context:
        structlog.contextvars.bind_contextvars(**context)
    else:
        structlog.contextvars.clear_contextvars()


def get_context() -> dict[str, Any]:
    """Return all key-value pairs currently bound to the logging context."""
    return structlog.contextvars.get_contextvars()


def clear_context() -> None:
    """Remove all bound context values including the correlation ID."""
    structlog.contextvars.clear_contextvars()


class LogContext:
    """
    Context manager that binds structured fields for the duration of a block
    and cleanly removes them on exit.

    Example::

        with LogContext(user_id="u-42", tenant="acme"):
            log.info("user action")  # → {..., "user_id": "u-42", "tenant": "acme"}
        log.info("after block")      # → no user_id or tenant
    """

    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = kwargs

    def __enter__(self) -> "LogContext":
        structlog.contextvars.bind_contextvars(**self._kwargs)
        return self

    def __exit__(self, *args: Any) -> None:
        structlog.contextvars.unbind_contextvars(*self._kwargs.keys())
