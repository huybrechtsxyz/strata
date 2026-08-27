"""Unit tests for deployment model — DeploymentStageTimeoutsModel."""

import pytest
from pydantic import ValidationError

from strata.models.deployment_model import (
    DeploymentLockingModel,
    DeploymentModel,
    DeploymentStageModel,
    DeploymentStageTimeoutsModel,
)


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


class TestDeploymentStageModelHelmNamespacesField:
    """helm_namespaces (plural) is unrelated to namespace (singular) — the latter
    is sync-provisioner-only (argocd/flux); the former is helm-only."""

    def test_helm_namespaces_optional_defaults_none(self):
        stage = DeploymentStageModel(name="apps")
        assert stage.helm_namespaces is None

    def test_helm_namespaces_empty_list(self):
        stage = DeploymentStageModel(name="apps", helm_namespaces=[])
        assert stage.helm_namespaces == []

    def test_helm_namespaces_specific_names(self):
        stage = DeploymentStageModel(name="apps", provisioner="helm", helm_namespaces=["immich", "media"])
        assert stage.helm_namespaces == ["immich", "media"]

    def test_namespace_and_helm_namespaces_coexist_independently(self):
        """The singular sync field and the plural helm field don't interfere with each other."""
        stage = DeploymentStageModel(name="mixed", namespace="argocd", helm_namespaces=["immich"])
        assert stage.namespace == "argocd"
        assert stage.helm_namespaces == ["immich"]


class TestDeploymentTenantField:
    """Tests for the optional tenant field on DeploymentSpecModel."""

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

    def test_tenant_absent_defaults_none(self):
        """tenant is None when not specified."""
        model = self._model()
        assert model.spec.tenant is None

    def test_tenant_valid_platform_name(self):
        """A valid PlatformName is accepted."""
        model = self._model(tenant="acme")
        assert model.spec.tenant == "acme"

    def test_tenant_invalid_platform_name_rejected(self):
        """Uppercase / spaces are rejected by PlatformName constraint."""
        with pytest.raises(ValidationError):
            self._model(tenant="ACME Corp")


class TestDeploymentLockingModel:
    """Tests for DeploymentLockingModel and its wiring into DeploymentSpecModel."""

    def _model(self, locking=None):
        spec = {
            "workspace": {"name": "ws", "file": "workspace.yaml"},
            "environments": ["env.yaml"],
        }
        if locking is not None:
            spec["locking"] = locking
        return DeploymentModel.model_validate(
            {
                "apiVersion": "strata.huybrechts.xyz/v1",
                "kind": "deployment",
                "meta": {"name": "test_deploy"},
                "spec": spec,
            }
        )

    def test_locking_absent_defaults_none(self):
        """spec.locking is None when not declared."""
        model = self._model()
        assert model.spec.locking is None

    def test_locking_enabled_false_by_default(self):
        """enabled defaults to False when locking block is present but empty."""
        model = self._model(locking={})
        assert model.spec.locking is not None
        assert model.spec.locking.enabled is False

    def test_locking_defaults(self):
        """All defaults apply when only enabled: true is set."""
        model = self._model(locking={"enabled": True})
        lock = model.spec.locking
        assert lock.enabled is True
        assert lock.strategy == "wrap"
        assert lock.wait_timeout == "30m"
        assert lock.force_unlock_after == "8h"

    def test_locking_full_config(self):
        """All fields accepted when explicitly provided."""
        model = self._model(
            locking={
                "enabled": True,
                "strategy": "delegate",
                "wait_timeout": "1h",
                "force_unlock_after": "24h",
            }
        )
        lock = model.spec.locking
        assert lock.strategy == "delegate"
        assert lock.wait_timeout == "1h"
        assert lock.force_unlock_after == "24h"

    def test_strategy_invalid_value_rejected(self):
        """An unknown strategy value is rejected by Pydantic."""
        with pytest.raises(ValidationError):
            self._model(locking={"strategy": "unknown"})

    def test_locking_model_standalone(self):
        """DeploymentLockingModel can be instantiated directly."""
        lock = DeploymentLockingModel()
        assert lock.enabled is False
        assert lock.strategy == "wrap"

    def test_locking_roundtrip(self):
        """model_dump preserves all fields."""
        lock = DeploymentLockingModel(enabled=True, strategy="delegate", wait_timeout="2h", force_unlock_after="12h")
        data = lock.model_dump()
        assert data["enabled"] is True
        assert data["strategy"] == "delegate"
        assert data["wait_timeout"] == "2h"
        assert data["force_unlock_after"] == "12h"
