"""OIDC relying party for the strata control plane (ADR-0067 Step 7).

The server never stores a password — it is an OAuth2/OIDC Relying Party against
one externally-configured identity provider, using the Authorization Code + PKCE
grant for interactive human login. See the module-level notes below for what this
deliberately does NOT do yet.

Configuration is server-level (CLI flags / env vars on `serve run`), not a workspace
`spec.integrations` entry — unlike the CLI-side identity-provider integrations
(`strata.integrations.identity`), this server has no "workspace" concept at all
(ADR-0065: "consumed by many workspaces... its bind/TLS config is process/operational
config, not one workspace's deployment config"). The same reasoning applies here.

What this deliberately does NOT do (yet):
- No session persistence/revocation — `server.auth.session_tokens` mints a stateless,
  short-lived bearer token; Step 8 ("Session store") adds a real, revocable record.
- No RBAC — Step 9. A caller who completes login is authenticated, not yet authorized
  to do anything specific.
- No Client Credentials (M2M) HTTP route on this server — per the ADR, the IdP issues
  those tokens directly to the calling service; this server has nothing to proxy.
  `client_credentials_token()` exists here only so the relying party can validate its
  own configuration end-to-end in tests, mirroring the ADR's framing of "one identity
  provider, two grant types" in one module.
- No browser redirects — every route returns JSON (`{"authorization_url": ...}`,
  not a 302), the same "hand back the URL, let the caller decide" philosophy the
  CLI-side device-code flow already uses, and consistent with every other route this
  server exposes (ADR-0065's API is JSON-only). A production frontend integration
  (redirect-with-fragment, or a first-party cookie) is a webapp/Step-8 concern.

`id_token` signature verification uses `authlib`/`joserfc` (JWKS fetch, RS256/ES256
verification, key rotation) rather than hand-rolled crypto — this is deliberately
the one place this module reaches for a real dependency instead of stdlib: verifying
a JWT signature correctly (algorithm confusion, key rotation, clock skew) is exactly
the kind of security-critical code this codebase does not roll by hand elsewhere
either (see e.g. `azure_keyvault.py`'s use of real SDKs for anything credential-shaped).
Requires the optional dependency: pip install xyz-strata[server]
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

try:
    from joserfc import jwt as _joserfc_jwt
    from joserfc.errors import JoseError
    from joserfc.jwk import KeySet
    from joserfc.jwt import JWTClaimsRegistry
except ImportError as _exc:
    raise ImportError(
        "The 'authlib' package is required for the strata control-plane's OIDC relying party.\n"
        "Install it with: pip install xyz-strata[server]"
    ) from _exc

_HTTP_TIMEOUT = 15
_DEFAULT_SCOPE = "openid profile email"


@dataclass(frozen=True)
class OidcRelyingPartyConfig:
    """Server-level OIDC configuration for `serve run` (ADR-0067 Step 7).

    `client_secret` is optional — a public client using PKCE alone (the default,
    matching the CLI-side device-code integrations' own public-client reasoning)
    does not need one; a confidential client registered with the IdP does.
    """

    issuer: str
    client_id: str
    redirect_base: str
    client_secret: Optional[str] = None
    scope: str = _DEFAULT_SCOPE


class OidcRelyingParty:
    """Drives the Authorization Code + PKCE grant against one configured IdP."""

    def __init__(self, config: OidcRelyingPartyConfig) -> None:
        self._config = config
        self._discovery_cache: Optional[Dict[str, Any]] = None
        self._jwks_cache: Optional[KeySet] = None

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self) -> Dict[str, Any]:
        """Fetch (and cache, process-lifetime) the issuer's OIDC discovery document."""
        if self._discovery_cache is not None:
            return self._discovery_cache
        url = f"{self._config.issuer.rstrip('/')}/.well-known/openid-configuration"
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read())
        self._discovery_cache = data
        return data

    @property
    def redirect_uri(self) -> str:
        return f"{self._config.redirect_base.rstrip('/')}/auth/callback"

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

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

    def fetch_userinfo(self, discovery: Dict[str, Any], access_token: str) -> Dict[str, Any]:
        userinfo_endpoint = discovery.get("userinfo_endpoint")
        if not userinfo_endpoint:
            return {}
        req = urllib.request.Request(userinfo_endpoint, method="GET")
        req.add_header("Authorization", f"Bearer {access_token}")
        try:
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                return json.loads(resp.read())
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Authorization Code + PKCE (human login)
    # ------------------------------------------------------------------

    def build_authorization_url(self, state: str, code_challenge: str, nonce: str) -> str:
        """Return the URL a client should send the browser to, to begin login.

        `nonce` is required, not optional — it is what `verify_id_token()` checks the
        returned `id_token` against, and skipping it would silently reopen the id_token
        replay gap this module exists to close.
        """
        discovery = self.discover()
        params = {
            "client_id": self._config.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": self._config.scope,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "nonce": nonce,
        }
        return f"{discovery['authorization_endpoint']}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str, code_verifier: str) -> Tuple[bool, Dict[str, Any]]:
        """Exchange an authorization code for tokens. Returns (success, token_response_or_error)."""
        discovery = self.discover()
        fields = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self._config.client_id,
            "code_verifier": code_verifier,
        }
        if self._config.client_secret:
            fields["client_secret"] = self._config.client_secret
        status, data = self._post_form(discovery["token_endpoint"], fields)
        return (status == 200 and "access_token" in data), data

    # ------------------------------------------------------------------
    # id_token verification (real JWT/JWKS signature check, via authlib/joserfc)
    # ------------------------------------------------------------------

    def _fetch_jwks(self) -> KeySet:
        """Fetch (and cache, process-lifetime) the issuer's JWKS document as a joserfc KeySet."""
        if self._jwks_cache is not None:
            return self._jwks_cache
        discovery = self.discover()
        jwks_uri = discovery.get("jwks_uri")
        if not jwks_uri:
            raise ValueError("issuer's discovery document does not advertise a jwks_uri")
        with urllib.request.urlopen(jwks_uri, timeout=_HTTP_TIMEOUT) as resp:
            jwks_data = json.loads(resp.read())
        key_set = KeySet.import_key_set(jwks_data)
        self._jwks_cache = key_set
        return key_set

    def verify_id_token(self, id_token: str, nonce: str) -> Dict[str, Any]:
        """Verify an `id_token`'s signature and standard claims. Raises ValueError on any failure.

        Checks, in order: signature (against the issuer's published JWKS, key rotation
        handled by `joserfc`), `iss` (must match the discovery document's own declared
        issuer — not merely the configured issuer string, which may differ by a trailing
        slash), `aud` (must include this relying party's `client_id`), `exp`/`iat`
        (standard time-bound validity), and finally `nonce` (must match the value bound
        to this specific login attempt — the check hand-rolled crypto would be most
        likely to get wrong is exactly this one, since it is not a signature check at
        all and is easy to forget entirely).
        """
        discovery = self.discover()
        key_set = self._fetch_jwks()
        try:
            token = _joserfc_jwt.decode(id_token, key_set)
            claims_registry = JWTClaimsRegistry(
                leeway=30,  # tolerate minor clock skew between this server and the IdP
                iss={"essential": True, "value": discovery.get("issuer", self._config.issuer)},
                aud={"essential": True, "value": self._config.client_id},
                exp={"essential": True},
            )
            claims_registry.validate(token.claims)
        except JoseError as exc:
            raise ValueError(f"id_token verification failed: {exc}") from exc

        if token.claims.get("nonce") != nonce:
            raise ValueError("id_token verification failed: nonce mismatch")
        return dict(token.claims)

    # ------------------------------------------------------------------
    # Client Credentials (M2M) — see module docstring for why there is no HTTP route
    # ------------------------------------------------------------------

    def client_credentials_token(self, scope: Optional[str] = None) -> Tuple[bool, Dict[str, Any]]:
        """Request an M2M token directly from the IdP. Requires a confidential client secret."""
        if not self._config.client_secret:
            return False, {"error": "client_credentials requires a configured client_secret"}
        discovery = self.discover()
        status, data = self._post_form(
            discovery["token_endpoint"],
            {
                "grant_type": "client_credentials",
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
                "scope": scope or self._config.scope,
            },
        )
        return (status == 200 and "access_token" in data), data
