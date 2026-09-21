#!/usr/bin/env python3
"""Reusable authentication models for platform integrations.

All fields are key references resolved at runtime from centralized
environment variable, secret, and feature declarations.

Two differences from v1's real `auth_models.py`, found by comparison and not
carried over here:

- `AuthenticationModel.env_vars`/`.env_var` (declared, never consumed
  anywhere in v1's codebase) are dropped — dead fields, same vestigial-field
  pattern as `is_control` (ADR-0013).
- `AuthenticationModel` gains `validate_method_matches_populated_config()`
  (below), which v1 never had: v1's `method` and its method-specific blocks
  (`oauth2`, `aws`, ...) had no cross-field check at all, so `method: oauth2`
  with `oauth2` unset (or `aws` populated instead) validated successfully and
  would silently resolve wrong/empty credentials at runtime — the same
  "validates fine, silently wrong" bug class real ADR-0071 already found
  elsewhere (Provisioner `backend`/`properties`).
"""

from typing import Literal

from pydantic import Field, model_validator

from strata.models.common_models import PlatformBaseModel


class OAuth2AuthenticationModel(PlatformBaseModel):
    """
    OAuth2/OpenID Connect authentication.

    Used by: Azure (Service Principal), generic OAuth2 providers.
    All fields are key references resolved from environment declarations.
    """

    client_id: str = Field(description="Key reference for OAuth2 client/application ID")
    client_secret: str = Field(description="Key reference for OAuth2 client secret")
    tenant_id: str | None = Field(
        None,
        description="Key reference for tenant/directory ID (Azure AD, Okta, etc.)",
    )
    token_url: str | None = Field(None, description="Key reference for OAuth2 token endpoint URL")
    authorization_url: str | None = Field(None, description="Key reference for OAuth2 authorization endpoint URL")
    scope: str | None = Field(None, description="Key reference for OAuth2 scopes (space-separated)")


class AWSAuthenticationModel(PlatformBaseModel):
    """
    AWS access key authentication.

    All fields are key references resolved from environment declarations.
    """

    access_key_id: str = Field(description="Key reference for AWS access key ID")
    secret_access_key: str = Field(description="Key reference for AWS secret access key")
    session_token: str | None = Field(
        None,
        description="Key reference for AWS session token (temporary credentials)",
    )
    region: str | None = Field(None, description="Key reference for AWS region")
    role_arn: str | None = Field(None, description="Key reference for AWS IAM role ARN for AssumeRole")


class GCPAuthenticationModel(PlatformBaseModel):
    """
    Google Cloud Platform service account authentication.

    All fields are key references resolved from environment declarations.
    """

    project_id: str = Field(description="Key reference for GCP project ID")
    credentials_json: str | None = Field(
        None,
        description="Key reference for GCP service account JSON credentials (as string content)",
    )
    credentials_file: str | None = Field(
        None,
        description="Key reference for path to GCP service account JSON file",
    )
    service_account_email: str | None = Field(None, description="Key reference for GCP service account email")


class APIKeyAuthenticationModel(PlatformBaseModel):
    """
    API key authentication.

    Used by: Generic REST APIs, some cloud services.
    All fields are key references resolved from environment declarations.
    """

    api_key: str = Field(description="Key reference for API key value")
    api_secret: str | None = Field(None, description="Key reference for API secret (used alongside the API key)")
    endpoint: str | None = Field(None, description="Key reference for API endpoint URL")
    header_name: str | None = Field(
        None,
        description="Key reference for HTTP header name (default: 'X-API-Key')",
    )


class CertificateAuthenticationModel(PlatformBaseModel):
    """
    Certificate-based authentication (mTLS).

    All fields are key references resolved from environment declarations.
    """

    certificate_path: str = Field(description="Key reference for client certificate file path")
    private_key_path: str = Field(description="Key reference for private key file path")
    ca_bundle_path: str | None = Field(None, description="Key reference for CA bundle file path")
    certificate_password: str | None = Field(
        None,
        description="Key reference for certificate password (if encrypted)",
    )


