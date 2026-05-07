"""Tests for ValueController, ResolvedValues, and inject_tf_vars."""

import os
from unittest.mock import MagicMock

from xyz_platform.controllers.value_controller import (
    ResolvedValues,
    ValueController,
    inject_tf_vars,
)
from xyz_platform.models.store_models import (
    FeatureStoreModel,
    FeatureStoreType,
    SecretStoreModel,
    SecretStoreType,
    VariableStoreModel,
    VariableStoreType,
)

# ---------------------------------------------------------------------------
# ResolvedValues
# ---------------------------------------------------------------------------


class TestResolvedValues:
    def test_is_empty_true_when_all_dicts_empty(self):
        rv = ResolvedValues()
        assert rv.is_empty() is True

    def test_is_empty_false_with_variable(self):
        rv = ResolvedValues(variables={"k": "v"})
        assert rv.is_empty() is False

    def test_is_empty_false_with_secret(self):
        rv = ResolvedValues(secrets={"s": "x"})
        assert rv.is_empty() is False

    def test_is_empty_false_with_feature(self):
        rv = ResolvedValues(features={"f": True})
        assert rv.is_empty() is False

    def test_as_tf_vars_variables_prefixed(self):
        rv = ResolvedValues(variables={"region": "us-east-1"})
        tf = rv.as_tf_vars()
        assert tf["TF_VAR_region"] == "us-east-1"

    def test_as_tf_vars_secrets_prefixed(self):
        rv = ResolvedValues(secrets={"db_pass": "secret123"})
        tf = rv.as_tf_vars()
        assert tf["TF_VAR_db_pass"] == "secret123"

    def test_as_tf_vars_features_as_lowercase_bool(self):
        rv = ResolvedValues(features={"enable_x": True, "enable_y": False})
        tf = rv.as_tf_vars()
        assert tf["TF_VAR_enable_x"] == "true"
        assert tf["TF_VAR_enable_y"] == "false"

    def test_as_tf_vars_none_feature_skipped(self):
        rv = ResolvedValues(features={"flag": None})
        tf = rv.as_tf_vars()
        assert "TF_VAR_flag" not in tf

    def test_as_tf_vars_empty_produces_empty_dict(self):
        rv = ResolvedValues()
        assert rv.as_tf_vars() == {}


# ---------------------------------------------------------------------------
# inject_tf_vars context manager
# ---------------------------------------------------------------------------


class TestInjectTfVars:
    def test_sets_vars_inside_context(self):
        rv = ResolvedValues(variables={"my_test_var": "hello"})
        with inject_tf_vars(rv):
            assert os.environ.get("TF_VAR_my_test_var") == "hello"

    def test_restores_vars_after_context(self):
        rv = ResolvedValues(variables={"test_restore_var": "value"})
        os.environ.pop("TF_VAR_test_restore_var", None)
        with inject_tf_vars(rv):
            pass
        assert "TF_VAR_test_restore_var" not in os.environ

    def test_restores_original_value_after_context(self):
        os.environ["TF_VAR_overwrite_test"] = "original"
        rv = ResolvedValues(variables={"overwrite_test": "new"})
        with inject_tf_vars(rv):
            assert os.environ["TF_VAR_overwrite_test"] == "new"
        assert os.environ["TF_VAR_overwrite_test"] == "original"
        del os.environ["TF_VAR_overwrite_test"]

    def test_empty_resolved_noop(self):
        rv = ResolvedValues()
        # Should not raise, no env vars set
        with inject_tf_vars(rv):
            pass

    def test_none_resolved_noop(self):
        with inject_tf_vars(None):
            pass


# ---------------------------------------------------------------------------
# ValueController._resolve_variable
# ---------------------------------------------------------------------------


class TestValueControllerResolveVariable:
    def test_resolve_constant_variable(self):
        ctrl = ValueController()
        item = VariableStoreModel(key="region", store=VariableStoreType.CONSTANT, value="eu-west-1")
        val, err = ctrl._resolve_variable(item)
        assert err is None
        assert val == "eu-west-1"

    def test_resolve_environment_variable_present(self, monkeypatch):
        monkeypatch.setenv("TEST_REGION_VAR", "us-east-1")
        ctrl = ValueController()
        item = VariableStoreModel(key="region", store=VariableStoreType.ENVIRONMENT, value="TEST_REGION_VAR")
        val, err = ctrl._resolve_variable(item)
        assert err is None
        assert val == "us-east-1"

    def test_resolve_environment_variable_missing(self, monkeypatch):
        monkeypatch.delenv("MISSING_XYZ_VAR", raising=False)
        ctrl = ValueController()
        item = VariableStoreModel(key="region", store=VariableStoreType.ENVIRONMENT, value="MISSING_XYZ_VAR")
        val, err = ctrl._resolve_variable(item)
        assert val is None
        assert err is not None
        assert "not set" in err


