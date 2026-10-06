#!/usr/bin/env python3
"""The deployment manifest — `_manifest.json` (docs/design/audit-trail.md's "The audit files").

Phase 2 of that doc's Layer 2 Implementation Plan. This is a **written
artifact**, not a discoverable `(kind, meta.name)` document — `.strata/`
(where it lives, via `layout.audit_dir()`) is permanently excluded from
solution discovery (`layout.DEFAULT_IGNORED_DIRS`), so this model carries
no `apiVersion`/`kind`/`meta` envelope, just the record itself.

Merges v1's real deploy-log and deployment-manifest models into one record,
keeping the richer side wherever the two disagreed on granularity — see the
design doc's "The audit files" table for the full field-by-field rationale.
Two structural decisions worth restating here because they change the
shape, not just the docs:

- `artifacts.platform` is a **reference** (`hash` + `path`), never an embed —
  v1's `ManifestPlatformModel.content: dict | None` (the full embedded
  `platform.json`) is dropped. Keeps every satellite artifact
  (platform/sbom/outputs) the same shape: a hash the manifest vouches for,
  a path a reader follows.
- No persisted `_event.json` — `render_manifest_event()`/
  `render_metrics_event()` (`controllers/audit_event_rendering.py`) compute
  the CloudEvents+ECS envelope fresh at dispatch time instead.
"""

from typing import Any, Literal

from pydantic import Field, model_validator

from strata.models.common_models import PlatformBaseModel, PlatformName


class ChangeReferenceModel(PlatformBaseModel):
    """Reference to an external change/ticket record justifying a deployment.

    The same instance is shared, unmodified, by the manifest and (once
    rendered) the audit event — ported directly from v1's real
    `models/change_reference_model.py`. `system` is an open string, not an
    enum: teams with internal/unsupported trackers must be able to use this
    without a strata release.

    **Three separate identities, not two.** `deployed_by`
    (`DeploymentManifestModel`, who *ran* the deploy) and `supplied_by`
    (who *typed* this reference — usually the same actor, auto-derived
    from `resolve_actor()`) both already existed; `approved_by`/
    `approved_at` close the real gap docs/design/audit-trail.md's
    compliance analysis flagged for Q3 ("who approved") — self-attestation
    by the executor is not independent authorization under ISO 27001
    A.12.1.2. **Honesty limit, stated directly rather than implied:**
    `approved_by`/`approved_at` are operator-supplied (`deploy run
    --change-approved-by`/`--change-approved-at`), the same as `reason` —
    not independently verified against the tracker, and `approved_at`
    deliberately does NOT auto-derive from the run's own clock the way
    `supplied_at` does, since the approval happened earlier, in the
    external system. A future tracker-integration lookup (fetching a real
    approval record by `system`+`id`) would be a strictly stronger
    replacement for operator-supplied values — not built here, since no
    real consumer's tracker integration exists yet to build it against.
    """

    system: str = Field(
        description="Tracker identifier, e.g. 'jira', 'azure_devops', 'servicenow', or an internal name"
    )
    id: str = Field(description="Change/ticket identifier in the tracker, e.g. 'OPS-1234'")
    reason: str = Field(description="Operator-supplied justification for this deployment")
    classification: str | None = Field(default=None, description="Change classification, e.g. 'emergency'/'normal'")
    title: str | None = Field(default=None, description="Snapshot of the record's title at invocation time")
    url: str | None = Field(default=None, description="Link to the change record")
    supplied_by: str = Field(description="Actor who supplied this reference")
    supplied_at: str = Field(description="ISO-8601 timestamp when this reference was supplied")
    approved_by: str | None = Field(
        default=None,
        description="Who approved this change in the tracker — distinct from supplied_by (who typed this "
        "reference) and deployed_by (who ran the deploy). Operator-supplied, not independently verified.",
    )
    approved_at: str | None = Field(
        default=None,
        description="ISO-8601 timestamp of the approval itself (in the tracker), not of this CLI invocation — "
        "operator-supplied, never auto-derived from the run's own clock.",
    )

    @model_validator(mode="after")
    def validate_approval_supplied_together(self) -> "ChangeReferenceModel":
        """`approved_by`/`approved_at` are a pair — one without the other is
        half an attestation, the same "supplied together or not at all"
        discipline `deploy_command.py` already enforces for `system`/`id`/
        `reason`."""
        if (self.approved_by is None) != (self.approved_at is None):
            raise ValueError("approved_by and approved_at must be supplied together, or not at all.")
        return self


