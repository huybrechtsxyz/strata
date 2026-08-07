"""GitHub OAuth App identity-provider integration — zero-infrastructure CLI login (ADR-0067).

Standalone — not built on ``GenericOidcIdentityIntegration``. Classic GitHub OAuth Apps
do not expose standard OIDC discovery (no ``.well-known/openid-configuration``), issue
tokens that typically do not expire unless an app opts into token expiration, and expose
identity via GitHub's own REST API (``GET /user``) rather than an OIDC ``userinfo``
endpoint. It is, however, close enough in shape (device-code polling with
``authorization_pending``/``slow_down`` errors, per GitHub's own device flow docs) that
the polling loop below mirrors the generic implementation without literally sharing code
with it.

No client_secret is required — GitHub's device flow is designed for public/CLI clients.

Configuration YAML (the zero-infrastructure default — no IdP tenant, project, or domain
to configure, just a client_id from a GitHub OAuth App)::

    integrations:
      - name: strata-control-plane
        type: github_oauth
        capabilities: [identity]
        authentication:
          method: oauth2
          oauth2:
            client_id: STRATA_GITHUB_CLIENT_ID   # env var name holding the OAuth App's client id
            client_secret: UNUSED                 # required by the model; device flow needs no secret
            scope: GITHUB_OAUTH_SCOPES            # optional; env var, default "read:user user:email"

``endpoints.address`` may be set to a GitHub Enterprise Server base URL (e.g.
``https://github.example.com``) instead of the default github.com endpoints.

The identity claim returned for `actor` resolution prefers the GitHub *login* (username)
as ``preferred_username`` — matching ADR-0066's own CI-actor fallback, which already
treats ``GITHUB_ACTOR`` as the first-class signal for "who triggered this."
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.capabilities import IIdentityProvider
from strata.logger import get_logger
from strata.utils.identity_token_cache import clear_token, load_token, save_token

logger = get_logger(__name__)

_DEFAULT_SCOPE = "read:user user:email"
_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
_HTTP_TIMEOUT = 15
_GITHUB_BASE = "https://github.com"
_GITHUB_API_BASE = "https://api.github.com"


class GitHubOAuthIdentityIntegration(BaseIntegration):
    """OAuth device-code login to GitHub for the strata CLI — the zero-infrastructure default."""

    COMMAND = "github_oauth"  # API-only; no CLI binary required
    CAPABILITIES = [IIdentityProvider]

    @classmethod
    def _get_instance_key_static(cls, class_ref, *args, **kwargs) -> str:
        """One instance per declared integration name — a workspace may declare several."""
        config = kwargs.get("config") or (args[0] if args else None)
        return getattr(config, "name", None) or "default"

    # ------------------------------------------------------------------
    # BaseIntegration overrides (API-only — no CLI binary, mirrors generic_oidc)
    # ------------------------------------------------------------------

    def get_version_command(self):
        return [self.command, "--version"]

    def get_version(self, use_cache: bool = True) -> Optional[str]:
        return "api"

    def parse_version(self, version_output: str) -> str:
        return "api"

    def is_available(self, use_cache: bool = True) -> bool:
        try:
            self._client_id  # noqa: B018 - property access validates config
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    @property
    def _base(self) -> str:
        if self.config.endpoints and self.config.endpoints.address:
            return self.config.endpoints.address.rstrip("/")
        return _GITHUB_BASE

    @property
    def _api_base(self) -> str:
        if self.config.endpoints and self.config.endpoints.address:
            # GitHub Enterprise Server API convention
            return f"{self._base}/api/v3"
        return _GITHUB_API_BASE

    @property
    def _device_code_url(self) -> str:
        return f"{self._base}/login/device/code"

    @property
    def _token_url(self) -> str:
        return f"{self._base}/login/oauth/access_token"

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
    # GitHub device flow mechanics
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

    def _fetch_user(self, access_token: str) -> Dict[str, Any]:
        req = urllib.request.Request(f"{self._api_base}/user", method="GET")
        req.add_header("Authorization", f"Bearer {access_token}")
        req.add_header("Accept", "application/vnd.github+json")
        try:
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                return json.loads(resp.read())
        except Exception as exc:
            logger.debug("github_oauth_fetch_user_failed", integration_name=self.integration_name, error=str(exc))
            return {}

    def _claims_from_user(self, user: Dict[str, Any]) -> Dict[str, Any]:
        login = user.get("login")
        return {
            "email": user.get("email"),
            "preferred_username": login,
            "sub": str(user.get("id")) if user.get("id") is not None else login,
        }

    # ------------------------------------------------------------------
    # IIdentityProvider capability
    # ------------------------------------------------------------------

    def check_auth(self) -> Tuple[bool, str]:
        cached = load_token(self.integration_name)
        if not cached:
            return False, "Not logged in. Run with --login to sign in."

        expires_at = cached.get("expires_at")
        if expires_at is None:
            # Classic GitHub OAuth App tokens do not expire unless the app opts in —
            # trust the cache without a live round-trip on every command.
            claims = cached.get("claims") or {}
            who = claims.get("preferred_username") or claims.get("email") or "unknown"
            return True, f"Authenticated as {who}"

        if expires_at > time.time() + 30:
            claims = cached.get("claims") or {}
            who = claims.get("preferred_username") or claims.get("email") or "unknown"
            return True, f"Authenticated as {who}"

        refresh_token = cached.get("refresh_token")
        if not refresh_token:
            clear_token(self.integration_name)
            return False, "Session expired. Run with --login to sign in again."

        status, refreshed = self._post_form(
            self._token_url,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._client_id,
            },
        )
        if status != 200 or "access_token" not in refreshed:
            clear_token(self.integration_name)
            return False, "Session expired and refresh failed. Run with --login to sign in again."

        self._persist_token(refreshed, claims=cached.get("claims"))
        claims = cached.get("claims") or {}
        who = claims.get("preferred_username") or claims.get("email") or "unknown"
        return True, f"Authenticated as {who} (session refreshed)"

    def login(self) -> Tuple[bool, str]:
        status, device = self._post_form(
            self._device_code_url,
            {"client_id": self._client_id, "scope": self._scope},
        )
        if status != 200 or "device_code" not in device:
            return False, f"Device authorization request failed: {device.get('error_description', device)}"

        print(f"\n  To sign in, open: {device.get('verification_uri')}")
        print(f"  And enter code:   {device.get('user_code')}\n")

        interval = device.get("interval", 5)
        expires_at = time.time() + device.get("expires_in", 900)

        while time.time() < expires_at:
            time.sleep(interval)
            status, token = self._post_form(
                self._token_url,
                {
                    "client_id": self._client_id,
                    "device_code": device["device_code"],
                    "grant_type": _DEVICE_GRANT,
                },
            )
            error = token.get("error")
            if status == 200 and "access_token" in token:
                user = self._fetch_user(token["access_token"])
                claims = self._claims_from_user(user)
                self._persist_token(token, claims=claims)
                who = claims.get("preferred_username") or claims.get("email") or "unknown"
                return True, f"Logged in as {who}"
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            return False, f"Login failed: {token.get('error_description', error or token)}"

        return False, "Login timed out waiting for authorization."

    def get_access_token(self) -> Optional[str]:
        ok, _ = self.check_auth()
        if not ok:
            return None
        cached = load_token(self.integration_name)
        return cached.get("access_token") if cached else None

    def get_identity_claims(self) -> Optional[Dict[str, Any]]:
        cached = load_token(self.integration_name)
        return cached.get("claims") if cached else None

    def _persist_token(self, token: Dict[str, Any], claims: Optional[Dict[str, Any]]) -> None:
        payload: Dict[str, Any] = {
            "access_token": token["access_token"],
            "claims": claims or {},
        }
        # Only apps with token expiration enabled return these — classic tokens omit them.
        if "expires_in" in token:
            payload["expires_at"] = time.time() + token["expires_in"]
        if "refresh_token" in token:
            payload["refresh_token"] = token["refresh_token"]
        save_token(self.integration_name, payload)
