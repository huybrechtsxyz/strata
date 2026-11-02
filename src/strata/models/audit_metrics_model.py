#!/usr/bin/env python3
"""The deployment metrics record — `_metrics.json` (docs/design/audit-trail.md's
"A third file — `_metrics.json`, adapting v1's never-built ADR-0064").

Phase 2 of that doc's Layer 2 Implementation Plan. Same non-discoverable,
written-artifact status as `audit_manifest_model.py` — no `apiVersion`/
`kind`/`meta` envelope.

The one rule everything else here follows — the self-containment invariant:

    A deployment record may contain only facts observable from inside the
    deploying process.

No scanning deployment history, no computing aggregates (frequency, failure
rate, MTTR) at deploy time — a CI runner starts from a fresh checkout with
no local history to scan, and change failure rate/MTTR are structurally
uncomputable at deploy time anyway (MTTR needs a *future* recovery event
that hasn't happened yet). Aggregation is a downstream consumer's job,
deliberately not built here.

Scope is `deploy`/`destroy` only — never `build` (a repeated development-loop
action with no gates/approvals/lock/resource changes) and never a dry run
(nothing was actually changed).
"""

from enum import Enum
from typing import Any

from pydantic import Field, model_validator

from strata.models.common_models import PlatformBaseModel, PlatformName


class ErrorCategory(str, Enum):
    """A bounded, redacted classification of failure — never raw error text.

    Raw error text stays exclusively in `_manifest.json`; this file is
    designed to be shipped to third-party platforms (SIEM, Grafana, Datadog)
    more liberally than the manifest, so it needs a stricter no-leak bar.
    """

    AUTH = "auth"
    QUOTA = "quota"
    TIMEOUT = "timeout"
    CONFLICT = "conflict"
    POLICY = "policy"
    NETWORK = "network"
    STATE_LOCK = "state_lock"
    UNKNOWN = "unknown"


class MetricsDimensionsModel(PlatformBaseModel):
    """What you slice by.

    `outcome`, not `status` — deliberately distinct from "change failure
    rate", which strata cannot determine (that needs history this record
    structurally does not carry).
    """

    outcome: str = Field(description="Execution-level success/failure: 'success' | 'failed'")
    action: str = Field(description="'deploy' | 'destroy' — never 'build', never a dry run")
    environment: str | None = Field(default=None, description="Environment label")
    tenant: str | None = Field(default=None, description="Tenant name, when applicable")
    actor: str | None = Field(default=None, description="Resolved actor identity — unbounded, see label_safe")
    commit_sha: str | None = Field(default=None, description="Git commit SHA — unbounded, see label_safe")


class MetricsMeasuresModel(PlatformBaseModel):
    """What you aggregate."""

    duration_seconds: float = Field(description="Total wall-clock duration — the 'runtime' ask")
    stages_total: int = Field(description="Total number of stages executed")
    stages_failed: int = Field(description="Number of stages that failed")
    resource_changes: int | None = Field(default=None, description="Resource change count, when available")
    policy_checks_total: int | None = Field(default=None, description="Total policy/gate checks evaluated")
    policy_checks_failed: int | None = Field(default=None, description="Policy/gate checks that failed")


class MetricsSectionModel(PlatformBaseModel):
    """A conditional group (cost, SBOM/CVE, drift) that may not have run for a given deploy.

    `measured` is explicit rather than inferred from `data` being present —
    a plain `null` cannot distinguish "not measured" from "measured as
    zero", and that ambiguity silently corrupts any average built on top.
    """

    measured: bool = Field(default=False, description="Whether this section actually ran for this deployment")
    data: dict[str, Any] | None = Field(default=None, description="Section-specific fields, when measured")


class DeploymentMetricsModel(PlatformBaseModel):
    """`_metrics.json` — the manifest's lean, redacted, more-shippable sibling."""

    execution_id: str = Field(description="Same execution_id as the peer _manifest.json")
    deployment: PlatformName = Field(description="Deployment document name")
    workspace: PlatformName = Field(description="Workspace document name")
    timestamp: str = Field(description="ISO-8601 timestamp — the run's started_at")

    dimensions: MetricsDimensionsModel = Field(description="What this record can be sliced by")
    measures: MetricsMeasuresModel = Field(description="What this record can be aggregated on")
    sections: dict[str, MetricsSectionModel] | None = Field(
        default=None, description="Conditional groups keyed by name, e.g. 'cost', 'sbom_cve', 'drift'"
    )

    error_category: ErrorCategory | None = Field(
        default=None, description="Bounded failure classification — set only when outcome is 'failed'"
    )
    label_safe: list[str] = Field(
        default_factory=lambda: ["outcome", "action", "environment", "tenant"],
        description="Names of dimension keys with bounded cardinality — safe to promote to labels in a "
        "dimensional system (Prometheus, etc.). Excludes actor/commit_sha/execution_id, which are "
        "legitimate record attributes but unbounded.",
    )

    @model_validator(mode="after")
    def validate_error_category_matches_outcome(self) -> "DeploymentMetricsModel":
        """error_category is meaningful only for a failed outcome — never a stray leftover."""
        if self.error_category is not None and self.dimensions.outcome != "failed":
            raise ValueError("error_category may only be set when dimensions.outcome is 'failed'.")
        return self