class ManifestPullRequestModel(PlatformBaseModel):
    """PR evidence, extracted by a future `gh`-based enrichment step (Layer 1's
    unbuilt half) — modeled now so the manifest shape is stable ahead of it.
    """

    number: int = Field(description="PR number")
    title: str | None = Field(default=None, description="PR title at extraction time")
    url: str | None = Field(default=None, description="Link to the PR")
    author: str | None = Field(default=None, description="PR author")
    merged_by: str | None = Field(default=None, description="Who merged the PR")
    merged_at: str | None = Field(default=None, description="ISO-8601 merge timestamp")
    labels: list[str] | None = Field(default=None, description="PR labels at extraction time")
    files_changed: int | None = Field(default=None, description="Number of files changed in the PR")
    approvers: list[str] | None = Field(
        default=None, description="Reviewers who approved — modeled but not populated by the current extraction"
    )


class ManifestPlatformReferenceModel(PlatformBaseModel):
    """Hash + path reference to the build's platform.json — never embedded content."""

    hash: str = Field(description="SHA-256 hash of platform.json")
    path: str = Field(description="Relative path to platform.json in the build output")


class ManifestRepositoryModel(PlatformBaseModel):
    """Pinned version of a single source repository at deploy time."""

    url: str | None = Field(default=None, description="Git remote URL")
    ref: str | None = Field(default=None, description="Requested git ref (tag, branch, or commit)")
    commit: str | None = Field(default=None, description="Resolved full commit SHA")


class ManifestImageModel(PlatformBaseModel):
    """A container image used during the deployment."""

    name: str = Field(description="Service or component name")
    image: str = Field(description="Full image reference including tag")
    digest: str | None = Field(default=None, description="Image content digest (sha256:...), when available")


class ManifestChartModel(PlatformBaseModel):
    """A Helm/ArgoCD chart used during the deployment — the effective,
    pin-resolved chart version, not the module's own bare declaration."""

    name: str = Field(description="Module document name")
    chart: str = Field(description="Chart name (SourceModel.chart_name)")
    version: str | None = Field(default=None, description="Effective chart version, when known")


class ManifestProviderModel(PlatformBaseModel):
    """Provisioner entry used in the deployment — tool, state backend, type-specific details."""

    name: str = Field(description="Provisioner name as defined in the workspace")
    type: str = Field(description="IaC tool: terraform | ansible | compose | helm | script")
    backend: dict[str, Any] | None = Field(default=None, description="State backend config, when stateful")
    details: dict[str, Any] | None = Field(default=None, description="Type-specific metadata")


class ManifestArtifactsModel(PlatformBaseModel):
    """The artifact bill of materials for the deployment."""

    platform: ManifestPlatformReferenceModel = Field(description="Platform artifact hash + path reference")
    repositories: dict[str, ManifestRepositoryModel] | None = Field(
        default=None, description="Pinned source repository versions keyed by repository name"
    )
    images: list[ManifestImageModel] | None = Field(default=None, description="Container images used")
    charts: list[ManifestChartModel] | None = Field(default=None, description="Helm/ArgoCD charts used")
    providers: list[ManifestProviderModel] | None = Field(default=None, description="Provisioners invoked")


class ManifestStepModel(PlatformBaseModel):
    """One step within a stage — deploy-log's richer per-step shape, kept over the
    manifest's own coarser (names-only) `steps[]`."""

    step: str = Field(description="Step name, e.g. 'init', 'plan', 'apply'")
    success: bool = Field(description="Whether this step succeeded")
    duration_seconds: float | None = Field(default=None, description="Wall-clock duration in seconds")


class ManifestOutputsReferenceModel(PlatformBaseModel):
    """Reference to a durable outputs artifact — path + hash, never embedded values."""

    path: str = Field(description="Relative path to the outputs artifact JSON file")
    sha256: str = Field(description="SHA-256 hash of the outputs artifact")


class ManifestStageModel(PlatformBaseModel):
    """Result of a single deployment stage execution."""

    name: PlatformName = Field(description="Stage name")
    provisioner: str | None = Field(default=None, description="Provisioner used")
    topology: str | None = Field(default=None, description="Topology name, if topology-based")
    status: Literal["success", "failed", "skipped"] = Field(description="Stage outcome")
    skip_reason: str | None = Field(
        default=None, description="Why the stage was skipped — set only when status is 'skipped'"
    )
    started_at: str | None = Field(default=None, description="ISO-8601 start timestamp")
    completed_at: str | None = Field(default=None, description="ISO-8601 completion timestamp")
    duration_seconds: float | None = Field(default=None, description="Wall-clock duration in seconds")
    steps: list[ManifestStepModel] | None = Field(default=None, description="Per-step outcome and timing")
    outputs: dict[str, Any] | None = Field(default=None, description="Non-sensitive outputs collected from the stage")
    outputs_artifact: ManifestOutputsReferenceModel | None = Field(
        default=None, description="Reference to the durable outputs artifact for this stage"
    )
    errors: list[str] | None = Field(default=None, description="Error messages if the stage failed")
    warnings: list[str] | None = Field(default=None, description="Non-fatal warnings recorded for this stage")
    messages: list[str] | None = Field(default=None, description="Informational messages recorded for this stage")


