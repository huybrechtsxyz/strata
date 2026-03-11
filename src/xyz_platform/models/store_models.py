#!/usr/bin/env python3
"""
===============================================================================
Script Name   : store_models.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Store-related models and validation functions for XYZ Platform.
                Includes models for variable stores, secret stores, and feature
                flag stores, along with validation utilities.

                Store type enums use integration type strings that map to
                registered integrations in the IntegrationFactory.
===============================================================================
"""

from enum import Enum
from typing import Annotated, Any, List, Optional
from pydantic import BaseModel, Field, StringConstraints


# Enumeration of store backend source categories.
class StoreBackendSource(str, Enum):
    """Enumeration of store backend source categories."""

    VARIABLES = "variables"
    SECRETS = "secrets"
    FEATURES = "features"


# Enumeration of supported variable store types.
class VariableStoreType(str, Enum):
    """
    Enumeration of supported variable store backend types.

    Store types map to integration types registered in IntegrationFactory:
    - CONSTANT/ENVIRONMENT: Built-in resolvers (no integration required)
    - AZURE_APPCONFIG: "azure-appconfig" integration type
    - HASHICORP_CONSUL: "consul" integration type
    - HASHICORP_VAULT: "vault" integration type
    """

    CONSTANT = "constant"
    ENVIRONMENT = "environment"
    AZURE_APPCONFIG = "azure-appconfig"
    HASHICORP_CONSUL = "consul"
    HASHICORP_VAULT = "vault"


# Enumeration of supported secret store types.
class SecretStoreType(str, Enum):
    """
    Enumeration of supported secret store backend types.

    Store types map to integration types registered in IntegrationFactory:
    - CONSTANT/ENVIRONMENT: Built-in resolvers (no integration required)
    - AZURE_KEYVAULT: "azure-keyvault" integration type
    - BITWARDEN: "bitwarden" integration type
    - HASHICORP_VAULT: "vault" integration type
    """

    CONSTANT = "constant"
    ENVIRONMENT = "environment"
    AZURE_KEYVAULT = "azure-keyvault"
    BITWARDEN = "bitwarden"
    HASHICORP_VAULT = "vault"


# Enumeration of supported feature flag store types.
class FeatureStoreType(str, Enum):
    """
    Enumeration of supported feature flag store backend types.

    Store types map to integration types registered in IntegrationFactory:
    - CONSTANT/ENVIRONMENT: Built-in resolvers (no integration required)
    - AZURE_APPCONFIG: "azure-appconfig" integration type

    Can be expanded to include more feature flag services:
    - LaunchDarkly, Split, Unleash, etc.
    """

    CONSTANT = "constant"
    ENVIRONMENT = "environment"
    AZURE_APPCONFIG = "azure-appconfig"


# Model for secret definitions.
class SecretStoreModel(BaseModel):
    """
    Model for secret definitions.

    Secrets can be sourced from various backends using registered integrations.
    The 'store' field specifies the integration type, and the 'value' field
    specifies the secret identifier within that store.
    """

    key: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description="Secret key name for referencing in configurations"
    )
    store: SecretStoreType = Field(
        description="Secret store type: constant, environment, azure-keyvault, bitwarden, or vault"
    )
    value: Any = Field(
        description="Secret identifier: literal value for constant, env var name for environment, secret path/ID for integration stores"
    )
    version: Optional[str] = Field(
        None,
        description="Optional version for store-based secrets (supported by some integrations)",
    )
    description: Optional[str] = Field(
        None, description="Optional description for documentation purposes"
    )


# Model for variable definitions.
class VariableStoreModel(BaseModel):
    """
    Model for variable definitions.

    Variables can be sourced from various backends using registered integrations.
    The 'store' field specifies the integration type, and the 'value' field
    specifies the variable identifier within that store.
    """

    key: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description="Variable key name for referencing in configurations"
    )
    store: VariableStoreType = Field(
        description="Variable store type: constant, environment, azure-appconfig, consul, or vault"
    )
    value: Any = Field(
        description="Variable identifier: literal value for constant, env var name for environment, config path/key for integration stores"
    )
    version: Optional[str] = Field(
        None,
        description="Optional version for store-based variables (supported by some integrations)",
    )
    description: Optional[str] = Field(
        None, description="Optional description for documentation purposes"
    )


