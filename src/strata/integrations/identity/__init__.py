"""Identity-provider integrations — CLI-side OIDC/OAuth2 login (ADR-0067)."""

from strata.integrations.identity.azure_identity_integration import AzureIdentityIntegration
from strata.integrations.identity.generic_oidc_identity_integration import GenericOidcIdentityIntegration

__all__ = [
    "GenericOidcIdentityIntegration",
    "AzureIdentityIntegration",
]
