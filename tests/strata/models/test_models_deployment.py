"""Unit tests for deployment model — DeploymentStageTimeoutsModel."""

import pytest
from pydantic import ValidationError

from strata.models.deployment_model import DeploymentModel, DeploymentStageModel, DeploymentStageTimeoutsModel


class TestDeploymentStageTimeoutsModel:
    def test_all_fields_optional(self):
        t = DeploymentStageTimeoutsModel()
        assert t.setup is None
        assert t.check is None
        assert t.plan is None
        assert t.apply is None
        assert t.destroy is None

    def test_partial_override(self):
        t = DeploymentStageTimeoutsModel(plan=300, apply=1200)
        assert t.setup is None
        assert t.check is None
        assert t.plan == 300
        assert t.apply == 1200
        assert t.destroy is None

    def test_all_fields_set(self):
        t = DeploymentStageTimeoutsModel(setup=60, check=30, plan=300, apply=900, destroy=900)
        assert t.setup == 60
        assert t.check == 30
        assert t.plan == 300
        assert t.apply == 900
        assert t.destroy == 900

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            DeploymentStageTimeoutsModel(plan="fast")

    def test_old_field_names_not_accepted(self):
        """init and validate were renamed to setup and check — extra='forbid' now rejects them."""
        with pytest.raises(ValidationError):
            DeploymentStageTimeoutsModel(init=999, validate=999)  # type: ignore[call-arg]


class TestDeploymentStageModelTimeoutsField:
    def test_timeouts_optional(self):
        stage = DeploymentStageModel(name="prod")
        assert stage.timeouts is None

    def test_timeouts_parsed_from_dict(self):
        stage = DeploymentStageModel(
            name="prod",
            timeouts={"setup": 120, "apply": 1200},
        )
        assert stage.timeouts is not None
        assert stage.timeouts.setup == 120
        assert stage.timeouts.apply == 1200
        assert stage.timeouts.plan is None

    def test_timeouts_model_instance_accepted(self):
        t = DeploymentStageTimeoutsModel(plan=600)
        stage = DeploymentStageModel(name="prod", timeouts=t)
        assert stage.timeouts is not None
        assert stage.timeouts.plan == 600


class TestDeploymentStageModelSecretsField:
    def test_secrets_optional_defaults_none(self):
        stage = DeploymentStageModel(name="prod")
        assert stage.secrets is None

    def test_secrets_empty_list(self):
        stage = DeploymentStageModel(name="prod", secrets=[])
        assert stage.secrets == []

    def test_secrets_specific_keys(self):
        stage = DeploymentStageModel(name="prod", secrets=["db_pass", "api_key"])
        assert stage.secrets == ["db_pass", "api_key"]

    def test_secrets_wildcard(self):
        stage = DeploymentStageModel(name="prod", secrets=["*"])
        assert stage.secrets == ["*"]

    def test_secrets_roundtrip_with_other_fields(self):
        stage = DeploymentStageModel(
            name="infra",
            provisioner="my_tf",
            secrets=["hetzner_api_token"],
            timeouts={"apply": 1200},
        )
        assert stage.secrets == ["hetzner_api_token"]
        assert stage.timeouts is not None
        assert stage.timeouts.apply == 1200


class TestDeploymentCustomerField:
    """Tests for the optional customer field on DeploymentSpecModel."""

    def _spec(self, **overrides):
        base = {
            "workspace": {"name": "ws", "file": "workspace.yaml"},
            "environments": ["env.yaml"],
        }
        base.update(overrides)
        return base

    def _model(self, **spec_overrides):
        return DeploymentModel.model_validate(
            {
                "apiVersion": "strata.huybrechts.xyz/v1",
                "kind": "deployment",
                "meta": {"name": "test_deploy"},
                "spec": self._spec(**spec_overrides),
            }
        )

    def test_customer_absent_defaults_none(self):
        """customer is None when not specified."""
        model = self._model()
        assert model.spec.customer is None

    def test_customer_valid_platform_name(self):
        """A valid PlatformName is accepted."""
        model = self._model(customer="acme")
        assert model.spec.customer == "acme"

    def test_customer_invalid_platform_name_rejected(self):
        """Uppercase / spaces are rejected by PlatformName constraint."""
        with pytest.raises(ValidationError):
            self._model(customer="ACME Corp")
