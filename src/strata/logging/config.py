#!/usr/bin/env python3
"""Logging configuration — the single place that decides where output goes.

structlog is routed through the standard library's root logger so that
third-party libraries land in the same stream and format, and so any stdlib-
based sink (file, Logstash, Azure Monitor's OpenTelemetry bridge) captures
every structlog entry automatically without touching a call site.

Sinks, all independently optional:
  console (default on) → StreamHandler + ConsoleRenderer (color on TTY, JSON otherwise)
  json_file_path        → FileHandler + JSON renderer
  logstash_host          → LogstashHandler (TCP) + JSON renderer, for a direct ELK pipe
  otlp                   → OpenTelemetry collector export (`otel.py`)
  azure_connection_string → Azure Application Insights, via its own OpenTelemetry bridge

Configuration arrives as arguments from the entry point — this module reads
no environment variables (aside from respecting `NO_COLOR`) and no config
files.
"""

import logging
import os
import sys
from pathlib import Path
from typing import Any, TextIO

import structlog

from .handlers import LogstashHandler, configure_azure_monitor
from .otel import OtlpExport, shutdown_otlp
from .redaction import censor_secrets

# Applied to every entry before the renderer. Redaction is last, so nothing
# added by an earlier processor can slip past it.
SHARED_PROCESSORS: list[Any] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    censor_secrets,
]

_configured = False


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound logger. Pass `__name__` from the calling module.

    Has no side effects. If `configure_logging` has not run, structlog's own
    defaults apply — output still appears, it is simply unformatted.
    """
    return structlog.stdlib.get_logger(name)


def configure_logging(
    level: int | str = logging.INFO,
    *,
    json_output: bool | None = None,
    stream: TextIO | None = None,
    otlp: OtlpExport | None = None,
    json_file_path: str | None = None,
    logstash_host: str | None = None,
    logstash_port: int = 5000,
    azure_connection_string: str | None = None,
) -> None:
    """Configure structlog and the stdlib root logger.

    Args:
        level: Root level, as a name or a `logging` constant.
        json_output: Force JSON on or off for the console sink. `None`
            selects JSON whenever the stream is not a terminal, which is the
            correct default for both a container and an interactive shell.
        stream: Console sink destination. Defaults to `sys.stderr`, keeping
            diagnostics off stdout so a command's actual output stays
            machine-readable.
        otlp: Also export to an OpenTelemetry collector (`otel.OtlpExport`).
        json_file_path: Also write JSON logs to this file path (parent
            directories are created if needed).
        logstash_host: Also ship JSON logs directly to a Logstash TCP input
            at this host. Use `logstash_port` to set the port (default 5000).
        logstash_port: Logstash TCP port, only used when `logstash_host` is set.
        azure_connection_string: Also send logs/traces/metrics to Azure
            Application Insights via this connection string.

    None of the console sink is ever replaced by an optional sink — if a
    collector/Logstash/Azure Monitor is unreachable, logs still exist where
    the process's own stream can be captured.

    Safe to call more than once; existing handlers are replaced rather than
    added to.
    """
    global _configured

    target = stream if stream is not None else sys.stderr
    resolved_level = getattr(logging, level.upper()) if isinstance(level, str) else level
    as_json = json_output if json_output is not None else not _is_tty(target)

    structlog.configure(
        processors=[*SHARED_PROCESSORS, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    root = logging.getLogger()
    _remove_handlers(root)
    root.setLevel(resolved_level)

    console_handler = logging.StreamHandler(target)
    console_handler.setLevel(resolved_level)
    console_handler.setFormatter(_json_formatter() if as_json else _console_formatter(target))
    root.addHandler(console_handler)

    if json_file_path is not None:
        Path(json_file_path).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(json_file_path, encoding="utf-8")
        file_handler.setLevel(resolved_level)
        file_handler.setFormatter(_json_formatter())
        root.addHandler(file_handler)

    if logstash_host is not None:
        logstash_handler = LogstashHandler(host=logstash_host, port=logstash_port)
        logstash_handler.setLevel(resolved_level)
        logstash_handler.setFormatter(_json_formatter())
        root.addHandler(logstash_handler)

    if otlp is not None:
        from .otel import attach_otlp

        root.addHandler(attach_otlp(otlp, resolved_level, _json_formatter()))

    if azure_connection_string is not None:
        configure_azure_monitor(azure_connection_string)

    _configured = True


def shutdown_logging() -> None:
    """Flush and close every handler. Call before the process exits."""
    shutdown_otlp()
    logging.shutdown()


def _is_tty(stream: TextIO) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


def _remove_handlers(logger: logging.Logger) -> None:
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()


def _console_formatter(stream: TextIO) -> "structlog.stdlib.ProcessorFormatter":
    # Respect the NO_COLOR env var (https://no-color.org) — any presence of
    # the variable, regardless of its value, disables ANSI color output.
    want_colors = _is_tty(stream) and "NO_COLOR" not in os.environ
    try:
        renderer = structlog.dev.ConsoleRenderer(colors=want_colors)
    except SystemError:
        # structlog raises SystemError on Windows when colors=True is
        # requested but the optional `colorama` package isn't installed.
        # Fall back to a plain (uncolored) renderer instead of crashing
        # every CLI invocation.
        renderer = structlog.dev.ConsoleRenderer(colors=False)
    return structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=SHARED_PROCESSORS,
    )


def _json_formatter() -> "structlog.stdlib.ProcessorFormatter":
    return structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.ExceptionRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=SHARED_PROCESSORS,
    )