class SAMLAuthenticationModel(PlatformBaseModel):
    """
    SAML 2.0 authentication.

    All fields are key references resolved from environment declarations.
    """

    idp_entity_id: str = Field(description="Key reference for SAML Identity Provider entity ID")
    sp_entity_id: str = Field(description="Key reference for SAML Service Provider entity ID")
    sso_url: str = Field(description="Key reference for SAML Single Sign-On URL")
    certificate_path: str | None = Field(None, description="Key reference for SAML certificate file path")


class CLIAuthenticationModel(PlatformBaseModel):
    """
    CLI-based authentication using local credentials.

    Uses existing CLI authentication: az login, gcloud auth, aws configure.
    No credential references needed - uses ambient credentials.
    """

    use_cli: bool = Field(
        default=True,
        description="Use local CLI authentication (az, gcloud, aws)",
    )


class ManagedIdentityAuthenticationModel(PlatformBaseModel):
    """
    Managed/Workload Identity authentication.

    Uses cloud provider managed identity (Azure MSI, GCP Workload Identity, AWS IAM Roles).
    No credential references needed - uses ambient credentials.
    """

    use_managed_identity: bool = Field(
        default=True,
        description="Use cloud provider managed identity",
    )
    # Azure-specific
    client_id: str | None = Field(
        None,
        description="Key reference for user-assigned managed identity client ID (Azure only)",
    )


class AuthenticationModel(PlatformBaseModel):
    """
    Authentication configuration for cloud provider or integration access.

    Specifies authentication method and corresponding credentials as key references.
    Keys are resolved at runtime from centralized environment declarations.
    """

    method: Literal[
        "oauth2",
        "aws",
        "gcp",
        "api_key",
        "certificate",
        "saml",
        "cli",
        "managed_identity",
    ] = Field(description="Authentication method to use")

    # Method-specific configurations (only one should be populated based on method)
    oauth2: OAuth2AuthenticationModel | None = Field(None, description="OAuth2/Service Principal authentication")
    aws: AWSAuthenticationModel | None = Field(None, description="AWS access key authentication")
    gcp: GCPAuthenticationModel | None = Field(None, description="GCP service account authentication")
    api_key: APIKeyAuthenticationModel | None = Field(None, description="API key authentication")
    certificate: CertificateAuthenticationModel | None = Field(
        None, description="Certificate-based authentication (mTLS)"
    )
    saml: SAMLAuthenticationModel | None = Field(None, description="SAML 2.0 authentication")
    cli: CLIAuthenticationModel | None = Field(None, description="CLI-based authentication")
    managed_identity: ManagedIdentityAuthenticationModel | None = Field(
        None, description="Managed/Workload Identity authentication"
    )

    # Operator documentation field (not used by the runtime — for human reference only)
    description: str | None = Field(
        None,
        description="Human-readable description of this authentication configuration and setup instructions",
    )

    @model_validator(mode="after")
    def validate_method_matches_populated_config(self) -> "AuthenticationModel":
        """Ensure exactly the config matching `method` is populated.

        `method` selects which of the method-specific fields applies. The matching
        field must be set, and no other method's field may be set at the same time
        (that would be ambiguous about which credentials actually apply).
        """
        method_fields = (
            "oauth2",
            "aws",
            "gcp",
            "api_key",
            "certificate",
            "saml",
            "cli",
            "managed_identity",
        )
        populated = [name for name in method_fields if getattr(self, name) is not None]

        if self.method not in populated:
            raise ValueError(f"method is '{self.method}' but '{self.method}' configuration is not set")

        extra = [name for name in populated if name != self.method]
        if extra:
            raise ValueError(
                f"method is '{self.method}' but unrelated configuration is also set: {', '.join(extra)}"
            )

        return self
