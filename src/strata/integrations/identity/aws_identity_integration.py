"""AWS IAM Identity Center identity-provider integration — CLI login for the control plane (ADR-0067).

Standalone — not built on `azure_cli`'s reuse pattern. `aws_cli` wraps IAM access-key/STS
credentials (``AWSCLIIntegration.get_identity()`` returns ``Account``/``UserId``/``Arn``
from STS), which are not OIDC tokens and share no mechanism with IAM Identity Center's own
device-authorization flow. This is a genuinely different AWS auth surface, not an
extension of what ``aws_cli.py`` already does — see ADR-0067's "Relationship to the
existing `azure_cli`/`aws_cli`/`gcloud_cli` integrations".

IAM Identity Center's ``sso-oidc`` API is JSON REST, not standard OIDC discovery or the
RFC 8628 form-encoded device grant used by ``GenericOidcIdentityIntegration`` — so this
class does not subclass it. The flow is: ``RegisterClient`` (once, cached) ->
``StartDeviceAuthorization`` -> poll ``CreateToken``.

Configuration YAML::

    integrations:
      - name: strata-control-plane
        type: aws_identity_center
        capabilities: [identity]
        endpoints:
          address: https://my-sso-portal.awsapps.com/start   # IAM Identity Center start URL

No client_id is configured ahead of time — IAM Identity Center dynamically registers a
public client on first use, and that registration is cached alongside the session so it
only happens once. Region is resolved the same way ``AWSCLIIntegration.get_region()``
already does (``AWS_DEFAULT_REGION``/``AWS_REGION`` env vars, falling back to an
already-configured ``aws_cli`` integration's own resolution) so operators do not have to
declare it twice.
"""

import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.capabilities import IAWSTool, IIdentityProvider
from strata.logger import get_logger
from strata.utils.identity_token_cache import clear_token, load_token, save_token

logger = get_logger(__name__)

_HTTP_TIMEOUT = 15
_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
_CLIENT_NAME = "strata-cli"