class ManifestSbomReferenceModel(PlatformBaseModel):
    """Reference to the generated CycloneDX SBOM — path + hash, never embedded content."""

    path: str = Field(description="Relative path to the SBOM file")
    format: str = Field(description="SBOM format, e.g. 'cyclonedx-json'")
    sha256: str = Field(description="SHA-256 hash of the SBOM file")
    component_count: int | None = Field(default=None, description="Number of components in the SBOM")


class ManifestPolicyResultModel(PlatformBaseModel):
    """Result of a single policy evaluation recorded in the manifest."""

    policy_name: str = Field(description="Policy name as declared in configuration")
    policy_type: str = Field(description="Policy type, e.g. tenant_zone, required_labels, naming_pattern")
    phase: str = Field(description="Phase when evaluated: validate | build | plan | deploy")
    enforcement: str = Field(description="Enforcement level: deny | warn | audit")
    passed: bool = Field(description="Whether the policy passed")
    violations: list[str] = Field(default_factory=list, description="Violation messages when the policy failed")


class ManifestLockReferenceModel(PlatformBaseModel):
    """State lock audit trail — which backend held the lock, who, when."""

    lock_id: str = Field(description="Unique lock identifier")
    backend: str = Field(description="Lock backend type: azurerm | terraform_cloud | s3 | consul | gcs | local")
    acquired_at: str = Field(description="ISO-8601 timestamp when the lock was acquired")
    released_at: str | None = Field(default=None, description="ISO-8601 timestamp when the lock was released")
    holder: str = Field(description="Identity of the lock holder")
    hostname: str = Field(description="Hostname of the machine that acquired the lock")


class DeploymentManifestModel(PlatformBaseModel):
    """`_manifest.json` — the one substantive persisted audit record for a run."""

    # Identity
    execution_id: str = Field(description="Fresh UUID4, fixed at the top of the run")
    action: Literal["build", "deploy", "destroy"] = Field(description="Action performed")
    version: str | None = Field(default=None, description="strata CLI version that produced this record")
    deployment: PlatformName = Field(description="Deployment document name")
    workspace: PlatformName = Field(description="Workspace document name")
    environment: str | None = Field(default=None, description="Environment label")
    file: str | None = Field(default=None, description="Path to the deployment file used, if applicable")

    # Timing — started_at fixed at the top of the run, never changes once set
    started_at: str = Field(description="ISO-8601 timestamp when the run started")
    completed_at: str | None = Field(default=None, description="ISO-8601 timestamp when the run completed")
    duration_seconds: float | None = Field(default=None, description="Total wall-clock duration in seconds")

    # Outcome — tri-state, not a bare bool: 'partial' is real signal a bool can't carry
    status: Literal["success", "partial", "failed"] = Field(description="Overall outcome")

    # Flags
    force: bool = Field(default=False, description="Whether --force was passed")
    dry_run: bool = Field(default=False, description="Whether this was a dry run")

    # Actor + git context — actor kept distinct from commit_author (who wrote the code
    # is not who ran the deploy)
    deployed_by: str | None = Field(default=None, description="Resolved actor identity")
    commit_sha: str | None = Field(default=None, description="Git commit SHA at deploy time")
    commit_message: str | None = Field(default=None, description="Git commit message")
    commit_author: str | None = Field(default=None, description="Git commit author")

    # Layer 1 evidence
    pull_request: ManifestPullRequestModel | None = Field(default=None, description="PR evidence, when extracted")
    change_reference: ChangeReferenceModel | None = Field(
        default=None, description="External change/ticket record, when supplied"
    )

    # Artifacts (BOM)
    artifacts: ManifestArtifactsModel = Field(description="Complete artifact bill of materials")

    # Stages
    stages: list[ManifestStageModel] | None = Field(default=None, description="Results of each deployment stage")

    # Satellite references — reference-only pattern, applied consistently
    sbom: ManifestSbomReferenceModel | None = Field(default=None, description="Reference to the generated SBOM")
    outputs: ManifestOutputsReferenceModel | None = Field(
        default=None, description="Reference to the run's durable outputs artifact"
    )
    signatures: dict[str, Any] | None = Field(default=None, description="Signing/attestation data, when configured")
    policy_results: list[ManifestPolicyResultModel] | None = Field(
        default=None, description="Policy evaluation results from all phases run during this deployment"
    )
    lock: ManifestLockReferenceModel | None = Field(default=None, description="State lock audit trail")

    # Catch-alls
    errors: list[str] | None = Field(default=None, description="Run-level error messages")
    messages: list[str] | None = Field(default=None, description="Run-level informational messages")
    metadata: dict[str, Any] | None = Field(default=None, description="Free-form additional data")
