#!/usr/bin/env python3
"""
Core logging configuration using structlog.

Architecture
------------
structlog is configured to route through Python's stdlib logging so that
Azure Monitor's OpenTelemetry hook (which attaches to stdlib) captures
every log entry automatically.

Sink matrix:
  enable_console   → StreamHandler  + ConsoleRenderer (color on TTY, JSON otherwise)
  enable_json_file → FileHandler    + JSONRenderer
  enable_logstash  → LogstashHandler + JSONRenderer (TCP to ELK)
  enable_azure     → configure_azure_monitor() hooks into stdlib root logger
"""

import logging
import os
import sys
from pathlib import Path
from typing import Optional

import structlog

from .formatters import SHARED_PROCESSORS, make_console_formatter, make_json_formatter

_configured = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Return a structlog bound logger.

    Auto-configures with console output at INFO level if configure_logging()
    has not been called yet.

    Args:
        name: Logger name — pass ``__name__`` from the calling module.

    Example::

        log = get_logger(__name__)
        log.info("started", component="worker", workers=4)
    """
    if not _configured:
        configure_logging()
    return structlog.get_logger(name)


def configure_logging(
    level: str = "INFO",
    enable_console: bool = True,
    enable_json_file: bool = False,
    enable_logstash: bool = False,
    enable_azure: bool = False,
    log_file_path: Optional[str] = None,
    logstash_host: Optional[str] = None,
    logstash_port: int = 5000,
    azure_connection_string: Optional[str] = None,
) -> None:
    """
    Configure logging for the platform.

    structlog is wired through stdlib's root logger so a single handler
    setup controls all output — including third-party libraries and Azure
    Monitor's OpenTelemetry bridge.

    Args:
        level: Root log level (DEBUG / INFO / WARNING / ERROR / CRITICAL).
        enable_console: Write to stdout (colored on TTY, JSON otherwise).
        enable_json_file: Write JSON logs to *log_file_path*.
        enable_logstash: Ship JSON logs to a Logstash TCP input.
        enable_azure: Send logs/traces/metrics to Azure Application Insights.
        log_file_path: Destination for JSON file logs (default ``logs/application.json``).
        logstash_host: Logstash hostname or IP.
        logstash_port: Logstash TCP port (default 5000).
        azure_connection_string: App Insights connection string.
            Falls back to the ``APPLICATIONINSIGHTS_CONNECTION_STRING`` env var.

    Example::

        # Development
        configure_logging(level="DEBUG", enable_console=True)

        # Production (ELK + Azure)
        configure_logging(
            level="INFO",
            enable_console=False,
            enable_logstash=True,
            logstash_host="logstash.internal",
            enable_azure=True,
        )
    """
    global _configured

    log_level = getattr(logging, level.upper(), logging.INFO)

    # ------------------------------------------------------------------
    # Configure structlog to use stdlib as its final sink so that Azure
    # Monitor's root-logger hook captures every structlog entry.
    # ------------------------------------------------------------------
    structlog.configure(
        processors=SHARED_PROCESSORS
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        handler.close()
        root_logger.removeHandler(handler)
    root_logger.setLevel(log_level)

    # ------------------------------------------------------------------
    # Sinks
    # ------------------------------------------------------------------
    if enable_console:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(log_level)
        handler.setFormatter(make_console_formatter())
        root_logger.addHandler(handler)

    if enable_json_file:
        file_path = log_file_path or "logs/application.json"
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(file_path, encoding="utf-8")
        handler.setLevel(log_level)
        handler.setFormatter(make_json_formatter())
        root_logger.addHandler(handler)

    if enable_logstash and logstash_host:
        try:
            from .handlers import LogstashHandler

            handler = LogstashHandler(host=logstash_host, port=logstash_port)
            handler.setLevel(log_level)
            handler.setFormatter(make_json_formatter())
            root_logger.addHandler(handler)
        except Exception as exc:
            logging.warning("Failed to configure Logstash handler: %s", exc)

    if enable_azure:
        try:
            from .handlers import configure_azure_monitor

            conn_str = azure_connection_string or os.getenv(
                "APPLICATIONINSIGHTS_CONNECTION_STRING"
            )
            if conn_str:
                configure_azure_monitor(conn_str)
            else:
                logging.warning("Azure enabled but no connection string provided")
        except Exception as exc:
            logging.warning("Failed to configure Azure Application Insights: %s", exc)

    _configured = True


def reconfigure_logging(**kwargs) -> None:
    """
    Tear down current logging configuration and apply a new one.

    Accepts the same arguments as ``configure_logging()``.
    """
    global _configured
    _configured = False
    configure_logging(**kwargs)


def shutdown_logging() -> None:
    """Flush and close all handlers. Call before process exit."""
    logging.shutdown()


def get_active_log_files() -> list[str]:
    """Return absolute paths of all active file-based log sinks."""
    return [
        handler.baseFilename
        for handler in logging.getLogger().handlers
        if isinstance(handler, logging.FileHandler)
    ]


def get_active_log_file() -> Optional[str]:
    """Return the first active file sink path, or ``None``."""
    files = get_active_log_files()
    return files[0] if files else None
