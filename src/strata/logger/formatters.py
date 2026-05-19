#!/usr/bin/env python3
"""
Structlog processor chain and formatter factories.

SHARED_PROCESSORS is the single source of truth for the common processor
pipeline used by both structlog.configure() and stdlib ProcessorFormatter.
"""

import sys

import structlog
import structlog.contextvars
import structlog.dev
import structlog.processors
import structlog.stdlib

# ---------------------------------------------------------------------------
# Shared processor chain
# Applied to every log entry before the final renderer.
# Imported by logger.py to keep structlog.configure() in sync.
# ---------------------------------------------------------------------------
SHARED_PROCESSORS: list = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.processors.StackInfoRenderer(),
]


def make_console_formatter() -> structlog.stdlib.ProcessorFormatter:
    """
    Human-readable colored formatter for TTY, plain JSON for non-TTY.

    Used by the StreamHandler attached to the root stdlib logger.
    structlog's ConsoleRenderer produces the familiar dev-friendly output
    with colors, level badges, and key=value pairs on one line.
    """
    renderer = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    return structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=SHARED_PROCESSORS,
    )


def make_json_formatter() -> structlog.stdlib.ProcessorFormatter:
    """
    JSON formatter for file sinks and Logstash (ELK-compatible).

    ExceptionRenderer converts exc_info to a serialisable dict before
    JSONRenderer serialises the whole event dict to a JSON string.
    """
    return structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.ExceptionRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=SHARED_PROCESSORS,
    )
