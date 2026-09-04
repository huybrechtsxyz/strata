#!/usr/bin/env python3
"""
Structlog processor chain and formatter factories.

SHARED_PROCESSORS is the single source of truth for the common processor
pipeline used by both structlog.configure() and stdlib ProcessorFormatter.
"""

import os
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
    # Respect the NO_COLOR env var (https://no-color.org) — any presence of
    # the variable, regardless of its value, disables ANSI colour output.
    want_colors = sys.stderr.isatty() and "NO_COLOR" not in os.environ
    try:
        renderer = structlog.dev.ConsoleRenderer(colors=want_colors)
    except SystemError:
        # structlog raises SystemError on Windows when colors=True is requested
        # but the optional `colorama` package isn't installed. Fall back to a
        # plain (uncoloured) renderer instead of crashing every CLI invocation.
        renderer = structlog.dev.ConsoleRenderer(colors=False)
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
