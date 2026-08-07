"""Google identity-provider integration — CLI login for the control plane (ADR-0067).

Reuses an already-authenticated ``gcloud_cli`` integration when one is configured and
logged in — ``GCloudCLIIntegration.get_identity_token(audience)`` returns a Google-signed
OIDC ID token scoped to that audience, so an operator who already ran ``gcloud auth
login`` for Terraform/GKE never has to sign in twice. Falls back to the standard OIDC
device-code flow (inherited from ``GenericOidcIdentityIntegration``) when no
``gcloud_cli`` integration is configured or authenticated.

Unlike Azure's reuse path (an opaque ARM-style access token), the reused credential here
is itself a signed ID token — its claims are read directly (unverified locally, since the
token came from an already-trusted local ``gcloud`` session, not a remote peer; see
``strata.utils.jwt_utils``) rather than via a userinfo endpoint call.

Configuration YAML (device-code fallback only needs ``client_id``; reuse needs nothing
extra beyond a ``gcloud_cli`` integration already existing in the same workspace)::

    integrations:
      - name: gcloud
        type: gcloud_cli
        capabilities: [gcloud]

      - name: strata-control-plane
        type: google
        capabilities: [identity]
        authentication:
          method: oauth2
          oauth2:
            client_id: STRATA_OIDC_CLIENT_ID   # env var name holding the OAuth client id
            client_secret: UNUSED               # required by the model; device-code is a public-client flow
"""

from typing import Any, Dict, Optional, Tuple

from strata.integrations.capabilities import IGCloudTool, IIdentityProvider
from strata.integrations.identity.generic_oidc_identity_integration import GenericOidcIdentityIntegration
from strata.logger import get_logger
from strata.utils.jwt_utils import decode_payload_unverified

logger = get_logger(__name__)

_GOOGLE_ISSUER = "https://accounts.google.com"


class GoogleIdentityIntegration(GenericOidcIdentityIntegration):
    """OIDC login to Google for the CLI, reusing `gcloud_cli` when available."""

    COMMAND = "google"  # API-only; no CLI binary of its own — reuses gcloud_cli's `gcloud` when present
    CAPABILITIES = [IIdentityProvider]

    @property
    def _issuer(self) -> str:
        if self.config.endpoints and self.config.endpoints.address:
            return self.config.endpoints.address.rstrip("/")
        return _GOOGLE_ISSUER

    # ------------------------------------------------------------------
    # gcloud_cli reuse
    # ------------------------------------------------------------------

    def _find_authenticated_gcloud_cli(self):
        """Return an already-configured, authenticated `gcloud_cli` integration, or None."""
        try:
            from strata.services.integration_service import IntegrationService

            svc = IntegrationService.get_instance()
            if not svc.is_initialized():
                svc.initialize_integrations()
            for name in svc.get_integrations_with_capability(IGCloudTool):
                integration = svc.get_integration(name)
                if integration is None:
                    continue
                ok, _ = integration.ensure_available()
                if ok:
                    return integration
        except Exception as exc:
            logger.debug("google_identity_cli_reuse_lookup_failed", error=str(exc))
        return None

    def _reuse_identity_token(self) -> Optional[str]:
        gcloud_cli = self._find_authenticated_gcloud_cli()
        if gcloud_cli is None:
            return None
        return gcloud_cli.get_identity_token(audience=self._client_id)

    # ------------------------------------------------------------------
    # IIdentityProvider, reuse-first
    # ------------------------------------------------------------------

    def check_auth(self) -> Tuple[bool, str]:
        token = self._reuse_identity_token()
        if token:
            claims = decode_payload_unverified(token)
            who = claims.get("email") or claims.get("sub") or "gcloud CLI session"
            return True, f"Authenticated via gcloud CLI as {who}"
        return super().check_auth()

    def login(self) -> Tuple[bool, str]:
        token = self._reuse_identity_token()
        if token:
            claims = decode_payload_unverified(token)
            who = claims.get("email") or claims.get("sub") or "gcloud CLI session"
            return True, f"Reused existing gcloud CLI login as {who} — no separate sign-in needed"
        return super().login()

    def get_access_token(self) -> Optional[str]:
        token = self._reuse_identity_token()
        if token:
            return token
        return super().get_access_token()

    def get_identity_claims(self) -> Optional[Dict[str, Any]]:
        token = self._reuse_identity_token()
        if token:
            claims = decode_payload_unverified(token)
            email = claims.get("email")
            sub = claims.get("sub")
            if email or sub:
                return {"email": email, "preferred_username": email, "sub": sub}
        return super().get_identity_claims()
