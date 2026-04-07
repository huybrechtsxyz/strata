#!/usr/bin/env python3
"""Structured logging for xyz-platform, powered by structlog."""

from .logger import (
    get_logger,
    configure_logging,
    reconfigure_logging,
    shutdown_logging,
    get_active_log_file,
    get_active_log_files,
)
from .context import (
    LogContext,
    set_correlation_id,
    get_correlation_id,
    set_context,
    get_context,
    clear_context,
)
from .decorators import log_performance, trace_operation

__all__ = [
    "get_logger",
    "configure_logging",
    "reconfigure_logging",
    "shutdown_logging",
    "get_active_log_file",
    "get_active_log_files",
    "LogContext",
    "set_correlation_id",
    "get_correlation_id",
    "set_context",
    "get_context",
    "clear_context",
    "log_performance",
    "trace_operation",
]
