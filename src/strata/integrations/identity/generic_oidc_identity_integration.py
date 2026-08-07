"""Generic OIDC identity-provider integration — device-code login for the CLI itself (ADR-0067).

Implements the OAuth 2.0 Device Authorization Grant (RFC 8628) against any identity
provider that exposes standard OIDC discovery (``/.well-known/openid-configuration``):
Okta, Keycloak, PingFederate, or any other compliant issuer. Auth0's own OIDC endpoint
and Azure AD/Entra ID also work here directly if no cloud-CLI reuse shortcut applies.

Configuration YAML::

    integrations:
      - name: strata-control-plane
        type: generic_oidc
        capabilities: [identity]
        endpoints:
          address: https://your-issuer.example.com          # OIDC issuer base URL
        authentication:
          method: oauth2
          oauth2:
            client_id: STRATA_OIDC_CLIENT_ID                # env var name holding the client id
            scope: OIDC_SCOPES                               # optional; env var, default below

This integration never stores a client secret — device-code is a public-client flow.
Session state (access/refresh token, resolved identity claims) is persisted via
``strata.utils.identity_token_cache`` so a login survives across CLI invocations.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

from strata.integrations.base_integration import BaseIntegration
from strata.logger import get_logger
from strata.models.capabilities import IIdentityProvider
from strata.utils.identity_token_cache import clear_token, load_token, save_token

logger = get_logger(__name__)

_DEFAULT_SCOPE = "openid profile email offline_access"
_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
_HTTP_TIMEOUT = 15


class GenericOidcIdentityIntegration(BaseIntegration):
    """OIDC device-code login for the strata CLI — no external tool to delegate to."""

    COMMAND = "generic_oidc"  # API-only; no CLI binary required
    CAPABILITIES = [IIdentityProvider]

    # Discovery document cache: process-scoped, avoids repeated well-known lookups
    _discovery_cache: Optional[Dict[str, Any]] = None

    @classmethod
    def _get_instance_key_static(cls, class_ref, *args, **kwargs) -> str:
        """One instance per declared integration name — a workspace may declare several."""
        config = kwargs.get("config") or (args[0] if args else None)
        return getattr(config, "name", None) or "default"

    # ------------------------------------------------------------------
    # BaseIntegration overrides (API-only — no CLI binary, mirrors flagsmith.py)
    # ------------------------------------------------------------------

    def get_version_command(self):
        return [self.command, "--version"]

    def get_version(self, use_cache: bool = True) -> Optional[str]:
        return "api"

    def parse_version(self, version_output: str) -> str:
        return "api"

    def is_available(self, use_cache: bool = True) -> bool:
        """Reachability of the OIDC discovery document, not a CLI binary."""
        try:
            self._discover()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    @property
    def _issuer(self) -> str:
        if not self.config.endpoints or not self.config.endpoints.address:
            raise ValueError(f"'{self.integration_name}': endpoints.address (OIDC issuer URL) is required")
        return self.config.endpoints.address.rstrip("/")

    @property
    def _client_id(self) -> str:
        oauth2 = self.config.authentication.oauth2 if self.config.authentication else None
        if not oauth2 or not oauth2.client_id:
            raise ValueError(f"'{self.integration_name}': authentication.oauth2.client_id is required")
        value = self._get_env_var(oauth2.client_id)
        if not value:
            raise ValueError(
                f"'{self.integration_name}': env var '{oauth2.client_id}' (client_id reference) is not set"
            )
        return value

    @property
    def _scope(self) -> str:
        oauth2 = self.config.authentication.oauth2 if self.config.authentication else None
        if oauth2 and oauth2.scope:
            return self._get_env_var(oauth2.scope, _DEFAULT_SCOPE) or _DEFAULT_SCOPE
        return _DEFAULT_SCOPE

    # ------------------------------------------------------------------
    # OIDC mechanics
    # ------------------------------------------------------------------

    def _discover(self) -> Dict[str, Any]:
        if self._discovery_cache is not None:
            return self._discovery_cache
        url = f"{self._issuer}/.well-known/openid-configuration"
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read())
        self._discovery_cache = data
        return data

    def _post_form(self, url: str, fields: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
        body = urllib.parse.urlencode(fields).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read())
            except Exception:
                return exc.code, {"error": str(exc)}

    def _fetch_userinfo(self, discovery: Dict[str, Any], access_token: str) -> Dict[str, Any]:
        userinfo_endpoint = discovery.get("userinfo_endpoint")
        if not userinfo_endpoint:
            return {}
        req = urllib.request.Request(userinfo_endpoint, method="GET")
        req.add_header("Authorization", f"Bearer {access_token}")
        try:
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                return json.loads(resp.read())
        except Exception as exc:
            logger.debug("oidc_userinfo_failed", integration_name=self.integration_name, error=str(exc))
            return {}

    def _refresh(self, discovery: Dict[str, Any], refresh_token: str) -> Optional[Dict[str, Any]]:
        token_endpoint = discovery["token_endpoint"]
        status, data = self._post_form(
            token_endpoint,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._client_id,
            },
        )
        if status != 200 or "access_token" not in data:
            return None
        return data

    # ------------------------------------------------------------------
    # IIdentityProvider capability
    # ------------------------------------------------------------------

    def check_auth(self) -> Tuple[bool, str]:
        """Check whether a cached session is valid, refreshing it silently if possible."""
        cached = load_token(self.integration_name)
        if not cached:
            return False, "Not logged in. Run with --login to sign in."

        if cached.get("expires_at", 0) > time.time() + 30:
            claims = cached.get("claims") or {}
            who = claims.get("email") or claims.get("preferred_username") or claims.get("sub") or "unknown"
            return True, f"Authenticated as {who}"

        refresh_token = cached.get("refresh_token")
        if not refresh_token:
            return False, "Session expired. Run with --login to sign in again."

        try:
            discovery = self._discover()
            refreshed = self._refresh(discovery, refresh_token)
        except Exception as exc:
            return False, f"Could not reach identity provider to refresh: {exc}"

        if not refreshed:
            clear_token(self.integration_name)
            return False, "Session expired and refresh failed. Run with --login to sign in again."

        self._persist_token(refreshed, claims=cached.get("claims"))
        claims = cached.get("claims") or {}
        who = claims.get("email") or claims.get("preferred_username") or claims.get("sub") or "unknown"
        return True, f"Authenticated as {who} (session refreshed)"

    def login(self) -> Tuple[bool, str]:
        """Drive the OAuth 2.0 Device Authorization Grant and cache the resulting session."""
        try:
            discovery = self._discover()
        except Exception as exc:
            return False, f"Could not reach identity provider at '{self._issuer}': {exc}"

        device_endpoint = discovery.get("device_authorization_endpoint")
        if not device_endpoint:
            return False, f"'{self.integration_name}': issuer does not advertise a device_authorization_endpoint"

        status, device = self._post_form(
            device_endpoint,
            {"client_id": self._client_id, "scope": self._scope},
        )
        if status != 200 or "device_code" not in device:
            return False, f"Device authorization request failed: {device.get('error_description', device)}"

        verification_uri = device.get("verification_uri_complete") or device.get("verification_uri")
        print(f"\n  To sign in, open: {verification_uri}")
        if not device.get("verification_uri_complete"):
            print(f"  And enter code:   {device.get('user_code')}\n")
        else:
            print()

        interval = device.get("interval", 5)
        expires_at = time.time() + device.get("expires_in", 900)
        token_endpoint = discovery["token_endpoint"]

        while time.time() < expires_at:
            time.sleep(interval)
            status, token = self._post_form(
                token_endpoint,
                {
                    "grant_type": _DEVICE_GRANT,
                    "device_code": device["device_code"],
                    "client_id": self._client_id,
                },
            )
            error = token.get("error")
            if status == 200 and "access_token" in token:
                claims = self._fetch_userinfo(discovery, token["access_token"])
                self._persist_token(token, claims=claims)
                who = claims.get("email") or claims.get("preferred_username") or claims.get("sub") or "unknown"
                return True, f"Logged in as {who}"
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            return False, f"Login failed: {token.get('error_description', error or token)}"

        return False, "Login timed out waiting for authorization."

    def get_access_token(self) -> Optional[str]:
        """Return the current cached bearer token, ensuring it is still valid first."""
        ok, _ = self.check_auth()
        if not ok:
            return None
        cached = load_token(self.integration_name)
        return cached.get("access_token") if cached else None

    def get_identity_claims(self) -> Optional[Dict[str, Any]]:
        """Return the cached identity claims (sub/email/preferred_username) for `actor` resolution."""
        cached = load_token(self.integration_name)
        return cached.get("claims") if cached else None

    def _persist_token(self, token: Dict[str, Any], claims: Optional[Dict[str, Any]]) -> None:
        save_token(
            self.integration_name,
            {
                "access_token": token["access_token"],
                "refresh_token": token.get("refresh_token"),
                "expires_at": time.time() + token.get("expires_in", 3600),
                "claims": claims or {},
            },
        )
