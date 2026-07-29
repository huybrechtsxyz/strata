#!/usr/bin/env python3
"""Store models for variables, secrets, and feature flags.

Store type enums map to integration types registered in IntegrationFactory.
"""

from enum import Enum
from typing import Annotated, Any, List, Optional

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from strata.models.common_models import check_unique_names


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
    - INFISICAL: "infisical" integration type
    - ETCD: "etcd" integration type
    """

    CONSTANT = "constant"
    ENVIRONMENT = "environment"
    AZURE_APPCONFIG = "azure-appconfig"
    HASHICORP_CONSUL = "consul"
    HASHICORP_VAULT = "vault"
    INFISICAL = "infisical"
    ETCD = "etcd"


# Enumeration of supported secret store types.
class SecretStoreType(str, Enum):
    """
    Enumeration of supported secret store backend types.

    Store types map to integration types registered in IntegrationFactory:
    - CONSTANT/ENVIRONMENT: Built-in resolvers (no integration required)
    - GITHUB: Built-in resolver — env vars injected by GitHub Actions runner (no integration required)
    - AZURE_KEYVAULT: "azure-keyvault" integration type
    - BITWARDEN: "bitwarden" integration type
    - HASHICORP_VAULT: "vault" integration type
    - INFISICAL: "infisical" integration type
    """

    CONSTANT = "constant"
    ENVIRONMENT = "environment"
    GITHUB = "github"
    AZURE_KEYVAULT = "azure-keyvault"
    BITWARDEN = "bitwarden"
    HASHICORP_VAULT = "vault"
    INFISICAL = "infisical"


# Enumeration of secret generator types (mirrors --format options in strata secret generate).
class SecretGenerateType(str, Enum):
    """Supported cryptographic secret generator types."""

    URLSAFE = "urlsafe"
    HEX = "hex"
    PASSWORD = "password"
    ALPHANUMERIC = "alphanumeric"
    NUMERIC = "numeric"
    BASE64 = "base64"
    UUID4 = "uuid4"
    UUID7 = "uuid7"


# Spec for auto-generating a secret value if the store key is missing.
class SecretGenerateSpec(BaseModel):
    """Spec for generating a cryptographically secure secret when the store key does not exist."""

    type: SecretGenerateType = Field(
        description="Generator type (urlsafe, hex, password, alphanumeric, numeric, base64, uuid4, uuid7)"
    )
    length: int = Field(
        default=32,
        description="Length in bytes (urlsafe/hex/base64) or characters (alphanumeric/password/numeric). Ignored for uuid4/uuid7.",
    )

    @field_validator("length")
    @classmethod
    def validate_length(cls, v: int) -> int:
        if v < 8:
            raise ValueError("length must be >= 8")
        if v > 1024:
            raise ValueError("length must be <= 1024")
        return v


# Rotation policy enum.
class SecretRotatePolicy(str, Enum):
    """Rotation policy: warn (advisory only) or rotate (auto-regenerate)."""

    WARN = "warn"
    ROTATE = "rotate"


# Spec for age-based secret rotation.
class SecretRotateSpec(BaseModel):
    """Rotation policy for a secret — advisory warning or automatic regeneration."""

    max_age: int = Field(
        description="Maximum secret age in days before the policy triggers. Must be >= 1.",
    )
    policy: SecretRotatePolicy = Field(
        default=SecretRotatePolicy.WARN,
        description="Rotation policy: 'warn' emits an advisory, 'rotate' auto-regenerates (requires generate: spec).",
    )

    @field_validator("max_age")
    @classmethod
    def validate_max_age(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_age must be >= 1 (days)")
        return v


# Enumeration of supported feature flag store types.
class FeatureStoreType(str, Enum):
    """
    Enumeration of supported feature flag store backend types.

    Store types map to integration types registered in IntegrationFactory:
    - CONSTANT/ENVIRONMENT: Built-in resolvers (no integration required)
    - AZURE_APPCONFIG: "azure-appconfig" integration type
    - FLAGSMITH: "flagsmith" integration type
    """

    CONSTANT = "constant"
    ENVIRONMENT = "environment"
    AZURE_APPCONFIG = "azure-appconfig"
    FLAGSMITH = "flagsmith"


def _validate_field_not_on_builtin(
    *,
    kind_noun: str,
    key: str,
    field_name: str,
    field_value: Any,
    store: Enum,
    builtin_types: frozenset,
    suggested_stores: str,
) -> None:
    """Raise if *field_value* is set while *store* is one of the built-in types.

    Shared by the `generate`/`rotate`/`default` "not valid on built-in store"
    validators across `SecretStoreModel`, `VariableStoreModel`, and
    `FeatureStoreModel` — same check, same message shape, only the model/field/
    builtin-set/suggested-stores text differ per call site.
    """
    if field_value is not None and store in builtin_types:
        raise ValueError(
            f"{kind_noun} '{key}': '{field_name}' is not valid on built-in store type '{store.value}'. "
            f"Use an integration-backed store ({suggested_stores})."
        )


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
        description="Secret store type: constant, environment, github, azure-keyvault, bitwarden, or vault"
    )
    value: Any = Field(
        description="Secret identifier: literal value for constant, env var name for environment, "
        "GitHub Actions secret name (env var name injected by the runner, e.g. MY_API_KEY) for github, "
        "secret path/ID for integration stores"
    )
    version: Optional[str] = Field(
        None,
        description="Optional version for store-based secrets (supported by some integrations)",
    )
    description: Optional[str] = Field(None, description="Optional description for documentation purposes")
    generate: Optional[SecretGenerateSpec] = Field(
        None,
        description="Auto-generate the secret when the store key is missing (integration-backed stores only)",
    )
    rotate: Optional[SecretRotateSpec] = Field(
        None,
        description="Rotation policy: age-based advisory or automatic regeneration (integration-backed stores only)",
    )

    @model_validator(mode="after")
    def validate_generate_not_on_builtin(self) -> "SecretStoreModel":
        _validate_field_not_on_builtin(
            kind_noun="Secret",
            key=self.key,
            field_name="generate",
            field_value=self.generate,
            store=self.store,
            builtin_types=frozenset({SecretStoreType.CONSTANT, SecretStoreType.ENVIRONMENT, SecretStoreType.GITHUB}),
            suggested_stores="azure-keyvault, vault, bitwarden, infisical",
        )
        return self

    @model_validator(mode="after")
    def validate_rotate_not_on_builtin(self) -> "SecretStoreModel":
        _validate_field_not_on_builtin(
            kind_noun="Secret",
            key=self.key,
            field_name="rotate",
            field_value=self.rotate,
            store=self.store,
            builtin_types=frozenset({SecretStoreType.CONSTANT, SecretStoreType.ENVIRONMENT, SecretStoreType.GITHUB}),
            suggested_stores="azure-keyvault, vault, bitwarden, infisical",
        )
        return self

    @model_validator(mode="after")
    def validate_rotate_policy_requires_generate(self) -> "SecretStoreModel":
        if self.rotate is not None and self.rotate.policy == SecretRotatePolicy.ROTATE and self.generate is None:
            raise ValueError(
                f"Secret '{self.key}': rotate policy 'rotate' requires a 'generate' spec — "
                "strata cannot auto-regenerate a manually-placed secret. Use policy 'warn' instead."
            )
        return self

    @model_validator(mode="after")
    def validate_version_not_set_for_github(self) -> "SecretStoreModel":
        if self.store == SecretStoreType.GITHUB and self.version is not None:
            raise ValueError(
                f"Secret '{self.key}': 'version' is not supported for store type 'github'. "
                f"GitHub Secrets are not versioned."
            )
        return self


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
    description: Optional[str] = Field(None, description="Optional description for documentation purposes")
    default: Optional[str] = Field(
        None,
        description="Seed the store with this value when the key is missing (integration-backed stores only)",
    )

    @model_validator(mode="after")
    def validate_default_not_on_builtin(self) -> "VariableStoreModel":
        _validate_field_not_on_builtin(
            kind_noun="Variable",
            key=self.key,
            field_name="default",
            field_value=self.default,
            store=self.store,
            builtin_types=frozenset({VariableStoreType.CONSTANT, VariableStoreType.ENVIRONMENT}),
            suggested_stores="azure-appconfig, consul, vault, infisical, etcd",
        )
        return self


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
    store: FeatureStoreType = Field(description="Feature store type: constant, environment, or azure-appconfig")
    value: Any = Field(
        description="Feature flag identifier: literal value for constant, env var name for environment, flag name/key for integration stores"
    )
    version: Optional[str] = Field(
        None,
        description="Optional version for store-based feature flags (supported by some integrations)",
    )
    description: Optional[str] = Field(None, description="Optional description for documentation purposes")
    default: Optional[str] = Field(
        None,
        description="Seed the store with this state when the flag is missing. Use 'true' or 'false' (integration-backed stores only)",
    )

    @model_validator(mode="after")
    def validate_default_not_on_builtin(self) -> "FeatureStoreModel":
        _validate_field_not_on_builtin(
            kind_noun="Feature",
            key=self.key,
            field_name="default",
            field_value=self.default,
            store=self.store,
            builtin_types=frozenset({FeatureStoreType.CONSTANT, FeatureStoreType.ENVIRONMENT}),
            suggested_stores="azure-appconfig, flagsmith",
        )
        return self


# Validation helper functions for variables, secrets, and features
def validate_unique_variable_keys(
    variables: Optional[List[VariableStoreModel]],
) -> None:
    """Validate that all variable keys are unique."""
    if variables:
        check_unique_names([var.key for var in variables], "variable keys")


def validate_unique_secret_keys(secrets: Optional[List[SecretStoreModel]]) -> None:
    """Validate that all secret keys are unique."""
    if secrets:
        check_unique_names([secret.key for secret in secrets], "secret keys")


def validate_unique_feature_keys(features: Optional[List[FeatureStoreModel]]) -> None:
    """Validate that all feature keys are unique."""
    if features:
        check_unique_names([feature.key for feature in features], "feature keys")


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
