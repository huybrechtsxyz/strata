"""Identity-provider integrations — CLI-side OIDC/OAuth2 login (ADR-0067)."""

from strata.integrations.identity.auth0_identity_integration import Auth0IdentityIntegration
from strata.integrations.identity.aws_identity_integration import AwsIdentityIntegration
from strata.integrations.identity.azure_identity_integration import AzureIdentityIntegration
from strata.integrations.identity.generic_oidc_identity_integration import GenericOidcIdentityIntegration
from strata.integrations.identity.github_oauth_identity_integration import GitHubOAuthIdentityIntegration
from strata.integrations.identity.google_identity_integration import GoogleIdentityIntegration

__all__ = [
    "GenericOidcIdentityIntegration",
    "AzureIdentityIntegration",
    "GoogleIdentityIntegration",
    "AwsIdentityIntegration",
    "Auth0IdentityIntegration",
    "GitHubOAuthIdentityIntegration",
]