# Model for feature flag definitions.
class FeatureStoreModel(BaseModel):
    """
    Model for feature flag definitions.

    Feature flags can be sourced from various backends using registered integrations.
    The 'store' field specifies the integration type, and the 'value' field
    specifies the feature flag identifier within that store.
    """

    key: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description="Feature flag key name for referencing in configurations"
    )
    store: FeatureStoreType = Field(
        description="Feature store type: constant, environment, or azure-appconfig"
    )
    value: Any = Field(
        description="Feature flag identifier: literal value for constant, env var name for environment, flag name/key for integration stores"
    )
    version: Optional[str] = Field(
        None,
        description="Optional version for store-based feature flags (supported by some integrations)",
    )
    description: Optional[str] = Field(
        None, description="Optional description for documentation purposes"
    )


# Validation helper functions for variables, secrets, and features
def validate_unique_variable_keys(
    variables: Optional[List[VariableStoreModel]],
) -> None:
    """
    Validate that all variable keys are unique.

    Args:
        variables: List of VariableStoreModel instances to validate

    Raises:
        ValueError: If duplicate variable keys are found
    """
    if variables:
        var_keys = [var.key for var in variables]
        if len(var_keys) != len(set(var_keys)):
            duplicates = [key for key in var_keys if var_keys.count(key) > 1]
            raise ValueError(f"Duplicate variable keys found: {set(duplicates)}")


def validate_unique_secret_keys(secrets: Optional[List[SecretStoreModel]]) -> None:
    """
    Validate that all secret keys are unique.

    Args:
        secrets: List of SecretStoreModel instances to validate

    Raises:
        ValueError: If duplicate secret keys are found
    """
    if secrets:
        secret_keys = [secret.key for secret in secrets]
        if len(secret_keys) != len(set(secret_keys)):
            duplicates = [key for key in secret_keys if secret_keys.count(key) > 1]
            raise ValueError(f"Duplicate secret keys found: {set(duplicates)}")


def validate_unique_feature_keys(features: Optional[List[FeatureStoreModel]]) -> None:
    """
    Validate that all feature keys are unique.

    Args:
        features: List of FeatureStoreModel instances to validate

    Raises:
        ValueError: If duplicate feature keys are found
    """
    if features:
        feature_keys = [feature.key for feature in features]
        if len(feature_keys) != len(set(feature_keys)):
            duplicates = [key for key in feature_keys if feature_keys.count(key) > 1]
            raise ValueError(f"Duplicate feature keys found: {set(duplicates)}")


def validate_store_security_policy(
    variables: Optional[List[VariableStoreModel]],
    secrets: Optional[List[SecretStoreModel]],
    features: Optional[List[FeatureStoreModel]],
    allowed_variable_stores: Optional[List[VariableStoreType]],
    allowed_secret_stores: Optional[List[SecretStoreType]],
    allowed_feature_stores: Optional[List[FeatureStoreType]],
) -> List[str]:
    """
    Validate variables, secrets, and features against security policy.

    Args:
        variables: List of variables to validate
        secrets: List of secrets to validate
        features: List of features to validate
        allowed_variable_stores: Allowed variable store types (None = all allowed)
        allowed_secret_stores: Allowed secret store types (None = all allowed)
        allowed_feature_stores: Allowed feature store types (None = all allowed)

    Returns:
        List of error messages (empty if all valid)
    """
    errors = []

    # Validate secret store types
    if allowed_secret_stores and secrets:
        for secret in secrets:
            if secret.store not in allowed_secret_stores:
                allowed = ", ".join([s.value for s in allowed_secret_stores])
                errors.append(
                    f"Secret '{secret.key}' uses disallowed store type '{secret.store.value}'. "
                    f"Allowed stores: {allowed}"
                )

    # Validate variable store types
    if allowed_variable_stores and variables:
        for variable in variables:
            if variable.store not in allowed_variable_stores:
                allowed = ", ".join([s.value for s in allowed_variable_stores])
                errors.append(
                    f"Variable '{variable.key}' uses disallowed store type '{variable.store.value}'. "
                    f"Allowed stores: {allowed}"
                )

    # Validate feature store types
    if allowed_feature_stores and features:
        for feature in features:
            if feature.store not in allowed_feature_stores:
                allowed = ", ".join([s.value for s in allowed_feature_stores])
                errors.append(
                    f"Feature '{feature.key}' uses disallowed store type '{feature.store.value}'. "
                    f"Allowed stores: {allowed}"
                )

    return errors
