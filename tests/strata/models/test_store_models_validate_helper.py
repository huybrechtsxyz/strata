"""Unit tests for the shared `_validate_field_not_on_builtin` helper in store_models.py.

Extracted from 4 near-identical validators (SecretStoreModel.generate/rotate,
VariableStoreModel.default, FeatureStoreModel.default) — see _lesson.md I2 /
_todo.md. The public model-level behavior is already covered by
test_store_models.py and test_secret_rotation.py; this file locks in the
helper's own contract directly.
"""

import pytest

from strata.models.store_models import (
    FeatureStoreType,
    SecretStoreType,
    VariableStoreType,
    _validate_field_not_on_builtin,
)


class TestValidateFieldNotOnBuiltin:
    def test_no_error_when_field_value_is_none(self):
        _validate_field_not_on_builtin(
            kind_noun="Secret",
            key="k",
            field_name="generate",
            field_value=None,
            store=SecretStoreType.CONSTANT,
            builtin_types=frozenset({SecretStoreType.CONSTANT}),
            suggested_stores="vault",
        )  # must not raise

    def test_no_error_when_store_is_not_builtin(self):
        _validate_field_not_on_builtin(
            kind_noun="Secret",
            key="k",
            field_name="generate",
            field_value="something",
            store=SecretStoreType.AZURE_KEYVAULT,
            builtin_types=frozenset({SecretStoreType.CONSTANT, SecretStoreType.ENVIRONMENT}),
            suggested_stores="vault",
        )  # must not raise

    def test_raises_with_exact_message_shape(self):
        with pytest.raises(ValueError) as exc_info:
            _validate_field_not_on_builtin(
                kind_noun="Variable",
                key="my_key",
                field_name="default",
                field_value="x",
                store=VariableStoreType.CONSTANT,
                builtin_types=frozenset({VariableStoreType.CONSTANT}),
                suggested_stores="azure-appconfig, consul",
            )
        assert str(exc_info.value) == (
            "Variable 'my_key': 'default' is not valid on built-in store type 'constant'. "
            "Use an integration-backed store (azure-appconfig, consul)."
        )

    def test_raises_for_feature_kind_noun(self):
        with pytest.raises(ValueError) as exc_info:
            _validate_field_not_on_builtin(
                kind_noun="Feature",
                key="beta",
                field_name="default",
                field_value="true",
                store=FeatureStoreType.ENVIRONMENT,
                builtin_types=frozenset({FeatureStoreType.CONSTANT, FeatureStoreType.ENVIRONMENT}),
                suggested_stores="azure-appconfig, flagsmith",
            )
        assert "Feature 'beta'" in str(exc_info.value)
        assert "'environment'" in str(exc_info.value)
