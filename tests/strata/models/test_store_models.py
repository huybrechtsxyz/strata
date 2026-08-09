"""Tests for SecretStoreModel and SecretStoreType — github store behavior."""

import pytest
from pydantic import ValidationError

from strata.models.store_models import (
    FeatureStoreModel,
    SecretGenerateSpec,
    SecretGenerateType,
    SecretStoreModel,
    SecretStoreType,
    VariableStoreModel,
)


class TestSecretStoreTypeGithub:
    def test_github_is_valid_enum_value(self):
        """store: github is a recognized SecretStoreType value."""
        assert SecretStoreType("github") == SecretStoreType.GITHUB

    def test_github_model_validates_without_error(self):
        """SecretStoreModel with store='github' constructs without raising."""
        model = SecretStoreModel(key="api_key", store="github", value="MY_API_KEY")
        assert model.store == SecretStoreType.GITHUB

    def test_github_with_version_raises_validation_error(self):
        """version is not allowed for store='github'; model_validator raises ValueError."""
        with pytest.raises(ValidationError) as exc_info:
            SecretStoreModel(key="api_key", store="github", value="MY_API_KEY", version="1")
        assert "not supported" in str(exc_info.value)

    def test_github_serializes_as_github_not_environment(self):
        """model_dump() preserves 'github', never coerced to 'environment'."""
        model = SecretStoreModel(key="api_key", store="github", value="MY_API_KEY")
        dumped = model.model_dump()
        assert dumped["store"] == "github"


# ---------------------------------------------------------------------------
# SecretGenerateSpec validation
# ---------------------------------------------------------------------------


class TestSecretGenerateSpec:
    def test_valid_spec_constructs(self):
        spec = SecretGenerateSpec(type="password", length=32)
        assert spec.type == SecretGenerateType.PASSWORD
        assert spec.length == 32

    def test_default_length_is_32(self):
        spec = SecretGenerateSpec(type="urlsafe")
        assert spec.length == 32

    def test_length_too_small_raises(self):
        with pytest.raises(ValidationError):
            SecretGenerateSpec(type="hex", length=4)

    def test_length_too_large_raises(self):
        with pytest.raises(ValidationError):
            SecretGenerateSpec(type="hex", length=2048)

    def test_unknown_type_raises(self):
        with pytest.raises(ValidationError):
            SecretGenerateSpec(type="rsa-key", length=32)

    def test_all_types_accepted(self):
        for t in ["urlsafe", "hex", "password", "alphanumeric", "numeric", "base64", "uuid4", "uuid7"]:
            spec = SecretGenerateSpec(type=t, length=16)
            assert spec.type.value == t


# ---------------------------------------------------------------------------
# SecretStoreModel.generate field
# ---------------------------------------------------------------------------


class TestSecretStoreModelGenerate:
    def test_generate_on_keyvault_is_valid(self):
        m = SecretStoreModel(
            key="DB_PASSWORD",
            store="azure-keyvault",
            value="myapp-db-password",
            generate={"type": "password", "length": 32},
        )
        assert m.generate is not None
        assert m.generate.type == SecretGenerateType.PASSWORD

    def test_generate_on_constant_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            SecretStoreModel(key="k", store="constant", value="v", generate={"type": "hex", "length": 16})
        assert "built-in store type" in str(exc_info.value)

    def test_generate_on_environment_raises(self):
        with pytest.raises(ValidationError):
            SecretStoreModel(key="k", store="environment", value="MY_VAR", generate={"type": "uuid4", "length": 16})

    def test_generate_on_github_raises(self):
        with pytest.raises(ValidationError):
            SecretStoreModel(key="k", store="github", value="MY_VAR", generate={"type": "urlsafe", "length": 16})

    def test_generate_none_by_default(self):
        m = SecretStoreModel(key="k", store="constant", value="v")
        assert m.generate is None


# ---------------------------------------------------------------------------
# VariableStoreModel.default field
# ---------------------------------------------------------------------------


class TestVariableStoreModelDefault:
    def test_default_on_appconfig_is_valid(self):
        m = VariableStoreModel(key="LOG_LEVEL", store="azure-appconfig", value="myapp/log-level", default="info")
        assert m.default == "info"

    def test_default_on_constant_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            VariableStoreModel(key="k", store="constant", value="v", default="x")
        assert "built-in store type" in str(exc_info.value)

    def test_default_on_environment_raises(self):
        with pytest.raises(ValidationError):
            VariableStoreModel(key="k", store="environment", value="MY_VAR", default="x")

    def test_default_none_by_default(self):
        m = VariableStoreModel(key="k", store="constant", value="v")
        assert m.default is None

    def test_default_int_coerced_to_str(self):
        m = VariableStoreModel(key="k", store="azure-appconfig", value="myapp/k", default="3")
        assert m.default == "3"


