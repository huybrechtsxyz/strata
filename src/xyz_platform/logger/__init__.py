#!/usr/bin/env python3
"""Structured logging for xyz-platform, powered by structlog."""

from .context import (
    LogContext,
    clear_context,
    get_context,
    get_correlation_id,
    set_context,
    set_correlation_id,
)
from .decorators import log_performance, trace_operation
from .logger import (
    configure_logging,
    get_active_log_file,
    get_active_log_files,
    get_logger,
    reconfigure_logging,
    shutdown_logging,
)

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
