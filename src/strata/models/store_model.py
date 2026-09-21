#!/usr/bin/env python3
"""Store models for variables, secrets, and feature flags (Environment building block).

`FeatureStoreModel`/`VariableStoreModel`/`SecretStoreModel` share one shape
(`key`/`store`/`value`/`version`/`description` + a "not valid on a built-in
store" gate on any integration-only field) plus their own extras: `Variable`
adds `type` (declared HCL type + value-consistency check), `Secret` adds
`generate`/`rotate`. Ported from v1's real `store_models.py`.

`store` values map to integration types in v1 (`IntegrationFactory`) — that
mapping is not modeled here, since v2 doesn't have an `Integration` kind yet.
`constant`/`environment` (+ `github` for secrets) are the only *built-in*
resolvers (need no integration); every other store type is a placeholder
reference until `Integration` exists to give it real meaning (same
discipline as `Provisioner.tool`/`.integration`, ADR-0011).
"""

from enum import Enum
from typing import Any

from pydantic import Field, model_validator

from strata.models.common_models import PlatformBaseModel, VariableKey

#: Store types resolvable without any integration — a literal value
#: (`constant`) or an environment variable read directly (`environment`).
BUILTIN_FEATURE_STORE_TYPES = frozenset({"constant", "environment"})
BUILTIN_VARIABLE_STORE_TYPES = frozenset({"constant", "environment"})
#: Secrets additionally treat `github` as built-in — env vars injected by the
#: GitHub Actions runner, resolved the same way as `environment`, no
#: integration lookup involved.
BUILTIN_SECRET_STORE_TYPES = frozenset({"constant", "environment", "github"})


def _validate_field_not_on_builtin(
    *, kind_noun: str, key: str, field_name: str, field_value: Any, store_value: str, builtin_types: frozenset[str]
) -> None:
    """Raise if *field_value* is set while *store_value* is one of *builtin_types*.

    Shared by `FeatureStoreModel`/`VariableStoreModel`/`SecretStoreModel`'s
    `default`/`generate`/`rotate` validators — same check, same message
    shape, only the model/field/builtin-set differ per call site (matches
    v1's shared `_validate_field_not_on_builtin`).
    """
    if field_value is not None and store_value in builtin_types:
        raise ValueError(
            f"{kind_noun} '{key}': '{field_name}' is not valid on built-in store type '{store_value}'. "
            "Use an integration-backed store."
        )


class FeatureStoreType(str, Enum):
    """Feature flag store backend type.

    `CONSTANT`/`ENVIRONMENT` are built-in resolvers (no integration needed).
    `AZURE_APPCONFIG`/`FLAGSMITH` are integration-backed placeholders — kept
    as recognized values (matching v1's real vocabulary) but not yet
    resolvable to anything, since no `Integration` kind exists in v2 yet.
    """

    CONSTANT = "constant"
    ENVIRONMENT = "environment"
    AZURE_APPCONFIG = "azure-appconfig"
    FLAGSMITH = "flagsmith"


class FeatureStoreModel(PlatformBaseModel):
    """A single feature flag definition.

    `value` is the flag's identifier: a literal for `constant`, an
    environment variable name for `environment`, or a flag name/key for an
    integration-backed store. `default` seeds the store with a value when
    the key is missing — only meaningful for integration-backed stores,
    since `constant`/`environment` have no store to seed (v1 precedent).
    """

    key: VariableKey = Field(description="Feature flag key name for referencing in configurations")
    store: FeatureStoreType = Field(description="Feature store type: constant, environment, azure-appconfig, or flagsmith")
    value: Any = Field(
        description="Feature flag identifier: literal for constant, env var name for environment, "
        "flag name/key for integration-backed stores"
    )
    version: str | None = Field(None, description="Optional version for store-based feature flags")
    description: str | None = Field(None, description="Optional description for documentation purposes")
    default: str | None = Field(
        None,
        description="Seed the store with this state ('true'/'false') when the flag is missing "
        "(integration-backed stores only)",
    )

    @model_validator(mode="after")
    def validate_default_not_on_builtin(self) -> "FeatureStoreModel":
        """`default` only makes sense for an integration-backed store — there is no store to seed
        for `constant`/`environment`."""
        _validate_field_not_on_builtin(
            kind_noun="Feature",
            key=self.key,
            field_name="default",
            field_value=self.default,
            store_value=self.store.value,
            builtin_types=BUILTIN_FEATURE_STORE_TYPES,
        )
        return self


