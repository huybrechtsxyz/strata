"""Auth0 identity-provider integration — CLI login for the control plane (ADR-0067).

Auth0 already exposes standard OIDC discovery (``/.well-known/openid-configuration``)
and supports the Device Authorization Grant used by ``GenericOidcIdentityIntegration``,
so this is a thin subclass rather than a new mechanism — it exists as its own named
``type`` (rather than telling operators to use ``generic_oidc``) purely for
discoverability: Auth0 is the zero-infrastructure *managed* IdP option this ADR calls
out explicitly, and it has its own config convention (a "domain," not a full issuer URL)
worth matching in YAML.

There is no reuse path here (unlike Azure/Google) — strata has no existing integration
that already authenticates to Auth0, so every login goes through the standard device-code
flow inherited unchanged from ``GenericOidcIdentityIntegration``.

Configuration YAML::

    integrations:
      - name: strata-control-plane
        type: auth0
        capabilities: [identity]
        authentication:
          method: oauth2
          oauth2:
            client_id: STRATA_OIDC_CLIENT_ID    # env var name holding the Auth0 application's client id
            tenant_id: STRATA_AUTH0_DOMAIN      # env var name holding the Auth0 domain (e.g. my-tenant.us.auth0.com)
            client_secret: UNUSED               # required by the model; device-code is a public-client flow

``endpoints.address`` may be set instead of ``tenant_id`` for a custom Auth0 domain,
mirroring the same precedence ``AzureIdentityIntegration`` uses.

Known limitation, noted deliberately: Auth0's ``audience`` parameter (for minting a JWT
access token scoped to a specific API, as opposed to the default opaque token) is not
yet configurable here — there is no natural field for it in the shared
``OAuth2AuthenticationModel``, and the default scope-based flow (``openid profile
email``) is sufficient for authenticating a human for `actor` resolution. Add an
``audience`` field if/when a workspace needs Auth0-protected API access tokens rather
than just an authenticated identity.
"""

from strata.integrations.capabilities import IIdentityProvider
from strata.integrations.identity.generic_oidc_identity_integration import GenericOidcIdentityIntegration


class Auth0IdentityIntegration(GenericOidcIdentityIntegration):
    """OIDC device-code login to Auth0 for the CLI — no reuse path, same mechanism as generic OIDC."""

    COMMAND = "auth0"  # API-only; no CLI binary required
    CAPABILITIES = [IIdentityProvider]

    @property
    def _issuer(self) -> str:
        if self.config.endpoints and self.config.endpoints.address:
            return self.config.endpoints.address.rstrip("/")

        oauth2 = self.config.authentication.oauth2 if self.config.authentication else None
        domain_ref = oauth2.tenant_id if oauth2 else None
        if not domain_ref:
            raise ValueError(
                f"'{self.integration_name}': endpoints.address or authentication.oauth2.tenant_id "
                "(the Auth0 domain, e.g. my-tenant.us.auth0.com) is required"
            )
        domain = self._get_env_var(domain_ref)
        if not domain:
            raise ValueError(f"'{self.integration_name}': env var '{domain_ref}' (Auth0 domain reference) is not set")
        return f"https://{domain}"
