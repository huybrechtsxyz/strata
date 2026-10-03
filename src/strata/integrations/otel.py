#!/usr/bin/env python3
"""OTLP/HTTP JSON audit sink — `type: otel` (docs/design/audit-sink-dispatch.md).

Forwards a rendered audit event to any OTLP-compatible backend — the named
real consumer is Elastic ("our ELK stack can handle otel": modern
Elastic/APM Server accepts OTLP natively), but the same class works
unchanged for Grafana Loki, Datadog, or an OTel Collector that itself fans
out further. Supporting one protocol well here is closer to "stop writing
SIEM classes" than to "write a fourth one" (that design's D3 amendment).

Checked directly against v1's real `siem/otel_siem_integration.py`: it is a
plain OTLP/HTTP **JSON** exporter built on `requests` — no
`opentelemetry-*`/protobuf package involved — so this class follows the
exact same shape as `WebhookIntegration`, using the inherited
`self.request()` rather than adding a new dependency.

One real fix over v1's own implementation, not just a port: v1 stamps every
record with `time.time()` — the *send* time, not the event's own occurrence
time, silently wrong on a retried/delayed send. The rendered event already
carries a real timestamp (`controllers/audit_event_rendering.py`'s own
`time`, set from `completed_at`/`timestamp`) — this class parses and
converts that instead of calling the clock again.

`severityNumber`/`severityText` stay hardcoded (OTel's own "INFO" tier) —
v2 has no severity concept of its own to map from, the same honest-adaptation
reasoning `audit_run.py`'s `_platform_reference()` already uses elsewhere.

Configuration (all from the bound `Integration` document):

- ``spec.endpoints.address`` — the OTLP/HTTP base URL (e.g. an OTel
  Collector's ``:4318``). **Required**: `/v1/logs` is joined onto it, the
  protocol's own fixed path, not a deployment choice.
- ``spec.configuration.resource_attributes`` — optional extra OTLP resource
  attributes (e.g. ``{"deployment.environment": "prod"}``), merged over the
  `service.name: strata-audit` default.
"""

import json
from datetime import datetime
from typing import Any, ClassVar

from strata.integrations.capabilities import AuditSinkIntegration
from strata.integrations.errors import IntegrationError
from strata.models.integration_model import Capability

_DEFAULT_RESOURCE_ATTRIBUTES = {"service.name": "strata-audit"}
#: OTel's "INFO" severity tier — v2 has no severity concept of its own to map
#: from (matched exactly to v1's own hardcoded values).
_SEVERITY_NUMBER = 9
_SEVERITY_TEXT = "INFO"


class OtelIntegration(AuditSinkIntegration):
    """POST a rendered audit event as an OTLP/HTTP JSON `LogsServiceRequest`."""

    TYPE: ClassVar[str] = "otel"
    CAPABILITIES: ClassVar[frozenset[str]] = frozenset({Capability.AUDIT})
    TRANSPORTS: ClassVar[frozenset[str]] = frozenset({"http"})
    #: No CLI form at all — this integration exists only as an HTTP caller.
    COMMAND: ClassVar[str | None] = None

    def send(self, event: dict[str, Any]) -> None:
        """POST `event` to `spec.endpoints.address`/v1/logs as an OTLP log record.

        Raises:
            IntegrationError: No endpoint is configured, or the endpoint did
                not answer with a 2xx.
        """
        if self.config is None or self.config.spec.endpoints is None:
            raise IntegrationError(
                f"{self.name}: no 'spec.endpoints.address' configured — an OTel audit sink has nowhere to post to."
            )

        body = json.dumps(self._build_request(event)).encode("utf-8")
        headers = {"Content-Type": "application/json"}

        # `http_request()` returns a 4xx/5xx as a normal result, never an
        # exception, so a non-2xx has to be turned into a failure explicitly.
        result = self.request("POST", "v1/logs", headers=headers, body=body)
        if not result.is_successful:
            detail = "timed out" if result.timed_out else f"HTTP {result.status}: {result.body.strip()[:200]}"
            raise IntegrationError(f"{self.name}: audit event delivery failed — {detail}")

    def _build_request(self, event: dict[str, Any]) -> dict[str, Any]:
        """Build one OTLP `LogsServiceRequest` carrying `event` as its single log record."""
        configuration = (self.config.spec.configuration if self.config is not None else None) or {}
        resource_attributes = {
            **_DEFAULT_RESOURCE_ATTRIBUTES,
            **(configuration.get("resource_attributes") or {}),
        }
        return {
            "resourceLogs": [
                {
                    "resource": {
                        "attributes": [
                            {"key": key, "value": {"stringValue": str(value)}}
                            for key, value in resource_attributes.items()
                        ]
                    },
                    "scopeLogs": [
                        {
                            "scope": {"name": "strata.audit"},
                            "logRecords": [
                                {
                                    "timeUnixNano": str(self._time_unix_nanos(event.get("time"))),
                                    "severityNumber": _SEVERITY_NUMBER,
                                    "severityText": _SEVERITY_TEXT,
                                    "body": {"stringValue": json.dumps(event)},
                                    "attributes": [
                                        {"key": "event.type", "value": {"stringValue": str(event.get("type", ""))}}
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }

    @staticmethod
    def _time_unix_nanos(time_value: Any) -> int:
        """The event's own occurrence time, in UNIX nanoseconds.

        Deliberately *not* `time.time()` (v1's own defect, see module
        docstring) — parses the rendered event's own ISO-8601 `time`.
        Falls back to the current time only when `time_value` is missing or
        unparseable, which should not happen with the real renderer; this
        exists so a malformed/hand-built event still produces a valid OTLP
        record rather than raising.
        """
        if time_value:
            try:
                return int(datetime.fromisoformat(str(time_value)).timestamp() * 1_000_000_000)
            except ValueError:
                pass
        return int(datetime.now().timestamp() * 1_000_000_000)
