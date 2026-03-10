#!/usr/bin/env python3
"""
===============================================================================
Script Name   : formatters.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Log formatters using python-json-logger (industry standard)
===============================================================================
"""

import logging
from datetime import datetime
from typing import Dict, Any

# Use standard python-json-logger library for JSON logging (ELK standard)
from pythonjsonlogger import json as jsonlogger

from .context import get_correlation_id, get_context


class JsonFormatter(jsonlogger.JsonFormatter):
    """
    JSON formatter using python-json-logger (industry standard for ELK).

    This is the standard library used across the Python ecosystem for
    JSON logging to ELK stack. Extends pythonjsonlogger.JsonFormatter.

    Output format:
    {
        "timestamp": "2026-02-06T10:30:45.123Z",
        "level": "INFO",
        "logger": "xyz_platform.module",
        "message": "Operation completed",
        "correlation_id": "abc-123",
        "context": {...},
        "source": {"file": "module.py", "line": 42, "function": "do_work"}
    }
    """

    def add_fields(
        self,
        log_record: Dict[str, Any],
        record: logging.LogRecord,
        message_dict: Dict[str, Any],
    ):
        """
        Add custom fields to the log record.

        Called by pythonjsonlogger to customize the JSON output.
        """
        super(JsonFormatter, self).add_fields(log_record, record, message_dict)

        # Ensure timestamp is in ISO 8601 format
        if not log_record.get("timestamp"):
            log_record["timestamp"] = (
                datetime.utcfromtimestamp(record.created).isoformat() + "Z"
            )

        # Rename 'levelname' to 'level' for consistency
        if "levelname" in log_record:
            log_record["level"] = log_record.pop("levelname")

        # Rename 'name' to 'logger' for clarity
        if "name" in log_record:
            log_record["logger"] = log_record.pop("name")

        # Add correlation ID if present
        correlation_id = get_correlation_id()
        if correlation_id:
            log_record["correlation_id"] = correlation_id

        # Add context data
        context = get_context()
        if context:
            log_record["context"] = context
            # Also add important fields at top level for easier Kibana filtering
            if "session_id" in context:
                log_record["session_id"] = context["session_id"]
            if "execution_id" in context:
                log_record["execution_id"] = context["execution_id"]

        # Add source location
        log_record["source"] = {
            "file": record.pathname,
            "line": record.lineno,
            "function": record.funcName,
        }


class ConsoleFormatter(logging.Formatter):
    """
    Human-readable console formatter for development.

    Outputs colored logs with correlation ID and context.
    Perfect for local development - clean, readable, with ANSI colors.
    """

    # ANSI color codes
    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    def __init__(self, use_colors: bool = True):
        """
        Initialize console formatter.

        Args:
            use_colors: Whether to use ANSI colors (disable for non-TTY).
        """
        super().__init__()
        self.use_colors = use_colors

    def format(self, record: logging.LogRecord) -> str:
        """Format log record for console output."""
        # Format timestamp
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")

        # Get log level with color
        level = record.levelname
        if self.use_colors and level in self.COLORS:
            level = f"{self.COLORS[level]}{self.BOLD}{level:8s}{self.RESET}"
        else:
            level = f"{level:8s}"

        # Format logger name (dimmed if colors enabled)
        logger_name = record.name
        if self.use_colors:
            logger_name = f"{self.DIM}{logger_name}{self.RESET}"

        # Base message
        parts = [f"{timestamp} {level} [{logger_name}] {record.getMessage()}"]

        # Add correlation ID if present
        correlation_id = get_correlation_id()
        if correlation_id:
            parts.append(f"  ├─ correlation_id: {correlation_id}")

        # Add context if present
        context = get_context()
        if context:
            for key, value in context.items():
                parts.append(f"  ├─ {key}: {value}")

        # Add exception info if present
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
            parts.append(f"  └─ Exception:\n{exc_text}")

        return "\n".join(parts)
