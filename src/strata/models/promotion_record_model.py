#!/usr/bin/env python3
"""Pydantic models for promotion-record (kind: promotion-record) and the local activity log."""

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from strata.models.common_models import PlatformBaseModel, PlatformKind, PlatformVersion


class PromotionOutcome(str, Enum):
    """Possible outcomes for a promotion record."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    ROLLED_BACK = "rolled-back"


# ─── Promotion record sub-models ──────────────────────────────────────────────


class PromotionRecordTargetModel(PlatformBaseModel):
    """The artifact being promoted."""

    type: Literal["remote", "helm_chart", "image"] = Field(
        description="Artifact type: 'remote' | 'helm_chart' | 'image'"
    )
    name: str = Field(description="Remote name, module name, or service name")
    from_version: Optional[str] = Field(None, description="Version before this promotion")
    to_version: str = Field(description="Version set by this promotion")


class PromotionGateResultModel(PlatformBaseModel):
    """One gate evaluation result captured in the promotion record."""

    gate: str = Field(description="Gate name, e.g. 'require_progression_order'")
    ring: str = Field(description="Ring that was checked")
    require: Optional[str] = Field(None, description="Quorum policy: 'any_one' | 'all'")
    checked_at: str = Field(description="ISO 8601 UTC timestamp")
    passed: bool = Field(description="Whether the gate passed")
    detail: Optional[str] = Field(None, description="Human-readable explanation")


class PromotionCommitModel(PlatformBaseModel):
    """A single git commit made during the promotion."""

    ring_wave: int = Field(description="Ring wave number (1-based)")
    sha: str = Field(description="Git commit SHA")
    message: str = Field(description="Commit message")
    committed_at: str = Field(description="ISO 8601 UTC timestamp")


class PromotionRingWaveSummaryModel(PlatformBaseModel):
    """Summary of one ring wave execution."""

    ring_wave: int = Field(description="Ring wave number (1-based)")
    environments: List[str] = Field(description="Environment names targeted in this wave")
    deployment_wave: Optional[str] = Field(None, description="Deployment wave name ('canary', 'all', etc.)")
    deployments: Any = Field(
        description="Deployment names in this wave, or the string 'all'"
    )
    files_modified: Optional[List[str]] = Field(None, description="Files written/updated")
    fields_removed: Optional[List[str]] = Field(None, description="Fields/overlays removed (final wave cleanup)")
    committed_at: Optional[str] = Field(None, description="ISO 8601 UTC timestamp of the wave commit")


class PromotionRecordSpecModel(PlatformBaseModel):
    """Specification for a promotion-record document."""

    # What was promoted
    target: PromotionRecordTargetModel = Field(description="The artifact that was promoted")

    # How it was promoted
    strategy: str = Field(description="Strategy name")
    progression: str = Field(description="Progression name")
    rings: List[str] = Field(description="Full ordered ring list from the progression")

    # Outcome
    outcome: PromotionOutcome = Field(description="completed | partial | rolled-back")
    rollback_of: Optional[str] = Field(None, description="Name of the promotion-record this reverses (rollbacks only)")

    # Identity & timing
    initiated_by: str = Field(description="$USER or CI actor who ran strata promote start")
    hostname: str = Field(description="Machine that ran strata")
    started_at: str = Field(description="ISO 8601 UTC timestamp of first wave commit")
    completed_at: Optional[str] = Field(None, description="ISO 8601 UTC timestamp of last wave commit")
    duration_seconds: Optional[int] = Field(None, description="Calendar time first→last commit in seconds")

    # Git
    branch: str = Field(description="Git branch name (promote/{target}-{version}-{ring})")
    commits: List[PromotionCommitModel] = Field(default_factory=list, description="Per-wave commit records")

    # Gate results (compliance evidence)
    gates: List[PromotionGateResultModel] = Field(default_factory=list, description="Gate evaluation results")

    # Wave execution summary
    ring_waves: List[PromotionRingWaveSummaryModel] = Field(default_factory=list, description="Per-wave summaries")

    # Links to deployment manifests (populated later by strata deploy run)
    deployment_manifests: Optional[List[str]] = Field(
        None,
        description="Paths to deployment manifests written by subsequent strata deploy run invocations",
    )


class PromotionRecordMetaModel(PlatformBaseModel):
    """Metadata for a promotion-record document."""

    name: str = Field(description="Record name, e.g. prom-20260623-prd-001")
    labels: Optional[Dict[str, Any]] = Field(None, description="Labels for filtering (target, ring, outcome)")
    annotations: Optional[Dict[str, Any]] = Field(None, description="Free-form annotations")


class PromotionRecordModel(PlatformBaseModel):
    """Root model for kind: promotion-record.

    Written by ``strata promote start`` on the last ring wave, and by
    ``strata promote rollback`` when rollback edits are committed.
    Stored in ``.strata/promotions/records/`` locally.

    Example::

        apiVersion: strata.huybrechts.xyz/v1
        kind: promotion-record
        meta:
          name: prom-20260623-prd-001
          labels:
            target: tf_landscape
            ring: prd
            outcome: completed
        spec:
          target:
            type: remote
            name: tf_landscape
            from_version: v2.3.0
            to_version: v2.4.0
          strategy: infra-cautious
          ...
    """

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.PROMOTION_RECORD,
        frozen=True,
        description="Always 'promotion-record'",
    )
    meta: PromotionRecordMetaModel
    spec: PromotionRecordSpecModel


# ─── Activity log models ──────────────────────────────────────────────────────


class ActivityLogEventModel(PlatformBaseModel):
    """A single timestamped event appended to the promotion activity log.

    ``action`` values: start, gate_passed, gate_failed, branch_created,
    committed, completed, rolled_back.
    """

    timestamp: str = Field(description="ISO 8601 UTC timestamp")
    action: str = Field(description="Event type")
    ring_wave: Optional[int] = Field(None, description="Ring wave number when relevant")
    environments: Optional[List[str]] = Field(None, description="Environments targeted")
    deployment_wave: Optional[str] = Field(None, description="Deployment wave name")
    initiated_by: Optional[str] = Field(None, description="User identity")
    deployments: Optional[Any] = Field(None, description="Deployment names or 'all'")
    files_modified: Optional[List[str]] = Field(None, description="Files written")
    fields_removed: Optional[List[str]] = Field(None, description="Overlays/fields removed")
    gate: Optional[str] = Field(None, description="Gate name (gate_passed/gate_failed events)")
    detail: Optional[str] = Field(None, description="Human-readable detail")
    branch: Optional[str] = Field(None, description="Branch name (branch_created event)")
    commit: Optional[str] = Field(None, description="Commit SHA (committed event)")
    outcome: Optional[str] = Field(None, description="Final outcome (completed/rolled_back events)")


class ActivityLogModel(PlatformBaseModel):
    """Local diagnostic activity log for one in-flight promotion.

    Stored at ``.strata/promotions/{target}-{version}-{ring}.yaml``.
    Gitignored — not required for promotion to function; all state
    derivable from version-lock files and git history.
    """

    target: str = Field(description="Target name (remote, module, etc.)")
    version: str = Field(description="Target version being promoted")
    previous_version: Optional[str] = Field(None, description="Version before this promotion")
    ring: str = Field(description="Destination ring")
    environments: List[str] = Field(description="All environments in this ring")
    strategy: str = Field(description="Strategy name")
    progression: str = Field(description="Progression name")
    rings: List[str] = Field(description="All rings in the progression (ordered)")
    branch: Optional[str] = Field(None, description="Git branch created for this promotion")
    events: List[ActivityLogEventModel] = Field(default_factory=list, description="Ordered event log")
