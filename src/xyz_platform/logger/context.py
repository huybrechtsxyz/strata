#!/usr/bin/env python3
"""
===============================================================================
Script Name   : context.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Context management for logging (correlation IDs, context data)
===============================================================================
"""

import contextvars
from typing import Dict, Any, Optional

# Context variables for correlation ID and context data
_correlation_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "correlation_id", default=None
)
_log_context: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    "log_context", default={}
)


def set_correlation_id(correlation_id: str) -> None:
    """
    Set correlation ID for the current context.

    This ID will be included in all log messages within this context,
    useful for tracing requests across services.

    Args:
        correlation_id: Unique identifier for correlation (e.g., request ID).

    Example:
        set_correlation_id("req-abc-123")
        logger.info("Processing request")  # Includes correlation_id
    """
    _correlation_id.set(correlation_id)


def get_correlation_id() -> Optional[str]:
    """
    Get the current correlation ID.

    Returns:
        Current correlation ID or None if not set.
    """
    return _correlation_id.get()


def set_context(context: Optional[Dict[str, Any]]) -> None:
    """
    Set context data for logging.

    Args:
        context: Dictionary of context data to include in logs, or None to clear.
    """
    _log_context.set(context or {})


def get_context() -> Dict[str, Any]:
    """
    Get the current logging context.

    Returns:
        Current context dictionary.
    """
    return _log_context.get()


def clear_context() -> None:
    """Clear all context data and correlation ID."""
    _correlation_id.set(None)
    _log_context.set({})


class LogContext:
    """
    Context manager for scoped logging context.

    Usage:
        with LogContext(user_id=123, tenant="acme"):
            logger.info("User action")  # Includes user_id and tenant in logs
    """

    def __init__(self, **kwargs):
        """
        Initialize log context.

        Args:
            **kwargs: Key-value pairs to include in logging context.
        """
        self.context = kwargs
        self.previous_context = None

    def __enter__(self):
        """Enter the context."""
        self.previous_context = get_context()
        # Merge with existing context
        new_context = {**self.previous_context, **self.context}
        set_context(new_context)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the context and restore previous context."""
        set_context(self.previous_context)
        return False
