"""Azure AD/Entra ID identity-provider integration — CLI login for the control plane (ADR-0067).

Reuses an already-authenticated ``azure_cli`` integration when one is configured and
logged in — ``AzureCLIIntegration.get_access_token(resource=...)`` already accepts an
arbitrary audience, so an operator who already ran ``az login`` for Terraform never has
to sign in twice. Falls back to the standard OIDC device-code flow (inherited from
``GenericOidcIdentityIntegration``) when no ``azure_cli`` integration is configured or
authenticated.

Configuration YAML (device-code fallback only needs ``client_id``; reuse needs nothing
extra beyond an ``azure_cli`` integration already existing in the same workspace)::

    integrations:
      - name: azure
        type: azure_cli
        capabilities: [azure]

      - name: strata-control-plane
        type: azure_ad
        capabilities: [identity]
        authentication:
          method: oauth2
          oauth2:
            client_id: STRATA_OIDC_CLIENT_ID   # env var name holding the app registration's client id
            tenant_id: STRATA_OIDC_TENANT_ID    # env var name holding the Azure AD tenant id
            client_secret: UNUSED               # required by the model; device-code is a public-client flow

Simplification, noted deliberately rather than hidden: the reuse path passes the
control-plane's own client ID as the ``resource`` parameter to ``az account
get-access-token``. This is the common pattern for requesting a token for one's own
app registration, but a workspace whose app exposes a distinct App ID URI (``api://...``)
may need ``endpoints.address`` set to that URI instead — the code favours
``endpoints.address`` when present.
"""

from typing import Any, Dict, Optional, Tuple

from strata.integrations.capabilities import IAzureTool, IIdentityProvider
from strata.integrations.identity.generic_oidc_identity_integration import GenericOidcIdentityIntegration
from strata.logger import get_logger

logger = get_logger(__name__)

_ISSUER_TEMPLATE = "https://login.microsoftonline.com/{tenant}/v2.0"


class AzureIdentityIntegration(GenericOidcIdentityIntegration):
    """OIDC login to Azure AD/Entra ID for the CLI, reusing `azure_cli` when available."""

    COMMAND = "azure_ad"  # API-only; no CLI binary of its own — reuses azure_cli's `az` when present
    CAPABILITIES = [IIdentityProvider]

    @property
    def _issuer(self) -> str:
        if self.config.endpoints and self.config.endpoints.address:
            return self.config.endpoints.address.rstrip("/")

        oauth2 = self.config.authentication.oauth2 if self.config.authentication else None
        tenant_ref = oauth2.tenant_id if oauth2 else None
        if not tenant_ref:
            raise ValueError(
                f"'{self.integration_name}': endpoints.address or authentication.oauth2.tenant_id is required"
            )
        tenant = self._get_env_var(tenant_ref)
        if not tenant:
            raise ValueError(f"'{self.integration_name}': env var '{tenant_ref}' (tenant_id reference) is not set")
        return _ISSUER_TEMPLATE.format(tenant=tenant)

    # ------------------------------------------------------------------
    # azure_cli reuse
    # ------------------------------------------------------------------

    def _find_authenticated_azure_cli(self):
        """Return an already-configured, authenticated `azure_cli` integration, or None."""
        try:
            from strata.services.integration_service import IntegrationService

            svc = IntegrationService.get_instance()
            if not svc.is_initialized():
                svc.initialize_integrations()
            for name in svc.get_integrations_with_capability(IAzureTool):
                integration = svc.get_integration(name)
                if integration is None:
                    continue
                ok, _ = integration.ensure_available()
                if ok:
                    return integration
        except Exception as exc:
            logger.debug("azure_identity_cli_reuse_lookup_failed", error=str(exc))
        return None

    def _reuse_token(self) -> Optional[str]:
        azure_cli = self._find_authenticated_azure_cli()
        if azure_cli is None:
            return None
        return azure_cli.get_access_token(resource=self._client_id)

    def _reuse_who(self, azure_cli) -> str:
        if hasattr(azure_cli, "get_signed_in_user"):
            user = azure_cli.get_signed_in_user()
            if user and user.get("name"):
                return user["name"]
        return "az CLI session"

    # ------------------------------------------------------------------
    # IIdentityProvider, reuse-first
    # ------------------------------------------------------------------

    def check_auth(self) -> Tuple[bool, str]:
        azure_cli = self._find_authenticated_azure_cli()
        if azure_cli is not None:
            token = azure_cli.get_access_token(resource=self._client_id)
            if token:
                return True, f"Authenticated via az CLI as {self._reuse_who(azure_cli)}"
        return super().check_auth()

    def login(self) -> Tuple[bool, str]:
        azure_cli = self._find_authenticated_azure_cli()
        if azure_cli is not None:
            token = azure_cli.get_access_token(resource=self._client_id)
            if token:
                return (
                    True,
                    f"Reused existing az CLI login as {self._reuse_who(azure_cli)} — no separate sign-in needed",
                )
        return super().login()

    def get_access_token(self) -> Optional[str]:
        token = self._reuse_token()
        if token:
            return token
        return super().get_access_token()

    def get_identity_claims(self) -> Optional[Dict[str, Any]]:
        azure_cli = self._find_authenticated_azure_cli()
        if azure_cli is not None and azure_cli.get_access_token(resource=self._client_id):
            who = self._reuse_who(azure_cli)
            if who != "az CLI session":
                return {"email": who, "preferred_username": who, "sub": who}
        return super().get_identity_claims()
