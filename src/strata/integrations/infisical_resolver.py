#!/usr/bin/env python3
"""Infisical secret/variable resolver — REST only, no CLI dependency.

Non-secret connection settings (address/project/environment) may come from
a bound `kind: integration` document's `spec.endpoints`/`.configuration`
(docs/design/store-integration-configuration.md's Phase 2) — falls back to
environment variables, unchanged, when no document is bound or a specific
field is left unset. Real credentials are **always** environment variables
regardless — matches the real production auth mode (universal auth /
machine identity) confirmed in `/memories/repo/v1-consumer-usage.md`, plus
a service-token fallback since it costs nothing extra to support (see that
design's own "Deliberately out of scope" section for why `spec.
authentication` is never used here, even once bound):

- ``INFISICAL_TOKEN`` — a service token, used directly as the bearer token.
- ``INFISICAL_CLIENT_ID`` + ``INFISICAL_CLIENT_SECRET`` — universal auth
  (machine identity); exchanged for an access token via one login call.
- ``INFISICAL_PROJECT_ID`` — required either way, unless `configuration.
  project_id` is set on a bound `Integration` document instead.
- ``INFISICAL_ENVIRONMENT`` — defaults to ``prod``, unless `configuration.
  environment` is set on a bound `Integration` document instead.
- ``INFISICAL_ADDR`` — defaults to ``https://app.infisical.com``, unless
  `endpoints.address` is set on a bound `Integration` document instead.

Resolves every requested key in one bulk call (``GET /api/v3/secrets/raw``),
mirroring v1's `_fetch_all_secret_values` — cheaper than one request per key,
and the same endpoint already returns every value in one response.

ADR-0021 D7 retrofit: a `StoreIntegration`, transport moved from hand-rolled
`urllib` to `http_request` (D5). Still calls the free function directly
rather than `self.request()` — that method joins `path` onto `config.spec.
endpoints.address` with no default and no query-string support, neither of
which fits `_fetch_all()`'s own URL shape (a default host, a querystring)
or `_access_token()`'s separate login endpoint.

docs/design/store-integration-configuration.md's Phase 2: `config.spec.
endpoints.address`/`configuration["project_id"]`/`configuration["environment"]`
are now preferred over the matching env var above when a real `Integration`
document is bound (docs/_gap_v1.md gap-adjacent — closes the "config
document exists but nothing reads it" finding that design confirmed
directly). Every env var above still works completely unchanged when no
document is bound (`config is None`) or a specific field is left unset on
one that is — this is purely additive, never a breaking change.
"""

import json
from datetime import datetime
from os import environ
from typing import Any
from urllib.parse import urlencode

from strata.integrations.capabilities import StoreIntegration
from strata.integrations.errors import IntegrationError, ValueResolutionError
from strata.integrations.resolved_context import SecretMetadata
from strata.models.integration_model import Capability, IntegrationModel
from strata.utils.transport import NO_RESPONSE, http_request