class VariableValueType(str, Enum):
    """Declared HCL type for Terraform variable emission.

    When set on a `VariableStoreModel`, controls how the value is serialized
    for provisioner consumption (e.g. `.auto.tfvars.json`): `STRING` (default
    when omitted, backward compatible), `NUMBER`, `BOOL`, `OBJECT`, `LIST`,
    `MAP`.
    """

    STRING = "string"
    NUMBER = "number"
    BOOL = "bool"
    OBJECT = "object"
    LIST = "list"
    MAP = "map"


class VariableStoreType(str, Enum):
    """Variable store backend type.

    `CONSTANT`/`ENVIRONMENT` are built-in resolvers (no integration needed).
    The rest are integration-backed placeholders (see module docstring).
    """

    CONSTANT = "constant"
    ENVIRONMENT = "environment"
    AZURE_APPCONFIG = "azure-appconfig"
    HASHICORP_CONSUL = "consul"
    HASHICORP_VAULT = "vault"
    INFISICAL = "infisical"
    ETCD = "etcd"


class VariableStoreModel(PlatformBaseModel):
    """A single variable definition.

    `value` is the variable's identifier: a literal for `constant`, an
    environment variable name for `environment`, or a config path/key for an
    integration-backed store. `type` only affects emission for `constant`
    values today — other store types resolve their actual value at deploy
    time, so there is nothing yet to check the declared type against.
    """

    key: VariableKey = Field(description="Variable key name for referencing in configurations")
    store: VariableStoreType = Field(
        description="Variable store type: constant, environment, azure-appconfig, consul, vault, infisical, or etcd"
    )
    value: Any = Field(
        description="Variable identifier: literal for constant, env var name for environment, "
        "config path/key for integration-backed stores"
    )
    type: VariableValueType | None = Field(
        None,
        description="Declared HCL type for Terraform emission. Omitted means emitted as a string "
        "(backward compatible).",
    )
    version: str | None = Field(None, description="Optional version for store-based variables")
    description: str | None = Field(None, description="Optional description for documentation purposes")
    default: str | None = Field(
        None, description="Seed the store with this value when the key is missing (integration-backed stores only)"
    )

    @model_validator(mode="after")
    def validate_type_value_consistency(self) -> "VariableStoreModel":
        """When `type` is set on a `constant` store, `value` must match it.

        Only checked for `constant` — every other store type resolves its
        real value at deploy time, so there's nothing here yet to compare
        against.
        """
        if self.type is None or self.store != VariableStoreType.CONSTANT:
            return self
        if self.type == VariableValueType.OBJECT and not isinstance(self.value, dict):
            raise ValueError(f"Variable '{self.key}': type=object requires a mapping value")
        if self.type == VariableValueType.LIST and not isinstance(self.value, list):
            raise ValueError(f"Variable '{self.key}': type=list requires a sequence value")
        if self.type == VariableValueType.MAP and not isinstance(self.value, dict):
            raise ValueError(f"Variable '{self.key}': type=map requires a mapping value")
        if self.type == VariableValueType.NUMBER and not isinstance(self.value, int | float):
            raise ValueError(f"Variable '{self.key}': type=number requires a numeric value")
        if self.type == VariableValueType.BOOL and not isinstance(self.value, bool):
            raise ValueError(f"Variable '{self.key}': type=bool requires a boolean value")
        return self

    @model_validator(mode="after")
    def validate_default_not_on_builtin(self) -> "VariableStoreModel":
        """`default` only makes sense for an integration-backed store."""
        _validate_field_not_on_builtin(
            kind_noun="Variable",
            key=self.key,
            field_name="default",
            field_value=self.default,
            store_value=self.store.value,
            builtin_types=BUILTIN_VARIABLE_STORE_TYPES,
        )
        return self


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


class SecretGenerateSpec(PlatformBaseModel):
    """Spec for generating a cryptographically secure secret when the store key does not exist."""

    type: SecretGenerateType = Field(
        description="Generator type (urlsafe, hex, password, alphanumeric, numeric, base64, uuid4, uuid7)"
    )
    length: int = Field(
        default=32,
        ge=8,
        le=1024,
        description="Length in bytes (urlsafe/hex/base64) or characters (alphanumeric/password/numeric). "
        "Ignored for uuid4/uuid7.",
    )


