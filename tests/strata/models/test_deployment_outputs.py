"""Tests for Gap 5 — deployment outputs model and combined artifact."""

import json

from strata.models.deployment_outputs_model import (
    DeploymentOutputsMetaModel,
    DeploymentOutputsModel,
)

# ---------------------------------------------------------------------------
# DeploymentOutputsModel
# ---------------------------------------------------------------------------


class TestDeploymentOutputsModel:
    def test_minimal_valid(self):
        model = DeploymentOutputsModel(
            meta=DeploymentOutputsMetaModel(
                name="deploy-prd",
                deployment="deploy-prd",
                version="1.0.0",
                deployed_at="2026-08-06T14:00:00Z",
                workspace="my-workspace",
            ),
            outputs={},
        )
        assert model.kind == "deployment-outputs"
        assert model.outputs == {}
        assert model.sensitive_keys == []

    def test_with_stage_outputs(self):
        model = DeploymentOutputsModel(
            meta=DeploymentOutputsMetaModel(
                name="deploy-prd",
                deployment="deploy-prd",
                version="1.5.0",
                deployed_at="2026-08-06T14:30:00Z",
                workspace="my-workspace",
                environment="production",
                tenant="contoso",
            ),
            outputs={
                "platform_baseline": {
                    "vnet_id": "/subscriptions/.../vnet",
                    "cluster_id": "/subscriptions/.../aks",
                },
                "vct_module": {
                    "app_ip": "20.93.45.67",
                },
            },
            sensitive_keys=["platform_baseline.admin_password"],
            provenance={
                "stages_completed": ["platform_baseline", "vct_module"],
            },
        )
        assert len(model.outputs) == 2
        assert model.outputs["platform_baseline"]["vnet_id"].startswith("/subscriptions")
        assert model.sensitive_keys == ["platform_baseline.admin_password"]
        assert model.provenance["stages_completed"] == ["platform_baseline", "vct_module"]

    def test_serialization_roundtrip(self):
        model = DeploymentOutputsModel(
            meta=DeploymentOutputsMetaModel(
                name="deploy-dev",
                deployment="deploy-dev",
                version="0.1.0",
                deployed_at="2026-08-06T10:00:00Z",
                workspace="ws",
            ),
            outputs={"infra": {"ip": "1.2.3.4"}},
        )
        data = model.model_dump(exclude_none=True)
        assert data["kind"] == "deployment-outputs"
        assert data["outputs"]["infra"]["ip"] == "1.2.3.4"

        # Should be valid JSON
        json_str = json.dumps(data, default=str)
        parsed = json.loads(json_str)
        assert parsed["meta"]["name"] == "deploy-dev"

    def test_empty_outputs_valid(self):
        """Deploy with no Terraform outputs still produces a valid artifact."""
        model = DeploymentOutputsModel(
            meta=DeploymentOutputsMetaModel(
                name="deploy-empty",
                deployment="deploy-empty",
                version="1.0.0",
                deployed_at="2026-08-06T12:00:00Z",
                workspace="ws",
            ),
            outputs={},
        )
        assert model.outputs == {}

    def test_optional_meta_fields(self):
        model = DeploymentOutputsModel(
            meta=DeploymentOutputsMetaModel(
                name="deploy-x",
                deployment="deploy-x",
                version="1.0.0",
                deployed_at="2026-08-06T12:00:00Z",
                workspace="ws",
            ),
        )
        assert model.meta.environment is None
        assert model.meta.tenant is None

    def test_provenance_includes_stages(self):
        model = DeploymentOutputsModel(
            meta=DeploymentOutputsMetaModel(
                name="d",
                deployment="d",
                version="1.0.0",
                deployed_at="2026-08-06T12:00:00Z",
                workspace="ws",
            ),
            provenance={
                "stages_completed": ["a", "b"],
                "manifest_path": "build/manifest.yaml",
            },
        )
        assert model.provenance["stages_completed"] == ["a", "b"]

    def test_multiple_sensitive_keys(self):
        model = DeploymentOutputsModel(
            meta=DeploymentOutputsMetaModel(
                name="d",
                deployment="d",
                version="1.0.0",
                deployed_at="2026-08-06T12:00:00Z",
                workspace="ws",
            ),
            sensitive_keys=[
                "stage_a.password",
                "stage_a.token",
                "stage_b.secret_key",
            ],
        )
        assert len(model.sensitive_keys) == 3
