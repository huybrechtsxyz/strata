#!/usr/bin/env python3
"""Infisical secret/variable resolver — REST only, no CLI dependency.

Configured entirely from environment variables (see module docstring in
`strata.integrations`) — matches the real production auth mode (universal
auth / machine identity) confirmed in `/memories/repo/v1-consumer-usage.md`,
plus a service-token fallback since it costs nothing extra to support:

- ``INFISICAL_TOKEN`` — a service token, used directly as the bearer token.
- ``INFISICAL_CLIENT_ID`` + ``INFISICAL_CLIENT_SECRET`` — universal auth
  (machine identity); exchanged for an access token via one login call.
- ``INFISICAL_PROJECT_ID`` — required either way.
- ``INFISICAL_ENVIRONMENT`` — defaults to ``prod``.
- ``INFISICAL_ADDR`` — defaults to ``https://app.infisical.com``.

Resolves every requested key in one bulk call (``GET /api/v3/secrets/raw``),
mirroring v1's `_fetch_all_secret_values` — cheaper than one request per key,
and the same endpoint already returns every value in one response.

ADR-0021 D7 retrofit: a `StoreIntegration`, transport moved from hand-rolled
`urllib` to `http_request` (D5). Still calls the free function directly
rather than `self.request()` — that method joins onto `config.spec.
endpoints.address`, and this resolver has no config (env-var-driven,
`config=None` always: nothing looks up a named `Integration` document for a
store yet, see `strata.integrations` module docstring).
"""

import json
from os import environ
from urllib.parse import urlencode

from strata.integrations.capabilities import StoreIntegration
from strata.integrations.errors import ValueResolutionError
from strata.models.integration_model import IntegrationModel
from strata.utils.transport import NO_RESPONSE, http_request

_DEFAULT_ADDR = "https://app.infisical.com"
_DEFAULT_ENVIRONMENT = "prod"
_TIMEOUT_SECONDS = 10


class InfisicalResolver(StoreIntegration):
    """Resolves Infisical-backed variable/secret values, by key."""

    TYPE = "infisical"
    CAPABILITIES = frozenset({"variables", "secrets"})
    TRANSPORTS = frozenset({"http"})

    def __init__(self, config: IntegrationModel | None = None) -> None:
        super().__init__(config)
        self._addr = environ.get("INFISICAL_ADDR", _DEFAULT_ADDR).rstrip("/")
        self._project_id = environ.get("INFISICAL_PROJECT_ID")
        self._environment = environ.get("INFISICAL_ENVIRONMENT", _DEFAULT_ENVIRONMENT)
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
                "Infisical: not authenticated. Set INFISICAL_TOKEN, or "
                "INFISICAL_CLIENT_ID + INFISICAL_CLIENT_SECRET."
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
