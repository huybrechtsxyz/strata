"""Unit tests for ConfigurationOutputsModel, SensitiveOutputHandling, and the
outputs field on ConfigurationDeploymentModel."""

import pytest
from pydantic import ValidationError

from strata.models.configuration_model import (
    ConfigurationDeploymentModel,
    ConfigurationOutputsModel,
    SensitiveOutputHandling,
)


class TestSensitiveOutputHandling:
    def test_redact_value(self):
        assert SensitiveOutputHandling.REDACT == "redact"

    def test_omit_value(self):
        assert SensitiveOutputHandling.OMIT == "omit"

    def test_only_two_values(self):
        assert set(SensitiveOutputHandling) == {SensitiveOutputHandling.REDACT, SensitiveOutputHandling.OMIT}


class TestConfigurationOutputsModelDefaults:
    def test_defaults(self):
        m = ConfigurationOutputsModel()
        assert m.enabled is True
        assert m.path == ".strata/outputs"
        assert m.sensitive == SensitiveOutputHandling.REDACT

    def test_enabled_default_true(self):
        m = ConfigurationOutputsModel()
        assert m.enabled is True

    def test_path_default(self):
        m = ConfigurationOutputsModel()
        assert m.path == ".strata/outputs"

    def test_sensitive_default_redact(self):
        m = ConfigurationOutputsModel()
        assert m.sensitive == SensitiveOutputHandling.REDACT


class TestConfigurationOutputsModelCustom:
    def test_custom_path(self):
        m = ConfigurationOutputsModel(path="infra/outputs")
        assert m.path == "infra/outputs"

    def test_enabled_false(self):
        m = ConfigurationOutputsModel(enabled=False)
        assert m.enabled is False

    def test_sensitive_omit(self):
        m = ConfigurationOutputsModel(sensitive="omit")
        assert m.sensitive == SensitiveOutputHandling.OMIT

    def test_sensitive_redact_explicit(self):
        m = ConfigurationOutputsModel(sensitive="redact")
        assert m.sensitive == SensitiveOutputHandling.REDACT

    def test_all_fields_explicit(self):
        m = ConfigurationOutputsModel(enabled=False, path="custom/path", sensitive="omit")
        assert m.enabled is False
        assert m.path == "custom/path"
        assert m.sensitive == SensitiveOutputHandling.OMIT


class TestConfigurationOutputsModelInvalid:
    def test_invalid_sensitive_value_rejected(self):
        with pytest.raises(ValidationError):
            ConfigurationOutputsModel(sensitive="include")

    def test_invalid_sensitive_uppercase_rejected(self):
        with pytest.raises(ValidationError):
            ConfigurationOutputsModel(sensitive="REDACT")


class TestConfigurationDeploymentModelOutputsField:
    def test_outputs_absent_defaults_to_none(self):
        m = ConfigurationDeploymentModel()
        assert m.outputs is None

    def test_outputs_present_with_defaults(self):
        m = ConfigurationDeploymentModel(outputs={})
        assert m.outputs is not None
        assert m.outputs.enabled is True
        assert m.outputs.path == ".strata/outputs"
        assert m.outputs.sensitive == SensitiveOutputHandling.REDACT

    def test_outputs_present_custom_path(self):
        m = ConfigurationDeploymentModel(outputs={"path": ".strata/tf-outputs"})
        assert m.outputs is not None
        assert m.outputs.path == ".strata/tf-outputs"

    def test_outputs_enabled_false(self):
        m = ConfigurationDeploymentModel(outputs={"enabled": False})
        assert m.outputs is not None
        assert m.outputs.enabled is False

    def test_outputs_sensitive_omit(self):
        m = ConfigurationDeploymentModel(outputs={"sensitive": "omit"})
        assert m.outputs is not None
        assert m.outputs.sensitive == SensitiveOutputHandling.OMIT

    def test_outputs_coexists_with_manifest(self):
        m = ConfigurationDeploymentModel(
            manifest={"type": "local"},
            outputs={"path": ".strata/outputs", "sensitive": "redact"},
        )
        assert m.manifest is not None
        assert m.outputs is not None
        assert m.manifest.path == ".strata/deployments"
        assert m.outputs.path == ".strata/outputs"

    def test_outputs_invalid_sensitive_rejected(self):
        with pytest.raises(ValidationError):
            ConfigurationDeploymentModel(outputs={"sensitive": "expose"})
