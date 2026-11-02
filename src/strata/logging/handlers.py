#!/usr/bin/env python3
"""Additional optional stdlib logging handlers, ported from strata v1.

`LogstashHandler` ships JSON logs over TCP directly to a Logstash input, and
`configure_azure_monitor` wires Azure Application Insights straight into the
stdlib root logger — both bypass the OTLP-collector indirection in `otel.py`
for deployments that talk to one of these backends directly rather than
through a collector.
"""

import logging
import socket


class LogstashHandler(logging.Handler):
    """Handler that sends logs to Logstash via TCP (for an ELK stack).

    Sends JSON-formatted logs (pair with a JSON formatter) to a Logstash TCP
    input, e.g.::

        input {
          tcp {
            port => 5000
            codec => json
          }
        }
    """

    def __init__(self, host: str = "localhost", port: int = 5000, timeout: int = 5) -> None:
        """Initialize the Logstash handler.

        Args:
            host: Logstash host.
            port: Logstash port (typically 5000 for a JSON TCP input).
            timeout: Socket timeout in seconds.
        """
        super().__init__()
        self.host = host
        self.port = port
        self.timeout = timeout
        self._socket: socket.socket | None = None

    def _connect(self) -> None:
        """Create a TCP connection to Logstash."""
        if self._socket is None:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(self.timeout)
            try:
                self._socket.connect((self.host, self.port))
            except OSError:
                self._socket = None
                raise

    def emit(self, record: logging.LogRecord) -> None:
        """Send a log record to Logstash."""
        try:
            self._connect()
            message = self.format(record) + "\n"
            if self._socket:
                self._socket.sendall(message.encode("utf-8"))
        except Exception:
            if self._socket:
                self._socket.close()
                self._socket = None
            self.handleError(record)

    def close(self) -> None:
        """Close the socket connection."""
        if self._socket:
            self._socket.close()
            self._socket = None
        super().close()


def configure_azure_monitor(connection_string: str) -> None:
    """Configure Azure Application Insights using OpenTelemetry.

    Uses Microsoft's official `azure-monitor-opentelemetry` package, which
    hooks logging/tracing/metrics into Python's standard logging directly —
    unlike `otel.attach_otlp()`, this does not return a handler to attach;
    it configures itself against the root logger internally.

    Args:
        connection_string: Azure Application Insights connection string,
            e.g. ``"InstrumentationKey=...;IngestionEndpoint=https://..."``.

    Raises:
        ImportError: if the optional `strata-v2[azure-monitor]` extra isn't installed.
    """
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor as _configure
    except ImportError as exc:
        raise ImportError(
            "Azure Application Insights requires the optional 'azure-monitor' extra. "
            "Install with: pip install 'strata-v2[azure-monitor]'"
        ) from exc

    try:
        _configure(connection_string=connection_string)
    except Exception as exc:
        logging.error(f"Failed to configure Azure Application Insights: {exc}")
        raise
