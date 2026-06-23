"""Tests for ValueController, ResolvedValues, and inject_tf_vars."""

import os
from unittest.mock import MagicMock, patch

from strata.controllers.value_controller import (
    ResolvedValues,
    ValueController,
    inject_compose_env,
    inject_tf_vars,
)
from strata.models.store_models import (
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
# ResolvedValues.as_compose_env
# ---------------------------------------------------------------------------


class TestResolvedValuesAsComposeEnv:
    def test_variables_use_bare_key(self):
        rv = ResolvedValues(variables={"DB_HOST": "localhost"})
        env = rv.as_compose_env()
        assert env["DB_HOST"] == "localhost"
        assert "TF_VAR_DB_HOST" not in env

    def test_secrets_use_bare_key(self):
        rv = ResolvedValues(secrets={"DB_PASSWORD": "hunter2"})
        env = rv.as_compose_env()
        assert env["DB_PASSWORD"] == "hunter2"

    def test_features_serialized_as_lowercase_bool(self):
        rv = ResolvedValues(features={"ENABLE_METRICS": True, "DEBUG": False})
        env = rv.as_compose_env()
        assert env["ENABLE_METRICS"] == "true"
        assert env["DEBUG"] == "false"

    def test_none_feature_skipped(self):
        rv = ResolvedValues(features={"FLAG": None})
        env = rv.as_compose_env()
        assert "FLAG" not in env

    def test_secrets_win_over_variables_on_collision(self):
        rv = ResolvedValues(variables={"KEY": "from_var"}, secrets={"KEY": "from_secret"})
        env = rv.as_compose_env()
        assert env["KEY"] == "from_secret"

    def test_variables_win_over_features_on_collision(self):
        rv = ResolvedValues(features={"KEY": True}, variables={"KEY": "from_var"})
        env = rv.as_compose_env()
        assert env["KEY"] == "from_var"

    def test_empty_produces_empty_dict(self):
        rv = ResolvedValues()
        assert rv.as_compose_env() == {}


# ---------------------------------------------------------------------------
# inject_compose_env context manager
# ---------------------------------------------------------------------------


class TestInjectComposeEnv:
    def test_sets_vars_inside_context(self):
        rv = ResolvedValues(variables={"COMPOSE_TEST_VAR": "hello"})
        with inject_compose_env(rv):
            assert os.environ.get("COMPOSE_TEST_VAR") == "hello"

    def test_restores_vars_after_context(self):
        rv = ResolvedValues(variables={"COMPOSE_RESTORE_VAR": "value"})
        os.environ.pop("COMPOSE_RESTORE_VAR", None)
        with inject_compose_env(rv):
            pass
        assert "COMPOSE_RESTORE_VAR" not in os.environ

    def test_restores_original_value_after_context(self):
        os.environ["COMPOSE_OVERWRITE_TEST"] = "original"
        rv = ResolvedValues(variables={"COMPOSE_OVERWRITE_TEST": "new"})
        with inject_compose_env(rv):
            assert os.environ["COMPOSE_OVERWRITE_TEST"] == "new"
        assert os.environ["COMPOSE_OVERWRITE_TEST"] == "original"
        del os.environ["COMPOSE_OVERWRITE_TEST"]

    def test_empty_resolved_noop(self):
        rv = ResolvedValues()
        with inject_compose_env(rv):
            pass

    def test_none_resolved_noop(self):
        with inject_compose_env(None):
            pass

    def test_secrets_injected_inside_context(self):
        rv = ResolvedValues(secrets={"DB_PASSWORD": "secret123"})
        os.environ.pop("DB_PASSWORD", None)
        with inject_compose_env(rv):
            assert os.environ.get("DB_PASSWORD") == "secret123"
        assert "DB_PASSWORD" not in os.environ

    def test_features_injected_as_lowercase(self):
        rv = ResolvedValues(features={"ENABLE_X": True})
        os.environ.pop("ENABLE_X", None)
        with inject_compose_env(rv):
            assert os.environ.get("ENABLE_X") == "true"
        assert "ENABLE_X" not in os.environ


# ---------------------------------------------------------------------------
# ResolvedValues.for_stage — secret scoping
# ---------------------------------------------------------------------------


class TestResolvedValuesForStage:
    def _full_rv(self):
        return ResolvedValues(
            variables={"region": "eu-west-1"},
            secrets={"db_pass": "hunter2", "api_key": "abc123", "ssh_key": "PRIVATE"},
            features={"enable_x": True},
            stage_outputs={"server_ip": "10.0.0.1"},
            stage_outputs_sensitive={"kubeconfig": "YAML", "token": "tok123"},
        )

    def test_none_secrets_strips_all_sensitive(self):
        rv = self._full_rv()
        scoped = rv.for_stage(None)
        assert scoped.variables == {"region": "eu-west-1"}
        assert scoped.features == {"enable_x": True}
        assert scoped.stage_outputs == {"server_ip": "10.0.0.1"}
        assert scoped.secrets == {}
        assert scoped.stage_outputs_sensitive == {}

    def test_empty_list_strips_all_sensitive(self):
        rv = self._full_rv()
        scoped = rv.for_stage([])
        assert scoped.secrets == {}
        assert scoped.stage_outputs_sensitive == {}

    def test_wildcard_passes_all(self):
        rv = self._full_rv()
        scoped = rv.for_stage(["*"])
        assert scoped.secrets == rv.secrets
        assert scoped.stage_outputs_sensitive == rv.stage_outputs_sensitive
        assert scoped.variables == rv.variables

    def test_specific_keys_filters_secrets(self):
        rv = self._full_rv()
        scoped = rv.for_stage(["db_pass", "ssh_key"])
        assert scoped.secrets == {"db_pass": "hunter2", "ssh_key": "PRIVATE"}
        assert "api_key" not in scoped.secrets

    def test_specific_keys_filters_sensitive_outputs(self):
        rv = self._full_rv()
        scoped = rv.for_stage(["kubeconfig"])
        assert scoped.stage_outputs_sensitive == {"kubeconfig": "YAML"}
        assert "token" not in scoped.stage_outputs_sensitive
        # secrets filtered too — only kubeconfig key, which isn't in secrets
        assert scoped.secrets == {}

    def test_nonexistent_key_produces_empty(self):
        rv = self._full_rv()
        scoped = rv.for_stage(["does_not_exist"])
        assert scoped.secrets == {}
        assert scoped.stage_outputs_sensitive == {}

    def test_context_always_passes_through(self):
        rv = self._full_rv()
        for allowed in [None, [], ["db_pass"], ["*"]]:
            scoped = rv.for_stage(allowed)
            assert scoped.variables == rv.variables
            assert scoped.features == rv.features
            assert scoped.stage_outputs == rv.stage_outputs

    def test_returns_independent_copy(self):
        rv = self._full_rv()
        scoped = rv.for_stage(["*"])
        scoped.variables["new_key"] = "new_val"
        assert "new_key" not in rv.variables

    def test_errors_preserved(self):
        rv = ResolvedValues(errors=["some warning"])
        scoped = rv.for_stage(None)
        assert scoped.errors == ["some warning"]


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


# ---------------------------------------------------------------------------
# ValueController._resolve_secret — github store
# ---------------------------------------------------------------------------


class TestValueControllerGithubStore:
    def test_github_store_resolves_when_env_var_present_and_github_actions_true(self, monkeypatch):
        """Success path: env var present, GITHUB_ACTIONS=true — returns value, no error."""
        monkeypatch.setenv("MY_SECRET", "s3cr3t")
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        ctrl = ValueController()
        item = SecretStoreModel(key="my_secret", store=SecretStoreType.GITHUB, value="MY_SECRET")
        val, err = ctrl._resolve_secret(item)
        assert err is None
        assert val == "s3cr3t"

    def test_github_store_normalizes_value_to_uppercase(self, monkeypatch):
        """value 'my_secret' (lowercase) resolves via env var 'MY_SECRET' (.upper() applied)."""
        monkeypatch.setenv("MY_SECRET", "s3cr3t")
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        ctrl = ValueController()
        item = SecretStoreModel(key="db_password", store=SecretStoreType.GITHUB, value="my_secret")
        val, err = ctrl._resolve_secret(item)
        assert err is None
        assert val == "s3cr3t"

    def test_github_store_missing_env_var_returns_error(self, monkeypatch):
        """Env var absent — returns (None, error) with 'GitHub Actions' in the message."""
        monkeypatch.delenv("MISSING_GH_SECRET", raising=False)
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        ctrl = ValueController()
        item = SecretStoreModel(key="token", store=SecretStoreType.GITHUB, value="MISSING_GH_SECRET")
        val, err = ctrl._resolve_secret(item)
        assert val is None
        assert err is not None
        assert "GitHub Actions" in err

    def test_github_store_warns_when_github_actions_not_set(self, monkeypatch):
        """Warning emitted when GITHUB_ACTIONS is absent; resolution still succeeds."""
        monkeypatch.setenv("MY_SECRET", "s3cr3t")
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        ctrl = ValueController()
        item = SecretStoreModel(key="my_secret", store=SecretStoreType.GITHUB, value="MY_SECRET")
        with patch("strata.controllers.value_controller.logger") as mock_logger:
            val, err = ctrl._resolve_secret(item)
        assert err is None
        assert val == "s3cr3t"
        mock_logger.warning.assert_called_once()

    def test_github_store_no_warning_when_github_actions_true(self, monkeypatch):
        """No warning emitted when GITHUB_ACTIONS=true."""
        monkeypatch.setenv("MY_SECRET", "s3cr3t")
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        ctrl = ValueController()
        item = SecretStoreModel(key="my_secret", store=SecretStoreType.GITHUB, value="MY_SECRET")
        with patch("strata.controllers.value_controller.logger") as mock_logger:
            val, err = ctrl._resolve_secret(item)
        assert err is None
        assert val == "s3cr3t"
        mock_logger.warning.assert_not_called()


# ---------------------------------------------------------------------------
# ResolvedValues — stage_outputs and stage_outputs_sensitive
# ---------------------------------------------------------------------------


class TestResolvedValuesStageOutputs:
    # --- is_empty ---

    def test_is_empty_false_with_stage_outputs(self):
        rv = ResolvedValues(stage_outputs={"cluster_ip": "1.2.3.4"})
        assert rv.is_empty() is False

    def test_is_empty_true_with_only_sensitive_outputs(self):
        # stage_outputs_sensitive alone does not flip is_empty — sensitive values
        # are never injected, so there is nothing to "do" from the env perspective.
        rv = ResolvedValues(stage_outputs_sensitive={"admin_token": "secret"})
        assert rv.is_empty() is True

    # --- as_tf_vars: non-sensitive outputs ARE injected ---

    def test_stage_outputs_present_in_tf_vars(self):
        rv = ResolvedValues(stage_outputs={"cluster_ip": "10.0.0.1"})
        tf = rv.as_tf_vars()
        assert tf["TF_VAR_cluster_ip"] == "10.0.0.1"

    def test_stage_outputs_dict_value_json_encoded_in_tf_vars(self):
        rv = ResolvedValues(stage_outputs={"config": {"host": "db", "port": 5432}})
        tf = rv.as_tf_vars()
        import json

        assert tf["TF_VAR_config"] == json.dumps({"host": "db", "port": 5432})

    def test_stage_outputs_list_value_json_encoded_in_tf_vars(self):
        rv = ResolvedValues(stage_outputs={"zones": ["a", "b", "c"]})
        tf = rv.as_tf_vars()
        import json

        assert tf["TF_VAR_zones"] == json.dumps(["a", "b", "c"])

    def test_stage_outputs_none_value_skipped_in_tf_vars(self):
        rv = ResolvedValues(stage_outputs={"key": None})
        tf = rv.as_tf_vars()
        assert "TF_VAR_key" not in tf

    # --- as_tf_vars: sensitive outputs are NOT injected ---

    def test_stage_outputs_sensitive_absent_from_tf_vars(self):
        rv = ResolvedValues(stage_outputs_sensitive={"admin_token": "secret"})
        tf = rv.as_tf_vars()
        assert "TF_VAR_admin_token" not in tf

    def test_stage_outputs_sensitive_absent_even_with_other_vars(self):
        rv = ResolvedValues(
            variables={"region": "eu"},
            stage_outputs={"endpoint": "https://x"},
            stage_outputs_sensitive={"token": "s3cr3t"},
        )
        tf = rv.as_tf_vars()
        assert "TF_VAR_region" in tf
        assert "TF_VAR_endpoint" in tf
        assert "TF_VAR_token" not in tf

    # --- as_compose_env: non-sensitive outputs ARE injected verbatim ---

    def test_stage_outputs_present_in_compose_env(self):
        rv = ResolvedValues(stage_outputs={"DB_HOST": "postgres"})
        env = rv.as_compose_env()
        assert env["DB_HOST"] == "postgres"
        assert "TF_VAR_DB_HOST" not in env

    def test_stage_outputs_dict_value_json_encoded_in_compose_env(self):
        rv = ResolvedValues(stage_outputs={"config": {"a": 1}})
        env = rv.as_compose_env()
        import json

        assert env["config"] == json.dumps({"a": 1})

    def test_stage_outputs_none_value_skipped_in_compose_env(self):
        rv = ResolvedValues(stage_outputs={"missing": None})
        env = rv.as_compose_env()
        assert "missing" not in env

    # --- as_compose_env: sensitive outputs are NOT injected ---

    def test_stage_outputs_sensitive_absent_from_compose_env(self):
        rv = ResolvedValues(stage_outputs_sensitive={"DB_PASSWORD": "hunter2"})
        env = rv.as_compose_env()
        assert "DB_PASSWORD" not in env

    # --- injection context manager picks up stage_outputs ---

    def test_inject_tf_vars_injects_stage_outputs(self):
        rv = ResolvedValues(stage_outputs={"cluster_ip": "1.2.3.4"})
        os.environ.pop("TF_VAR_cluster_ip", None)
        with inject_tf_vars(rv):
            assert os.environ.get("TF_VAR_cluster_ip") == "1.2.3.4"
        assert "TF_VAR_cluster_ip" not in os.environ

    def test_inject_tf_vars_does_not_inject_sensitive(self):
        rv = ResolvedValues(stage_outputs_sensitive={"admin_token": "secret"})
        os.environ.pop("TF_VAR_admin_token", None)
        with inject_tf_vars(rv):
            assert "TF_VAR_admin_token" not in os.environ
        assert "TF_VAR_admin_token" not in os.environ

    def test_inject_compose_env_injects_stage_outputs(self):
        rv = ResolvedValues(stage_outputs={"DB_HOST": "postgres"})
        os.environ.pop("DB_HOST", None)
        with inject_compose_env(rv):
            assert os.environ.get("DB_HOST") == "postgres"
        assert "DB_HOST" not in os.environ

    def test_inject_compose_env_does_not_inject_sensitive(self):
        rv = ResolvedValues(stage_outputs_sensitive={"DB_PASSWORD": "hunter2"})
        os.environ.pop("DB_PASSWORD", None)
        with inject_compose_env(rv):
            assert "DB_PASSWORD" not in os.environ


# ---------------------------------------------------------------------------
# ValueController._resolve_secret — generate-on-missing (Phase 1)
# ---------------------------------------------------------------------------


class TestValueControllerSecretGenerateOnMissing:
    def _make_item(self, generate_type="password", length=16):
        from strata.models.store_models import SecretGenerateSpec

        return SecretStoreModel(
            key="DB_PASSWORD",
            store=SecretStoreType.AZURE_KEYVAULT,
            value="myapp-db-password",
            generate=SecretGenerateSpec(type=generate_type, length=length),
        )

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    @patch("strata.controllers.value_controller.ValueController._get_integration_by_type")
    def test_secret_exists_no_generate_called(self, mock_get_integration, _mock_init):
        """If the secret already exists, set_secret is never called."""
        mock_integration = MagicMock()
        mock_integration.get_secret.return_value = "existing-value"
        mock_get_integration.return_value = mock_integration

        ctrl = ValueController()
        val, err = ctrl._resolve_secret(self._make_item())

        assert err is None
        assert val == "existing-value"
        mock_integration.set_secret.assert_not_called()

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    @patch("strata.controllers.value_controller.ValueController._get_integration_by_type")
    def test_secret_missing_with_generate_writes_and_returns_generated(self, mock_get_integration, _mock_init):
        """Missing secret + generate spec → generates, writes, returns the value."""
        mock_integration = MagicMock()
        mock_integration.get_secret.return_value = None
        mock_integration.set_secret.return_value = True
        mock_get_integration.return_value = mock_integration

        ctrl = ValueController()
        val, err = ctrl._resolve_secret(self._make_item())

        assert err is None
        assert val is not None
        assert len(val) > 0
        mock_integration.set_secret.assert_called_once()

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    @patch("strata.controllers.value_controller.ValueController._get_integration_by_type")
    def test_secret_missing_no_generate_returns_error(self, mock_get_integration, _mock_init):
        """Missing secret without generate spec → error."""
        mock_integration = MagicMock()
        mock_integration.get_secret.return_value = None
        mock_get_integration.return_value = mock_integration

        ctrl = ValueController()
        item = SecretStoreModel(key="TOKEN", store=SecretStoreType.AZURE_KEYVAULT, value="myapp-token")
        val, err = ctrl._resolve_secret(item)

        assert val is None
        assert err is not None
        assert "not found" in err

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    @patch("strata.controllers.value_controller.ValueController._get_integration_by_type")
    def test_set_secret_fails_reread_succeeds_uses_existing(self, mock_get_integration, _mock_init):
        """Race condition: set_secret fails but re-read finds a value → use it."""
        mock_integration = MagicMock()
        mock_integration.get_secret.side_effect = [None, "race-winner-value"]
        mock_integration.set_secret.return_value = False
        mock_get_integration.return_value = mock_integration

        ctrl = ValueController()
        val, err = ctrl._resolve_secret(self._make_item())

        assert err is None
        assert val == "race-winner-value"

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    @patch("strata.controllers.value_controller.ValueController._get_integration_by_type")
    def test_set_secret_fails_reread_also_fails_returns_error(self, mock_get_integration, _mock_init):
        """set_secret fails and re-read also returns None → error."""
        mock_integration = MagicMock()
        mock_integration.get_secret.return_value = None
        mock_integration.set_secret.return_value = False
        mock_get_integration.return_value = mock_integration

        ctrl = ValueController()
        val, err = ctrl._resolve_secret(self._make_item())

        assert val is None
        assert err is not None

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    @patch("strata.controllers.value_controller.ValueController._get_integration_by_type")
    def test_generate_idempotent_second_call_reads_not_writes(self, mock_get_integration, _mock_init):
        """Second call finds the existing value — set_secret called only once total."""
        mock_integration = MagicMock()
        # First call: missing; second call: present
        mock_integration.get_secret.side_effect = [None, "generated-value"]
        mock_integration.set_secret.return_value = True
        mock_get_integration.return_value = mock_integration

        ctrl = ValueController()
        val1, err1 = ctrl._resolve_secret(self._make_item())
        assert err1 is None
        mock_integration.set_secret.assert_called_once()

        val2, err2 = ctrl._resolve_secret(self._make_item())
        assert err2 is None
        assert val2 == "generated-value"
        # Still only called once
        mock_integration.set_secret.assert_called_once()


# ---------------------------------------------------------------------------
# ValueController._resolve_variable — seed-on-missing (Phase 2)
# ---------------------------------------------------------------------------


class TestValueControllerVariableSeedOnMissing:
    def _make_item(self, default="info"):
        return VariableStoreModel(
            key="LOG_LEVEL",
            store=VariableStoreType.AZURE_APPCONFIG,
            value="myapp/log-level",
            default=default,
        )

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    @patch("strata.controllers.value_controller.ValueController._get_integration_by_type")
    def test_variable_exists_no_seed_called(self, mock_get_integration, _mock_init):
        mock_integration = MagicMock()
        mock_integration.get_variable.return_value = "debug"
        mock_get_integration.return_value = mock_integration

        ctrl = ValueController()
        val, err = ctrl._resolve_variable(self._make_item())

        assert err is None
        assert val == "debug"
        mock_integration.set_variable.assert_not_called()

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    @patch("strata.controllers.value_controller.ValueController._get_integration_by_type")
    def test_variable_missing_with_default_seeds_and_returns_default(self, mock_get_integration, _mock_init):
        mock_integration = MagicMock()
        mock_integration.get_variable.return_value = None
        mock_integration.set_variable.return_value = True
        mock_get_integration.return_value = mock_integration

        ctrl = ValueController()
        val, err = ctrl._resolve_variable(self._make_item("info"))

        assert err is None
        assert val == "info"
        mock_integration.set_variable.assert_called_once_with("myapp/log-level", "info")

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    @patch("strata.controllers.value_controller.ValueController._get_integration_by_type")
    def test_variable_missing_no_default_returns_error(self, mock_get_integration, _mock_init):
        mock_integration = MagicMock()
        mock_integration.get_variable.return_value = None
        mock_get_integration.return_value = mock_integration

        ctrl = ValueController()
        item = VariableStoreModel(key="KEY", store=VariableStoreType.AZURE_APPCONFIG, value="myapp/key")
        val, err = ctrl._resolve_variable(item)

        assert val is None
        assert err is not None
        assert "not found" in err

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    @patch("strata.controllers.value_controller.ValueController._get_integration_by_type")
    def test_variable_seed_race_re_read_used(self, mock_get_integration, _mock_init):
        mock_integration = MagicMock()
        mock_integration.get_variable.side_effect = [None, "race-value"]
        mock_integration.set_variable.return_value = False
        mock_get_integration.return_value = mock_integration

        ctrl = ValueController()
        val, err = ctrl._resolve_variable(self._make_item())

        assert err is None
        assert val == "race-value"


# ---------------------------------------------------------------------------
# ValueController._resolve_feature — seed-on-missing (Phase 2)
# ---------------------------------------------------------------------------


class TestValueControllerFeatureSeedOnMissing:
    def _make_item(self, default="false"):
        return FeatureStoreModel(
            key="DARK_MODE",
            store=FeatureStoreType.AZURE_APPCONFIG,
            value="myapp-dark-mode",
            default=default,
        )

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    @patch("strata.controllers.value_controller.ValueController._get_integration_by_type")
    def test_feature_exists_no_seed_called(self, mock_get_integration, _mock_init):
        mock_integration = MagicMock()
        mock_integration.get_feature.return_value = True
        mock_get_integration.return_value = mock_integration

        ctrl = ValueController()
        val, err = ctrl._resolve_feature(self._make_item())

        assert err is None
        assert val is True
        mock_integration.set_feature.assert_not_called()

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    @patch("strata.controllers.value_controller.ValueController._get_integration_by_type")
    def test_feature_missing_default_false_seeds_disabled(self, mock_get_integration, _mock_init):
        mock_integration = MagicMock()
        mock_integration.get_feature.return_value = None
        mock_integration.set_feature.return_value = True
        mock_get_integration.return_value = mock_integration

        ctrl = ValueController()
        val, err = ctrl._resolve_feature(self._make_item("false"))

        assert err is None
        assert val is False
        mock_integration.set_feature.assert_called_once_with("myapp-dark-mode", False)

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    @patch("strata.controllers.value_controller.ValueController._get_integration_by_type")
    def test_feature_missing_default_true_seeds_enabled(self, mock_get_integration, _mock_init):
        mock_integration = MagicMock()
        mock_integration.get_feature.return_value = None
        mock_integration.set_feature.return_value = True
        mock_get_integration.return_value = mock_integration

        ctrl = ValueController()
        val, err = ctrl._resolve_feature(self._make_item("true"))

        assert err is None
        assert val is True
        mock_integration.set_feature.assert_called_once_with("myapp-dark-mode", True)

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    @patch("strata.controllers.value_controller.ValueController._get_integration_by_type")
    def test_feature_missing_no_default_returns_error(self, mock_get_integration, _mock_init):
        mock_integration = MagicMock()
        mock_integration.get_feature.return_value = None
        mock_get_integration.return_value = mock_integration

        ctrl = ValueController()
        item = FeatureStoreModel(key="FLAG", store=FeatureStoreType.AZURE_APPCONFIG, value="myapp-flag")
        val, err = ctrl._resolve_feature(item)

        assert val is None
        assert err is not None
        assert "not found" in err
