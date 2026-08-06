"""Model for the combined deployment outputs artifact.

Produced after a successful deploy, this file merges all stages' Terraform
outputs into a single registry-consumable JSON document.  It is written to
the build directory alongside the deployment manifest.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from strata.models.common_models import PlatformBaseModel, PlatformName, PlatformVersion


class DeploymentOutputsMetaModel(PlatformBaseModel):
    """Metadata for the combined outputs artifact."""

    name: PlatformName = Field(description="Deployment name")
    deployment: str = Field(description="Full deployment identifier")
    version: str = Field(description="Deployment version label")
    deployed_at: str = Field(description="ISO-8601 timestamp of the deploy")
    workspace: str = Field(description="Workspace name")
    environment: Optional[str] = Field(None, description="Environment name (e.g., production)")
    tenant: Optional[str] = Field(None, description="Tenant code (if tenant-linked)")


class DeploymentOutputsModel(PlatformBaseModel):
    """Registry-consumable outputs artifact produced after a successful deploy.

    Merges all stages' non-sensitive outputs into one document keyed by
    stage/provisioner name.  Sensitive output keys are listed in
    ``sensitive_keys`` but their values are omitted by default.
    """

    apiVersion: PlatformVersion = Field(default="strata.huybrechts.xyz/v1")
    kind: Literal["deployment-outputs"] = "deployment-outputs"
    meta: DeploymentOutputsMetaModel = Field(description="Deployment metadata")
    outputs: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Outputs keyed by stage/provisioner name, then by output name",
    )
    sensitive_keys: List[str] = Field(
        default_factory=list,
        description="Dot-notation paths of sensitive outputs (stage.key) — values omitted",
    )
    provenance: Dict[str, Any] = Field(
        default_factory=dict,
        description="Traceability: manifest path, completed stages",
    )