class SecretRotatePolicy(str, Enum):
    """Rotation policy: warn (advisory only) or rotate (auto-regenerate)."""

    WARN = "warn"
    ROTATE = "rotate"


class SecretRotateSpec(PlatformBaseModel):
    """Rotation policy for a secret — advisory warning or automatic regeneration."""

    max_age: int = Field(ge=1, description="Maximum secret age in days before the policy triggers")
    policy: SecretRotatePolicy = Field(
        default=SecretRotatePolicy.WARN,
        description="'warn' emits an advisory, 'rotate' auto-regenerates (requires a 'generate' spec)",
    )


class SecretStoreType(str, Enum):
    """Secret store backend type.

    `CONSTANT`/`ENVIRONMENT`/`GITHUB` are built-in resolvers (no integration
    needed). The rest are integration-backed placeholders (see module
    docstring).
    """

    CONSTANT = "constant"
    ENVIRONMENT = "environment"
    GITHUB = "github"
    AZURE_KEYVAULT = "azure-keyvault"
    BITWARDEN = "bitwarden"
    HASHICORP_VAULT = "vault"
    INFISICAL = "infisical"


class SecretStoreModel(PlatformBaseModel):
    """A single secret definition.

    `value` is the secret's identifier: a literal for `constant`, an
    environment variable name for `environment`, a GitHub Actions secret
    name (env var injected by the runner) for `github`, or a secret path/ID
    for an integration-backed store.
    """

    key: VariableKey = Field(description="Secret key name for referencing in configurations")
    store: SecretStoreType = Field(
        description="Secret store type: constant, environment, github, azure-keyvault, bitwarden, vault, or infisical"
    )
    value: Any = Field(
        description="Secret identifier: literal for constant, env var name for environment, GitHub Actions "
        "secret name for github, secret path/ID for integration-backed stores"
    )
    version: str | None = Field(None, description="Optional version for store-based secrets")
    description: str | None = Field(None, description="Optional description for documentation purposes")
    generate: SecretGenerateSpec | None = Field(
        None, description="Auto-generate the secret when the store key is missing (integration-backed stores only)"
    )
    rotate: SecretRotateSpec | None = Field(
        None,
        description="Rotation policy: age-based advisory or automatic regeneration (integration-backed stores only)",
    )

    @model_validator(mode="after")
    def validate_generate_not_on_builtin(self) -> "SecretStoreModel":
        """`generate` only makes sense for an integration-backed store."""
        _validate_field_not_on_builtin(
            kind_noun="Secret",
            key=self.key,
            field_name="generate",
            field_value=self.generate,
            store_value=self.store.value,
            builtin_types=BUILTIN_SECRET_STORE_TYPES,
        )
        return self

    @model_validator(mode="after")
    def validate_rotate_not_on_builtin(self) -> "SecretStoreModel":
        """`rotate` only makes sense for an integration-backed store."""
        _validate_field_not_on_builtin(
            kind_noun="Secret",
            key=self.key,
            field_name="rotate",
            field_value=self.rotate,
            store_value=self.store.value,
            builtin_types=BUILTIN_SECRET_STORE_TYPES,
        )
        return self

    @model_validator(mode="after")
    def validate_rotate_policy_requires_generate(self) -> "SecretStoreModel":
        """A 'rotate' policy of 'rotate' needs a 'generate' spec — strata cannot auto-regenerate
        a manually-placed secret."""
        if self.rotate is not None and self.rotate.policy == SecretRotatePolicy.ROTATE and self.generate is None:
            raise ValueError(
                f"Secret '{self.key}': rotate policy 'rotate' requires a 'generate' spec — "
                "strata cannot auto-regenerate a manually-placed secret. Use policy 'warn' instead."
            )
        return self

    @model_validator(mode="after")
    def validate_version_not_set_for_github(self) -> "SecretStoreModel":
        """GitHub Secrets are not versioned."""
        if self.store == SecretStoreType.GITHUB and self.version is not None:
            raise ValueError(f"Secret '{self.key}': 'version' is not supported for store type 'github'.")
        return self
