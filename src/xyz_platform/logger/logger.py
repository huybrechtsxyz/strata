#!/usr/bin/env python3
"""
===============================================================================
Script Name   : logger.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Core logging configuration using standard frameworks
                - python-json-logger for ELK
                - azure-monitor-opentelemetry for Azure
                - YAML-based configuration (Serilog-style)
===============================================================================
"""

import logging
import logging.config
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Optional, List
import yaml

# Global flag to track if logging has been configured
_configured = False


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance.

    Args:
        name: Logger name (typically __name__ from calling module).

    Returns:
        Logger instance.

    Example:
        logger = get_logger(__name__)
        logger.info("Operation completed")

    Note:
        Logging must be explicitly configured via configure_logging() before use.
        If not configured, basic logging will be used.
    """
    global _configured

    # If not configured, use basic console logging as fallback
    if not _configured:
        configure_logging(level="INFO", enable_console=True)

    return logging.getLogger(name)


def configure_logging(
    config_path: Optional[str] = None,
    level: Optional[str] = None,
    enable_console: bool = True,
    enable_json_file: bool = False,
    enable_logstash: bool = False,
    enable_azure: bool = False,
    log_file_path: Optional[str] = None,
    logstash_host: Optional[str] = None,
    logstash_port: int = 5000,
    azure_connection_string: Optional[str] = None,
):
    """
    Configure logging for the platform using standard libraries.

    This function provides Serilog-style configuration - use YAML config file
    for production, or programmatic config for development.

    Args:
        config_path: Path to YAML config file (recommended). If provided, other args ignored.
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        enable_console: Enable console output with colors.
        enable_json_file: Enable JSON file output for local ELK testing.
        enable_logstash: Enable Logstash output for ELK stack.
        enable_azure: Enable Azure Application Insights via OpenTelemetry.
        log_file_path: Path for JSON log file (if enable_json_file=True).
        logstash_host: Logstash host (if enable_logstash=True).
        logstash_port: Logstash port (default: 5000).
        azure_connection_string: Azure connection string (or set APPLICATIONINSIGHTS_CONNECTION_STRING env var).

    Example:
        # Simple console logging (development)
        configure_logging(level="DEBUG", enable_console=True)

        # Console + Logstash (ELK)
        configure_logging(
            level="INFO",
            enable_console=True,
            enable_logstash=True,
            logstash_host="localhost",
            logstash_port=5000
        )

        # From YAML config (recommended for production)
        configure_logging(config_path="config/logging.yaml")
    """
    global _configured

    # If config file provided, use it (Serilog-style)
    if config_path and Path(config_path).exists():
        _configure_from_yaml(config_path)
        _configured = True
        return

    # Otherwise, configure programmatically
    root_logger = logging.getLogger()
    root_logger.handlers.clear()

    log_level = getattr(logging, level.upper() if level else "INFO")
    root_logger.setLevel(log_level)

    # Import formatters locally to avoid circular imports
    from .formatters import ConsoleFormatter, JsonFormatter

    # Console handler (human-readable, colored)
    if enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(ConsoleFormatter(use_colors=sys.stdout.isatty()))
        root_logger.addHandler(console_handler)

    # JSON file handler (for local ELK testing)
    if enable_json_file:
        file_path = log_file_path or "logs/application.json"
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(file_path)
        file_handler.setLevel(log_level)
        file_handler.setFormatter(JsonFormatter())
        root_logger.addHandler(file_handler)

    # Logstash handler (for ELK stack)
    if enable_logstash and logstash_host:
        try:
            from .handlers import LogstashHandler

            logstash_handler = LogstashHandler(host=logstash_host, port=logstash_port)
            logstash_handler.setLevel(log_level)
            logstash_handler.setFormatter(JsonFormatter())
            root_logger.addHandler(logstash_handler)
        except Exception as e:
            root_logger.warning(f"Failed to configure Logstash handler: {e}")

    # Azure Application Insights (via OpenTelemetry)
    if enable_azure:
        try:
            from .handlers import configure_azure_monitor

            connection_string = azure_connection_string or os.getenv(
                "APPLICATIONINSIGHTS_CONNECTION_STRING"
            )
            if connection_string:
                configure_azure_monitor(connection_string)
            else:
                root_logger.warning("Azure enabled but no connection string provided")
        except Exception as e:
            root_logger.warning(f"Failed to configure Azure Application Insights: {e}")

    _configured = True


def _configure_from_yaml(config_path: str):
    """
    Configure logging from YAML file (Serilog-style).

    Supports standard Python logging.config.dictConfig format.
    """
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)

        # Import custom classes for registration
        from .formatters import ConsoleFormatter, JsonFormatter
        from .handlers import LogstashHandler

        # Register custom formatters in the config
        if "formatters" in config:
            for name, formatter_config in config["formatters"].items():
                if "()" in formatter_config:
                    class_path = formatter_config["()"]
                    if "JsonFormatter" in class_path:
                        formatter_config["()"] = JsonFormatter
                    elif "ConsoleFormatter" in class_path:
                        formatter_config["()"] = ConsoleFormatter

        # Register custom handlers in the config
        if "handlers" in config:
            for name, handler_config in config["handlers"].items():
                if "()" in handler_config:
                    class_path = handler_config["()"]
                    if "LogstashHandler" in class_path:
                        handler_config["()"] = LogstashHandler

        # Apply the configuration
        logging.config.dictConfig(config)

        # Handle Azure separately (OpenTelemetry integration)
        if "azure" in config and config["azure"].get("enabled"):
            from .handlers import configure_azure_monitor

            connection_string = config["azure"].get("connection_string") or os.getenv(
                "APPLICATIONINSIGHTS_CONNECTION_STRING"
            )
            if connection_string:
                configure_azure_monitor(connection_string)

    except Exception as e:
        # Fallback to basic config
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
        logging.error(f"Failed to configure logging from {config_path}: {e}")


def reconfigure_logging(
    config_path: Optional[str] = None,
    level: Optional[str] = None,
    enable_console: bool = False,
):
    """
    Reconfigure logging after initial setup.
    Clears existing handlers and applies new configuration.

    Args:
        config_path: Path to YAML config file
        level: Log level override
        enable_console: Enable console output override

    Example:
        # Reconfigure from platform config
        reconfigure_logging(config_path="examples/logging.production.yaml")
    """
    global _configured

    # Clear existing handlers
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        handler.close()
        root_logger.removeHandler(handler)

    # Reset configured flag
    _configured = False

    # Apply new configuration
    configure_logging(
        config_path=config_path, level=level, enable_console=enable_console
    )


def shutdown_logging():
    """
    Shutdown logging and flush all handlers.

    Call this before application exit to ensure all logs are written.
    """
    logging.shutdown()


def get_active_log_file() -> Optional[Path]:
    """
    Get the path to the first active file handler's log file.

    This function introspects the current logging configuration to find
    the actual log file being written to. Useful for commands that need
    to read back logs (e.g., session logs command).

    Returns:
        Path to the active log file, or None if no file handler is configured.

    Example:
        log_file = get_active_log_file()
        if log_file and log_file.exists():
            with open(log_file, 'r') as f:
                logs = f.read()
    """
    # Check root logger and all child loggers
    loggers_to_check = [logging.getLogger()]  # Start with root
    loggers_to_check.extend(
        [logging.getLogger(name) for name in logging.Logger.manager.loggerDict]
    )

    for logger in loggers_to_check:
        for handler in logger.handlers:
            # Check for FileHandler or RotatingFileHandler
            if isinstance(
                handler, (logging.FileHandler, logging.handlers.RotatingFileHandler)
            ):
                # Get the absolute path from the handler
                return Path(handler.baseFilename).resolve()

    return None


def get_active_log_files() -> List[Path]:
    """
    Get paths to all active file handlers' log files.

    This function introspects the current logging configuration to find
    all log files being written to. Useful for operations that need to
    manage all log files (e.g., clearing logs).

    Returns:
        List of Paths to active log files. Empty list if no file handlers configured.

    Example:
        log_files = get_active_log_files()
        for log_file in log_files:
            if log_file.exists():
                log_file.unlink()
    """
    log_files = []
    seen_paths = set()  # Track unique paths

    # Check root logger and all child loggers
    loggers_to_check = [logging.getLogger()]  # Start with root
    loggers_to_check.extend(
        [logging.getLogger(name) for name in logging.Logger.manager.loggerDict]
    )

    for logger in loggers_to_check:
        for handler in logger.handlers:
            # Check for FileHandler or RotatingFileHandler
            if isinstance(
                handler, (logging.FileHandler, logging.handlers.RotatingFileHandler)
            ):
                # Get the absolute path from the handler
                log_path = Path(handler.baseFilename).resolve()
                # Avoid duplicates
                if log_path not in seen_paths:
                    seen_paths.add(log_path)
                    log_files.append(log_path)

    return log_files
