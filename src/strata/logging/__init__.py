"""Structured logging: diagnostic logs only.

Not an audit trail — an audit trail is a different concern with a different
lifetime and a required actor; if strata builds one, it lives elsewhere
(e.g. alongside a future deploy-log/audit model), not here.
"""

from .config import configure_logging, get_logger, shutdown_logging
from .context import LogContext, bind_run, clear_context, get_context, get_run_id
from .handlers import LogstashHandler, configure_azure_monitor
from .otel import OtlpExport
from .redaction import REDACTED, censor_secrets, censored, is_sensitive

__all__ = [
    "REDACTED",
    "LogContext",
    "LogstashHandler",
    "OtlpExport",
    "bind_run",
    "censor_secrets",
    "censored",
    "clear_context",
    "configure_azure_monitor",
    "configure_logging",
    "get_context",
    "get_logger",
    "get_run_id",
    "is_sensitive",
    "shutdown_logging",
]
