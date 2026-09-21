#!/usr/bin/env python3
"""OTLP export — diagnostic logs to an OpenTelemetry collector.

Exporting the vendor-neutral OTLP protocol rather than writing to a specific
backend directly keeps the destination a deployment concern: strata knows
one endpoint and nothing about what sits behind it (ELK, Azure Monitor,
anything else with an OTLP receiver).

Off by default. The OpenTelemetry SDK and its exporter cost real import time,
which a CLI invocation shouldn't pay when nothing is exporting — so
everything OTel-specific is imported lazily inside `attach_otlp()`, and
`OtlpExport` is a plain dataclass so describing the export costs nothing.
Requires the optional `strata-v2[otel]` extra.
"""

import logging
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from opentelemetry.sdk._logs import LoggerProvider

_provider: Any = None


@dataclass(frozen=True)
class OtlpExport:
    """Where to send logs, and how to identify strata once they arrive.

    Attributes:
        endpoint: Full OTLP HTTP logs URL, e.g. `http://collector:4318/v1/logs`.
        service_name: Becomes `service.name`. Without it every record in the
            collector is attributed to `unknown_service`.
        service_version: Becomes `service.version`.
        environment: Becomes `deployment.environment`.
        timeout: Export timeout in seconds.
    """

    endpoint: str
    service_name: str = "strata"
    service_version: str | None = None
    environment: str | None = None
    timeout: int = 10


def attach_otlp(
    export: OtlpExport,
    level: int,
    formatter: logging.Formatter,
) -> logging.Handler:
    """Build a handler that batches log records to an OTLP collector.

    The formatter is applied, so the exported body is the same redacted JSON
    line the stream sink receives. Without it, records bypass the redaction
    that runs in the formatter's chain for non-structlog loggers.

    Raises:
        ImportError: if the optional `strata-v2[otel]` extra isn't installed.
    """
    try:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource
    except ImportError as exc:
        raise ImportError(
            "OTLP export requires the optional 'otel' extra. Install with: pip install 'strata-v2[otel]'"
        ) from exc

    global _provider

    shutdown_otlp()

    attributes: dict[str, str] = {"service.name": export.service_name}
    if export.service_version:
        attributes["service.version"] = export.service_version
    if export.environment:
        attributes["deployment.environment"] = export.environment

    provider = LoggerProvider(resource=Resource.create(attributes))
    provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=export.endpoint, timeout=export.timeout))
    )
    _provider = provider

    # The SDK's LoggingHandler is marked deprecated in favour of a handler
    # that opentelemetry-instrumentation-logging does not yet provide — that
    # package only injects trace identifiers. Until it exists this is the
    # supported path, and the warning is not actionable.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        handler = LoggingHandler(level=level, logger_provider=provider)

    handler.setFormatter(formatter)
    return handler  # type: ignore[no-any-return]


def shutdown_otlp() -> None:
    """Flush and stop the exporter. Safe when nothing was attached."""
    global _provider
    if _provider is not None:
        provider: "LoggerProvider" = _provider
        _provider = None
        provider.shutdown()