# ---------------------------------------------------------------------------
# ValueController._resolve_secret
# ---------------------------------------------------------------------------


class TestValueControllerResolveSecret:
    def test_resolve_constant_secret(self):
        ctrl = ValueController()
        item = SecretStoreModel(key="api_key", store=SecretStoreType.CONSTANT, value="my-secret")
        val, err = ctrl._resolve_secret(item)
        assert err is None
        assert val == "my-secret"

    def test_resolve_environment_secret_present(self, monkeypatch):
        monkeypatch.setenv("TEST_SECRET_ENV", "super-secret")
        ctrl = ValueController()
        item = SecretStoreModel(key="token", store=SecretStoreType.ENVIRONMENT, value="TEST_SECRET_ENV")
        val, err = ctrl._resolve_secret(item)
        assert err is None
        assert val == "super-secret"

    def test_resolve_environment_secret_missing(self, monkeypatch):
        monkeypatch.delenv("MISSING_SECRET_ENV", raising=False)
        ctrl = ValueController()
        item = SecretStoreModel(key="token", store=SecretStoreType.ENVIRONMENT, value="MISSING_SECRET_ENV")
        val, err = ctrl._resolve_secret(item)
        assert val is None
        assert err is not None
        assert "not set" in err


# ---------------------------------------------------------------------------
# ValueController._resolve_feature
# ---------------------------------------------------------------------------


class TestValueControllerResolveFeature:
    def test_resolve_constant_feature_true(self):
        ctrl = ValueController()
        item = FeatureStoreModel(key="flag", store=FeatureStoreType.CONSTANT, value=True)
        val, err = ctrl._resolve_feature(item)
        assert err is None
        assert val is True

    def test_resolve_constant_feature_false(self):
        ctrl = ValueController()
        item = FeatureStoreModel(key="flag", store=FeatureStoreType.CONSTANT, value=False)
        val, err = ctrl._resolve_feature(item)
        assert err is None
        assert val is False

    def test_resolve_environment_feature_truthy(self, monkeypatch):
        monkeypatch.setenv("TEST_FEATURE_FLAG", "true")
        ctrl = ValueController()
        item = FeatureStoreModel(key="flag", store=FeatureStoreType.ENVIRONMENT, value="TEST_FEATURE_FLAG")
        val, err = ctrl._resolve_feature(item)
        assert err is None
        assert val is True

    def test_resolve_environment_feature_falsy(self, monkeypatch):
        monkeypatch.setenv("TEST_FEATURE_FLAG_OFF", "false")
        ctrl = ValueController()
        item = FeatureStoreModel(key="flag", store=FeatureStoreType.ENVIRONMENT, value="TEST_FEATURE_FLAG_OFF")
        val, err = ctrl._resolve_feature(item)
        assert err is None
        assert val is False

    def test_resolve_environment_feature_missing(self, monkeypatch):
        monkeypatch.delenv("MISSING_FEATURE_FLAG", raising=False)
        ctrl = ValueController()
        item = FeatureStoreModel(key="flag", store=FeatureStoreType.ENVIRONMENT, value="MISSING_FEATURE_FLAG")
        val, err = ctrl._resolve_feature(item)
        assert val is None
        assert err is not None
        assert "not set" in err


# ---------------------------------------------------------------------------
# ValueController.resolve_values — no deployment service
# ---------------------------------------------------------------------------


class TestValueControllerResolveValues:
    def test_resolve_values_no_environment_service(self):
        """resolve_values returns success with empty ResolvedValues when no env service."""
        mock_deployment_service = MagicMock()
        mock_deployment_service.get_environment_service.return_value = None

        ctrl = ValueController()
        ok, resolved, errors = ctrl.resolve_values(mock_deployment_service)
        assert ok is True
        assert resolved.is_empty() is True
        assert errors == []
