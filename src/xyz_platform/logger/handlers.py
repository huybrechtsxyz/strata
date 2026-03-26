#!/usr/bin/env python3
"""
===============================================================================
Script Name   : handlers.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Custom log handlers using standard frameworks
                - LogstashHandler for ELK stack
                - Azure Application Insights via azure-monitor-opentelemetry
===============================================================================
"""

import logging
import socket


class LogstashHandler(logging.Handler):
    """
    Handler that sends logs to Logstash via TCP (for ELK stack).

    Sends JSON-formatted logs to Logstash for processing.
    Configure Logstash with a TCP input like:

    input {
      tcp {
        port => 5000
        codec => json
      }
    }
    """

    def __init__(self, host: str = "localhost", port: int = 5000, timeout: int = 5):
        """
        Initialize Logstash handler.

        Args:
            host: Logstash host.
            port: Logstash port (typically 5000 for JSON input).
            timeout: Socket timeout in seconds.
        """
        super().__init__()
        self.host = host
        self.port = port
        self.timeout = timeout
        self._socket = None

    def _connect(self):
        """Create TCP connection to Logstash."""
        if self._socket is None:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(self.timeout)
            try:
                self._socket.connect((self.host, self.port))
            except socket.error:
                self._socket = None
                raise

    def emit(self, record: logging.LogRecord):
        """Send log record to Logstash."""
        try:
            # Ensure connection
            self._connect()

            # Format the record (assumes JSON formatter)
            message = self.format(record) + "\n"

            # Send to Logstash
            if self._socket:
                self._socket.sendall(message.encode("utf-8"))

        except Exception:
            # Close socket on error
            if self._socket:
                self._socket.close()
                self._socket = None
            self.handleError(record)

    def close(self):
        """Close the socket connection."""
        if self._socket:
            self._socket.close()
            self._socket = None
        super().close()


def configure_azure_monitor(connection_string: str):
    """
    Configure Azure Application Insights using OpenTelemetry.

    This uses Microsoft's official azure-monitor-opentelemetry package,
    which is the recommended way to integrate with Azure Application Insights.

    Args:
        connection_string: Azure Application Insights connection string.
                          Format: InstrumentationKey=xxx;IngestionEndpoint=https://...

    Example:
        configure_azure_monitor(
            "InstrumentationKey=12345678-1234-1234-1234-123456789012;"
            "IngestionEndpoint=https://westeurope-1.in.applicationinsights.azure.com/"
        )

    Note:
        This function configures OpenTelemetry to send logs, traces, and metrics
        to Azure Application Insights. It integrates with Python's standard logging.
    """
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor as azure_config

        # Configure Azure Monitor with OpenTelemetry
        # This automatically sets up logging, tracing, and metrics
        azure_config(
            connection_string=connection_string,
            # You can add additional configuration here
            # enable_live_metrics=True,  # Enable live metrics stream
        )

        logging.info("Azure Application Insights configured successfully")

    except ImportError:
        raise ImportError(
            "azure-monitor-opentelemetry package is required for Azure Application Insights. "
            "Install with: pip install azure-monitor-opentelemetry"
        )
    except Exception as e:
        logging.error(f"Failed to configure Azure Application Insights: {e}")
        raise
