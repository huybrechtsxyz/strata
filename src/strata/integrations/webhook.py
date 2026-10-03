#!/usr/bin/env python3
"""Generic HTTP audit sink — `type: webhook` (docs/design/audit-sink-dispatch.md).

The one concrete `AuditSinkIntegration` v2 ships, deliberately ahead of any
vendor-specific (Splunk HEC / ELK / Sentinel) class: v1 shipped five SIEM
backends and real usage was one, `enabled: false`. A generic POST plus a
documented configuration example per target covers Splunk HEC, ELK/Logstash,
Loki, Datadog and any plain webhook with zero vendor code here (that design's
D1/D3). What it cannot cover is recorded there too, so the limit is decided
rather than discovered: Azure Sentinel needs a per-request HMAC signature,
and syslog/CEF is not HTTP at all — both need real code, written when a real
consumer asks.

The request body is the already-rendered CloudEvents 1.0 + ECS envelope,
JSON-encoded. This class never shapes the payload itself — see
`AuditSinkIntegration.send()`'s own contract.

Configuration (all optional, all from the bound `Integration` document):

- ``spec.endpoints.address`` — the POST target. **Required in practice**:
  the inherited `request()` has no endpoint to join onto without it.
- ``spec.configuration.headers`` — static, non-secret request headers.
- ``spec.configuration.token_env_var`` — names an environment variable
  whose value becomes the `Authorization` header. Credentials are read from
  the environment, never the document (that design's D4) — the same rule
  `infisical_resolver.py` already states and follows, so this needs no
  secret-resolution plumbing and works unchanged in CI.
- ``spec.configuration.auth_scheme`` — prefix for that header's value,
  default ``Bearer``. Splunk HEC needs ``Splunk`` instead, which is exactly
  the kind of difference this knob exists to absorb without a second class.
"""

import json
from os import environ
from typing import Any, ClassVar

from strata.integrations.capabilities import AuditSinkIntegration
from strata.integrations.errors import IntegrationError
from strata.models.integration_model import Capability

_DEFAULT_AUTH_SCHEME = "Bearer"


class WebhookIntegration(AuditSinkIntegration):
    """POST a rendered audit event as JSON to a configured endpoint."""

    TYPE: ClassVar[str] = "webhook"
    CAPABILITIES: ClassVar[frozenset[str]] = frozenset({Capability.AUDIT})
    TRANSPORTS: ClassVar[frozenset[str]] = frozenset({"http"})
    #: No CLI form at all — this integration exists only as an HTTP caller.
    COMMAND: ClassVar[str | None] = None

    def send(self, event: dict[str, Any]) -> None:
        """POST `event` as JSON to `spec.endpoints.address`.

        Raises:
            IntegrationError: No endpoint is configured, the configured
                `token_env_var` is not set in the environment, or the
                endpoint did not answer with a 2xx.
        """
        if self.config is None or self.config.spec.endpoints is None:
            raise IntegrationError(
                f"{self.name}: no 'spec.endpoints.address' configured — a webhook audit sink has nowhere to post to."
            )

        body = json.dumps(event).encode("utf-8")
        headers = {"Content-Type": "application/json", **self._headers()}

        # `http_request()` returns a 4xx/5xx as a normal result, never an
        # exception (its own docstring: the error body "usually says what was
        # wrong"), so a non-2xx has to be turned into a failure explicitly.
        result = self.request("POST", "", headers=headers, body=body)
        if not result.is_successful:
            detail = "timed out" if result.timed_out else f"HTTP {result.status}: {result.body.strip()[:200]}"
            raise IntegrationError(f"{self.name}: audit event delivery failed — {detail}")

    def _headers(self) -> dict[str, str]:
        """Static configured headers, plus the environment-sourced `Authorization` when configured."""
        configuration = (self.config.spec.configuration if self.config is not None else None) or {}
        headers = {str(key): str(value) for key, value in (configuration.get("headers") or {}).items()}

        token_env_var = configuration.get("token_env_var")
        if not token_env_var:
            return headers

        token = environ.get(str(token_env_var))
        if not token:
            raise IntegrationError(
                f"{self.name}: configuration.token_env_var names '{token_env_var}', "
                "but that environment variable is unset or empty."
            )
        scheme = str(configuration.get("auth_scheme") or _DEFAULT_AUTH_SCHEME)
        headers["Authorization"] = f"{scheme} {token}".strip()
        return headers
