"""Pydantic models for the deployment manifest — the auditable output of a deploy run.

The deployment manifest captures **what was deployed**: repo versions, commits,
stage results, and outputs.  It is written automatically by the deploy command
on completion (success or failure) and stored in ``.strata/deployments/``.

YAML on-disk format::

    apiVersion: strata.huybrechts.xyz/v1
    kind: deployment-manifest
    meta:
      name: prod_deployment
      labels:
        version: "2.3.0"
        environment: production
    spec:
      deployment_name: prod_deployment
      workspace_name: my_workspace
      action: deploy
      status: success
      ...
"""

from typing import Any, Dict, List, Optional

from pydantic import Field

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
)

# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class ManifestPlatformModel(PlatformBaseModel):
    """Fingerprint of the platform.json artifact that was deployed."""

    hash: str = Field(description="SHA-256 hash of the platform.json file")
    path: Optional[str] = Field(None, description="Relative path to platform.json in the build output")


class ManifestRepositoryModel(PlatformBaseModel):
    """Pinned version of a single source repository at deploy time."""

    url: Optional[str] = Field(None, description="Git remote URL")
    ref: Optional[str] = Field(None, description="Requested git ref (tag, branch, or commit)")
    commit: Optional[str] = Field(None, description="Resolved full commit SHA")


class ManifestStageModel(PlatformBaseModel):
    """Result of a single deployment stage execution."""

    name: PlatformName = Field(description="Stage name")
    provisioner: Optional[str] = Field(None, description="Provisioner used (e.g. tf_hetzner)")
    topology: Optional[str] = Field(None, description="Topology name (if topology-based)")
    status: str = Field(description="Outcome: success | failed | skipped")
    started_at: Optional[str] = Field(None, description="ISO-8601 start timestamp")
    completed_at: Optional[str] = Field(None, description="ISO-8601 completion timestamp")
    duration_seconds: Optional[int] = Field(None, description="Wall-clock duration in seconds")
    steps: Optional[List[str]] = Field(None, description="Steps executed (e.g. [setup, check, plan, apply])")
    outputs: Optional[Dict[str, Any]] = Field(None, description="Non-sensitive outputs collected from the stage")
    error: Optional[str] = Field(None, description="Error message if the stage failed")


# ---------------------------------------------------------------------------
# Meta model
# ---------------------------------------------------------------------------


class DeploymentManifestMetaModel(PlatformBaseModel):
    """Metadata for a deployment manifest."""

    name: PlatformName = Field(description="Manifest name (derived from deployment name)")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: Optional[List[Any]] = Field(None, description="Optional tags (list of values for categorization)")


# ---------------------------------------------------------------------------
# Spec model
# ---------------------------------------------------------------------------


class DeploymentManifestSpecModel(PlatformBaseModel):
    """Full specification of a deployment manifest."""

    # Identity
    deployment_name: PlatformName = Field(description="Name of the deployment definition used")
    workspace_name: PlatformName = Field(description="Name of the workspace that was deployed")
    environment: Optional[str] = Field(None, description="Environment label (e.g. production, staging)")

    # Action & timing
    action: str = Field(description="Action performed: deploy | destroy")
    started_at: str = Field(description="ISO-8601 timestamp when the deploy started")
    completed_at: Optional[str] = Field(None, description="ISO-8601 timestamp when the deploy completed")
    duration_seconds: Optional[int] = Field(None, description="Total wall-clock duration in seconds")
    status: str = Field(description="Overall outcome: success | partial | failed")
    dry_run: bool = Field(default=False, description="Whether this was a dry-run (no changes applied)")

    # Actor
    deployed_by: Optional[str] = Field(
        None, description="Identity of who performed the deploy ($USER, $GITHUB_ACTOR, etc.)"
    )

    # Platform artifact fingerprint
    platform: ManifestPlatformModel = Field(description="Fingerprint of the platform.json used")

    # Repository bill of materials
    repositories: Optional[Dict[str, ManifestRepositoryModel]] = Field(
        None, description="Pinned repository versions keyed by repository name"
    )

    # Stage results
    stages: Optional[List[ManifestStageModel]] = Field(None, description="Results of each deployment stage")

    # Future extension points
    sbom: Optional[Dict[str, Any]] = Field(None, description="Software bill of materials (future)")
    signatures: Optional[Dict[str, Any]] = Field(None, description="Signing/attestation data (future)")


# ---------------------------------------------------------------------------
# Root model
# ---------------------------------------------------------------------------


class DeploymentManifestModel(PlatformBaseModel):
    """Root model for a deployment manifest file.

    Written by the deploy command after each deploy run (success or failure).
    Captures the full bill of materials: platform artifact hash, repository
    versions, stage results, and outputs.
    """

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version for platform configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.DEPLOYMENT_MANIFEST,
        frozen=True,
        description="Platform kind (always 'deployment-manifest')",
    )
    meta: DeploymentManifestMetaModel = Field(description="Manifest metadata (name, labels, tags, annotations)")
    spec: DeploymentManifestSpecModel = Field(description="Full deployment manifest specification")
