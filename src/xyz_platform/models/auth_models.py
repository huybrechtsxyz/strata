#!/usr/bin/env python3
"""
===============================================================================
Script Name   : auth_models.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Reusable authentication models for platform integrations.
                All fields reference keys that get resolved at runtime from
                centralized environment variable/secret/feature declarations.
===============================================================================
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal


class OAuth2AuthenticationModel(BaseModel):
    """
    OAuth2/OpenID Connect authentication.

    Used by: Azure (Service Principal), generic OAuth2 providers.
    All fields are key references resolved from environment declarations.
    """

    client_id: str = Field(description="Key reference for OAuth2 client/application ID")
    client_secret: str = Field(description="Key reference for OAuth2 client secret")
    tenant_id: Optional[str] = Field(
        None,
        description="Key reference for tenant/directory ID (Azure AD, Okta, etc.)",
    )
    token_url: Optional[str] = Field(
        None, description="Key reference for OAuth2 token endpoint URL"
    )
    authorization_url: Optional[str] = Field(
        None, description="Key reference for OAuth2 authorization endpoint URL"
    )
    scope: Optional[str] = Field(
        None, description="Key reference for OAuth2 scopes (space-separated)"
    )


class AWSAuthenticationModel(BaseModel):
    """
    AWS access key authentication.

    All fields are key references resolved from environment declarations.
    """

    access_key_id: str = Field(description="Key reference for AWS access key ID")
    secret_access_key: str = Field(
        description="Key reference for AWS secret access key"
    )
    session_token: Optional[str] = Field(
        None,
        description="Key reference for AWS session token (temporary credentials)",
    )
    region: Optional[str] = Field(None, description="Key reference for AWS region")
    role_arn: Optional[str] = Field(
        None, description="Key reference for AWS IAM role ARN for AssumeRole"
    )


class GCPAuthenticationModel(BaseModel):
    """
    Google Cloud Platform service account authentication.

    All fields are key references resolved from environment declarations.
    """

    project_id: str = Field(description="Key reference for GCP project ID")
    credentials_json: Optional[str] = Field(
        None,
        description="Key reference for GCP service account JSON credentials (as string content)",
    )
    credentials_file: Optional[str] = Field(
        None,
        description="Key reference for path to GCP service account JSON file",
    )
    service_account_email: Optional[str] = Field(
        None, description="Key reference for GCP service account email"
    )


class APIKeyAuthenticationModel(BaseModel):
    """
    API key authentication.

    Used by: Generic REST APIs, some cloud services.
    All fields are key references resolved from environment declarations.
    """

    api_key: str = Field(description="Key reference for API key value")
    header_name: Optional[str] = Field(
        None,
        description="Key reference for HTTP header name (default: 'X-API-Key')",
    )


class CertificateAuthenticationModel(BaseModel):
    """
    Certificate-based authentication (mTLS).

    All fields are key references resolved from environment declarations.
    """

    certificate_path: str = Field(
        description="Key reference for client certificate file path"
    )
    private_key_path: str = Field(description="Key reference for private key file path")
    ca_bundle_path: Optional[str] = Field(
        None, description="Key reference for CA bundle file path"
    )
    certificate_password: Optional[str] = Field(
        None,
        description="Key reference for certificate password (if encrypted)",
    )


class SAMLAuthenticationModel(BaseModel):
    """
    SAML 2.0 authentication.

    All fields are key references resolved from environment declarations.
    """

    idp_entity_id: str = Field(
        description="Key reference for SAML Identity Provider entity ID"
    )
    sp_entity_id: str = Field(
        description="Key reference for SAML Service Provider entity ID"
    )
    sso_url: str = Field(description="Key reference for SAML Single Sign-On URL")
    certificate_path: Optional[str] = Field(
        None, description="Key reference for SAML certificate file path"
    )


class CLIAuthenticationModel(BaseModel):
    """
    CLI-based authentication using local credentials.

    Uses existing CLI authentication: az login, gcloud auth, aws configure.
    No credential references needed - uses ambient credentials.
    """

    use_cli: bool = Field(
        default=True,
        description="Use local CLI authentication (az, gcloud, aws)",
    )


class ManagedIdentityAuthenticationModel(BaseModel):
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
    client_id: Optional[str] = Field(
        None,
        description="Key reference for user-assigned managed identity client ID (Azure only)",
    )


class AuthenticationModel(BaseModel):
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
    oauth2: Optional[OAuth2AuthenticationModel] = Field(
        None, description="OAuth2/Service Principal authentication"
    )
    aws: Optional[AWSAuthenticationModel] = Field(
        None, description="AWS access key authentication"
    )
    gcp: Optional[GCPAuthenticationModel] = Field(
        None, description="GCP service account authentication"
    )
    api_key: Optional[APIKeyAuthenticationModel] = Field(
        None, description="API key authentication"
    )
    certificate: Optional[CertificateAuthenticationModel] = Field(
        None, description="Certificate-based authentication (mTLS)"
    )
    saml: Optional[SAMLAuthenticationModel] = Field(
        None, description="SAML 2.0 authentication"
    )
    cli: Optional[CLIAuthenticationModel] = Field(
        None, description="CLI-based authentication"
    )
    managed_identity: Optional[ManagedIdentityAuthenticationModel] = Field(
        None, description="Managed/Workload Identity authentication"
    )