_DEFAULT_ADDR = "https://app.infisical.com"
_DEFAULT_ENVIRONMENT = "prod"
_TIMEOUT_SECONDS = 10


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse an Infisical `createdAt`/`updatedAt` ISO-8601 string, or `None`
    on anything else (absent, malformed) — `metadata()` never raises for a
    parse failure, matching its own "best-effort" contract."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class InfisicalResolver(StoreIntegration):
    """Resolves Infisical-backed variable/secret values, by key."""

    TYPE = "infisical"
    CAPABILITIES = frozenset({Capability.VARIABLES, Capability.SECRETS})
    TRANSPORTS = frozenset({"http"})

    def __init__(self, config: IntegrationModel | None = None) -> None:
        super().__init__(config)
        configuration = (config.spec.configuration or {}) if config is not None else {}
        endpoint_address = config.spec.endpoints.address if config is not None and config.spec.endpoints else None
        if endpoint_address is not None:
            self._addr = endpoint_address.rstrip("/")
        else:
            self._addr = environ.get("INFISICAL_ADDR", _DEFAULT_ADDR).rstrip("/")
        self._project_id = configuration.get("project_id") or environ.get("INFISICAL_PROJECT_ID")
        self._environment = configuration.get("environment") or environ.get(
            "INFISICAL_ENVIRONMENT", _DEFAULT_ENVIRONMENT
        )
        self._token = environ.get("INFISICAL_TOKEN")
        self._client_id = environ.get("INFISICAL_CLIENT_ID")
        self._client_secret = environ.get("INFISICAL_CLIENT_SECRET")
        self._cache: dict[str, str] | None = None

    def resolve(self, key: str) -> str:
        """Return the value for `key`.

        Raises:
            ValueResolutionError: Not configured, unreachable/unauthenticated,
                or `key` does not exist in the scope.
        """
        if self._cache is None:
            self._cache = self._fetch_all()
        if key not in self._cache:
            raise ValueResolutionError(f"Infisical: no secret named '{key}' in this project/environment.")
        return self._cache[key]

    def _access_token(self) -> str:
        if self._token:
            return self._token
        if not (self._client_id and self._client_secret):
            raise ValueResolutionError(
                "Infisical: not authenticated. Set INFISICAL_TOKEN, or INFISICAL_CLIENT_ID + INFISICAL_CLIENT_SECRET."
            )
        payload = json.dumps({"clientId": self._client_id, "clientSecret": self._client_secret}).encode("utf-8")
        result = http_request(
            "POST",
            f"{self._addr}/api/v1/auth/universal-auth/login",
            headers={"Content-Type": "application/json"},
            body=payload,
            timeout=_TIMEOUT_SECONDS,
        )
        if not result.is_successful:
            raise ValueResolutionError(f"Infisical: universal-auth login failed: {result.payload}")
        token = json.loads(result.payload).get("accessToken")
        if not token:
            raise ValueResolutionError("Infisical: universal-auth login did not return an access token.")
        return str(token)

    def _fetch_all(self) -> dict[str, str]:
        if not self._project_id:
            raise ValueResolutionError("Infisical: INFISICAL_PROJECT_ID is not set.")
        token = self._access_token()
        params = {"environment": self._environment, "secretPath": "/", "workspaceId": self._project_id}
        url = f"{self._addr}/api/v3/secrets/raw?{urlencode(params)}"
        result = http_request("GET", url, headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT_SECONDS)
        if not result.is_successful:
            if result.status == NO_RESPONSE:
                raise ValueResolutionError(f"Infisical: could not reach '{self._addr}': {result.payload}")
            raise ValueResolutionError(f"Infisical: request failed ({result.status}): {result.payload}")
        data = json.loads(result.payload)
        return {
            secret["secretKey"]: secret.get("secretValue", "")
            for secret in data.get("secrets", [])
            if secret.get("secretKey")
        }

    def set(self, key: str, value: str) -> None:
        """Create or overwrite Infisical secret `key`.

        Infisical's real API has distinct create (`POST`) and update
        (`PATCH`) endpoints — presented here as one upsert (D3): tries
        `PATCH` first (the common case, an existing secret being rotated
        or edited), falling back to `POST` only on a 404 (the secret does
        not exist yet).

        Raises:
            strata.integrations.errors.IntegrationError: Not configured,
                unauthenticated, or the write itself failed.
        """
        if not self._project_id:
            raise IntegrationError("Infisical: INFISICAL_PROJECT_ID is not set.")
        try:
            token = self._access_token()
        except ValueResolutionError as exc:
            raise IntegrationError(str(exc)) from exc

        body = json.dumps(
            {
                "workspaceId": self._project_id,
                "environment": self._environment,
                "secretPath": "/",
                "secretValue": value,
            }
        ).encode("utf-8")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        url = f"{self._addr}/api/v3/secrets/raw/{key}"

        result = http_request("PATCH", url, headers=headers, body=body, timeout=_TIMEOUT_SECONDS)
        if result.status == 404:
            result = http_request("POST", url, headers=headers, body=body, timeout=_TIMEOUT_SECONDS)
        if not result.is_successful:
            raise IntegrationError(f"Infisical: write for '{key}' failed ({result.status}): {result.payload}")

        # Invalidate the bulk-fetch cache so a subsequent resolve() in this
        # same instance sees the value just written, not a stale one.
        self._cache = None

    def metadata(self, key: str) -> SecretMetadata | None:
        """Best-effort `createdAt`/`updatedAt` for `key`, via a dedicated
        single-secret `GET` (the bulk list `_fetch_all()` uses for `resolve()`
        never requests timestamp fields).

        Returns `None` — never raises — when `key` isn't found, the request
        fails, or the response doesn't parse as expected: `values status`
        treats a missing timestamp as its own `no_timestamp`/`no_metadata`
        classification, not a resolution failure.
        """
        if not self._project_id:
            return None
        try:
            token = self._access_token()
        except ValueResolutionError:
            return None

        params = {"workspaceId": self._project_id, "environment": self._environment, "secretPath": "/"}
        url = f"{self._addr}/api/v3/secrets/raw/{key}?{urlencode(params)}"
        result = http_request("GET", url, headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT_SECONDS)
        if not result.is_successful:
            return None
        try:
            secret = result.json().get("secret", {})
        except (json.JSONDecodeError, AttributeError):
            return None
        return SecretMetadata(
            created_at=_parse_timestamp(secret.get("createdAt")),
            updated_at=_parse_timestamp(secret.get("updatedAt")),
        )