# ---------------------------------------------------------------------------
# FeatureStoreModel.default field
# ---------------------------------------------------------------------------


class TestFeatureStoreModelDefault:
    def test_default_on_appconfig_is_valid(self):
        m = FeatureStoreModel(key="DARK_MODE", store="azure-appconfig", value="myapp-dark-mode", default="false")
        assert m.default == "false"

    def test_default_on_flagsmith_is_valid(self):
        m = FeatureStoreModel(key="BETA", store="flagsmith", value="myapp-beta", default="true")
        assert m.default == "true"

    def test_default_on_constant_raises(self):
        with pytest.raises(ValidationError):
            FeatureStoreModel(key="k", store="constant", value=True, default="true")

    def test_default_on_environment_raises(self):
        with pytest.raises(ValidationError):
            FeatureStoreModel(key="k", store="environment", value="MY_FLAG", default="false")

    def test_default_none_by_default(self):
        m = FeatureStoreModel(key="k", store="constant", value=True)
        assert m.default is None


# ---------------------------------------------------------------------------
# VariableStoreModel.type field — structured value types
# ---------------------------------------------------------------------------


class TestVariableStoreModelType:
    """Tests for the VariableValueType field on VariableStoreModel."""

    def test_type_none_by_default(self):
        m = VariableStoreModel(key="k", store="constant", value="hello")
        assert m.type is None

    def test_type_string_valid(self):
        m = VariableStoreModel(key="k", store="constant", value="hello", type="string")
        assert m.type.value == "string"

    def test_type_number_with_int(self):
        m = VariableStoreModel(key="k", store="constant", value=42, type="number")
        assert m.type.value == "number"
        assert m.value == 42

    def test_type_number_with_float(self):
        m = VariableStoreModel(key="k", store="constant", value=3.14, type="number")
        assert m.value == 3.14

    def test_type_bool_with_true(self):
        m = VariableStoreModel(key="k", store="constant", value=True, type="bool")
        assert m.value is True

    def test_type_bool_with_false(self):
        m = VariableStoreModel(key="k", store="constant", value=False, type="bool")
        assert m.value is False

    def test_type_object_with_dict(self):
        val = {"worker_pools": {"default": {"size": "Standard_D4s_v3"}}}
        m = VariableStoreModel(key="aks_config", store="constant", value=val, type="object")
        assert m.value == val

    def test_type_list_with_list(self):
        val = ["10.0.0.0/8", "172.16.0.0/12"]
        m = VariableStoreModel(key="allowed_ips", store="constant", value=val, type="list")
        assert m.value == val

    def test_type_map_with_dict(self):
        val = {"env": "production", "team": "platform"}
        m = VariableStoreModel(key="tags", store="constant", value=val, type="map")
        assert m.value == val

    # --- Validation errors: type-value mismatch ---

    def test_type_object_with_string_raises(self):
        with pytest.raises(ValidationError, match="type=object requires a mapping value"):
            VariableStoreModel(key="k", store="constant", value="not a dict", type="object")

    def test_type_list_with_string_raises(self):
        with pytest.raises(ValidationError, match="type=list requires a sequence value"):
            VariableStoreModel(key="k", store="constant", value="not a list", type="list")

    def test_type_map_with_list_raises(self):
        with pytest.raises(ValidationError, match="type=map requires a mapping value"):
            VariableStoreModel(key="k", store="constant", value=["a", "b"], type="map")

    def test_type_number_with_string_raises(self):
        with pytest.raises(ValidationError, match="type=number requires a numeric value"):
            VariableStoreModel(key="k", store="constant", value="not a number", type="number")

    def test_type_bool_with_string_raises(self):
        with pytest.raises(ValidationError, match="type=bool requires a boolean value"):
            VariableStoreModel(key="k", store="constant", value="true", type="bool")

    def test_type_bool_with_int_raises(self):
        """int 1 is not bool — must be actual True/False."""
        with pytest.raises(ValidationError, match="type=bool requires a boolean value"):
            VariableStoreModel(key="k", store="constant", value=1, type="bool")

    # --- Non-constant stores: type is accepted without value validation ---

    def test_type_on_vault_store_no_validation(self):
        """Type on non-constant store is accepted (resolved at deploy-time)."""
        m = VariableStoreModel(key="k", store="vault", value="secret/data/k", type="object")
        assert m.type.value == "object"

    def test_type_on_appconfig_store_no_validation(self):
        m = VariableStoreModel(key="k", store="azure-appconfig", value="myapp/k", type="number")
        assert m.type.value == "number"

    # --- Invalid type value ---

    def test_invalid_type_raises(self):
        with pytest.raises(ValidationError):
            VariableStoreModel(key="k", store="constant", value="v", type="invalid_type")