class AwsIdentityIntegration(BaseIntegration):
    """IAM Identity Center device-code login for the strata CLI — standalone, no reuse path."""

    COMMAND = "aws_identity_center"  # API-only; no CLI binary required
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
            self._start_url  # noqa: B018 - property access validates config
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    @property
    def _start_url(self) -> str:
        if not self.config.endpoints or not self.config.endpoints.address:
            raise ValueError(
                f"'{self.integration_name}': endpoints.address (IAM Identity Center start URL) is required"
            )
        return self.config.endpoints.address

    @property
    def _region(self) -> str:
        region = self._get_env_var("AWS_DEFAULT_REGION") or self._get_env_var("AWS_REGION")
        if region:
            return region

        try:
            from strata.services.integration_service import IntegrationService

            svc = IntegrationService.get_instance()
            if not svc.is_initialized():
                svc.initialize_integrations()
            for name in svc.get_integrations_with_capability(IAWSTool):
                integration = svc.get_integration(name)
                if integration is not None:
                    region = integration.get_region()
                    if region:
                        return region
        except Exception as exc:
            logger.debug("aws_identity_region_lookup_failed", error=str(exc))

        raise ValueError(
            f"'{self.integration_name}': could not resolve AWS region. "
            "Set AWS_DEFAULT_REGION/AWS_REGION or configure an aws_cli integration."
        )

    @property
    def _endpoint(self) -> str:
        return f"https://oidc.{self._region}.amazonaws.com"

    # ------------------------------------------------------------------
    # sso-oidc mechanics
    # ------------------------------------------------------------------

    def _post_json(self, url: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read())
            except Exception:
                return exc.code, {"error": str(exc)}

    def _register_client(self, cached: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Return a registered client, reusing the cached one if still valid."""
        existing = (cached or {}).get("registered_client")
        if existing and existing.get("client_secret_expires_at", 0) > time.time() + 60:
            return existing

        status, data = self._post_json(
            f"{self._endpoint}/client/register",
            {"clientName": _CLIENT_NAME, "clientType": "public"},
        )
        if status != 200 or "clientId" not in data:
            raise RuntimeError(f"Failed to register with IAM Identity Center: {data.get('error', data)}")
        return {
            "client_id": data["clientId"],
            "client_secret": data["clientSecret"],
            "client_secret_expires_at": data.get("clientSecretExpiresAt", 0),
        }

    def _fetch_userinfo(self, access_token: str) -> Dict[str, Any]:
        req = urllib.request.Request(f"{self._endpoint}/userinfo", method="GET")
        req.add_header("Authorization", f"Bearer {access_token}")
        try:
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                return json.loads(resp.read())
        except Exception as exc:
            logger.debug("aws_identity_userinfo_failed", integration_name=self.integration_name, error=str(exc))
            return {}

    # ------------------------------------------------------------------
    # IIdentityProvider capability
    # ------------------------------------------------------------------

    def check_auth(self) -> Tuple[bool, str]:
        cached = load_token(self.integration_name)
        if not cached:
            return False, "Not logged in. Run with --login to sign in."

        if cached.get("expires_at", 0) > time.time() + 30:
            claims = cached.get("claims") or {}
            who = claims.get("email") or claims.get("sub") or "unknown"
            return True, f"Authenticated as {who}"

        # IAM Identity Center tokens are not always refreshable depending on registration
        # scopes; when there is no refresh token, re-running --login is the fix.
        refresh_token = cached.get("refresh_token")
        registered_client = cached.get("registered_client")
        if not refresh_token or not registered_client:
            clear_token(self.integration_name)
            return False, "Session expired. Run with --login to sign in again."

        status, refreshed = self._post_json(
            f"{self._endpoint}/token",
            {
                "grantType": "refresh_token",
                "refreshToken": refresh_token,
                "clientId": registered_client["client_id"],
                "clientSecret": registered_client["client_secret"],
            },
        )
        if status != 200 or "accessToken" not in refreshed:
            clear_token(self.integration_name)
            return False, "Session expired and refresh failed. Run with --login to sign in again."

        self._persist_token(refreshed, claims=cached.get("claims"), registered_client=registered_client)
        claims = cached.get("claims") or {}
        who = claims.get("email") or claims.get("sub") or "unknown"
        return True, f"Authenticated as {who} (session refreshed)"

    def login(self) -> Tuple[bool, str]:
        cached = load_token(self.integration_name)
        try:
            registered_client = self._register_client(cached)
        except Exception as exc:
            return False, str(exc)

        status, device = self._post_json(
            f"{self._endpoint}/device_authorization",
            {
                "clientId": registered_client["client_id"],
                "clientSecret": registered_client["client_secret"],
                "startUrl": self._start_url,
            },
        )
        if status != 200 or "deviceCode" not in device:
            return False, f"Device authorization request failed: {device.get('error_description', device)}"

        verification_uri = device.get("verificationUriComplete") or device.get("verificationUri")
        print(f"\n  To sign in, open: {verification_uri}")
        if not device.get("verificationUriComplete"):
            print(f"  And enter code:   {device.get('userCode')}\n")
        else:
            print()

        interval = device.get("interval", 5)
        expires_at = time.time() + device.get("expiresIn", 900)

        while time.time() < expires_at:
            time.sleep(interval)
            status, token = self._post_json(
                f"{self._endpoint}/token",
                {
                    "grantType": _DEVICE_GRANT,
                    "deviceCode": device["deviceCode"],
                    "clientId": registered_client["client_id"],
                    "clientSecret": registered_client["client_secret"],
                },
            )
            error = token.get("error")
            if status == 200 and "accessToken" in token:
                claims = self._fetch_userinfo(token["accessToken"])
                self._persist_token(token, claims=claims, registered_client=registered_client)
                who = claims.get("email") or claims.get("sub") or "unknown"
                return True, f"Logged in as {who}"
            if error in ("AuthorizationPendingException", "authorization_pending"):
                continue
            if error in ("SlowDownException", "slow_down"):
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

    def _persist_token(
        self,
        token: Dict[str, Any],
        claims: Optional[Dict[str, Any]],
        registered_client: Dict[str, Any],
    ) -> None:
        save_token(
            self.integration_name,
            {
                "access_token": token["accessToken"],
                "refresh_token": token.get("refreshToken"),
                "expires_at": time.time() + token.get("expiresIn", 3600),
                "claims": claims or {},
                "registered_client": registered_client,
            },
        )
