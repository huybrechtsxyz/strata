#!/usr/bin/env python3
"""The deployment metrics record — `_metrics.json` (docs/design/audit-trail.md's
"The audit files").

Same non-discoverable,
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
from typing import Literal

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
    #: Every step applied, but a verify check failed: the workload, not the deploy, is the problem.
    VERIFICATION = "verification"
    UNKNOWN = "unknown"


#: Whether the run's verify checks said the deployment works (docs/design/post-deploy-verification.md, D9): the
#: manifest's `verified` as one bounded word, for the metrics, the events and the handover document.
Verification = Literal["verified", "verification_failed", "not_verified"]


def verification_state(verified: bool | None) -> Verification:
    """`verified` (every check that ran answered pass or warn), `verification_failed` (one failed or could not
    answer) or `not_verified` (no check ran: none declared, or `--no-verify`)."""
    if verified is None:
        return "not_verified"
    return "verified" if verified else "verification_failed"


class MetricsDimensionsModel(PlatformBaseModel):
    """What you slice by.

    `outcome`, not `status` — deliberately distinct from "change failure
    rate", which strata cannot determine (that needs history this record
    structurally does not carry).
    """

    outcome: str = Field(
        description="Execution-level success/failure: 'success' | 'failed'; a 'partial' manifest is 'failed' here"
    )
    action: str = Field(description="'deploy' | 'destroy' — never 'build', never a dry run")
    environment: str | None = Field(default=None, description="Environment label")
    tenant: str | None = Field(default=None, description="Tenant name, when applicable")
    actor: str | None = Field(default=None, description="Resolved actor identity — unbounded, see label_safe")
    commit_sha: str | None = Field(default=None, description="Git commit SHA — unbounded, see label_safe")
    verification: Verification | None = Field(
        default=None,
        description="'verified' | 'verification_failed' | 'not_verified': what the run's verify checks said "
        "(`verification_state()`); left out of records written before it existed",
    )


class MetricsMeasuresModel(PlatformBaseModel):
    """What you aggregate."""

    duration_seconds: float = Field(description="Total wall-clock duration — the 'runtime' ask")
    stages_total: int = Field(description="Number of stages that ran (not skipped)")
    stages_failed: int = Field(description="Number of stages that failed")
    # No `resource_changes`: only Terraform's plan summary could give it (docs/design/audit-trail.md, History).
    policy_checks_total: int | None = Field(default=None, description="Total policy/gate checks evaluated")
    policy_checks_failed: int | None = Field(
        default=None, description="Policy/gate checks that did not pass, whatever their enforcement (deny, warn, audit)"
    )
    checks_total: int | None = Field(
        default=None,
        description="Verify checks that ran, the stages' and the deployment's; left out when none ran",
    )
    checks_failed: int | None = Field(
        default=None,
        description="Verify checks that failed or could not answer, whatever their enforcement; left out when none ran",
    )


class DeploymentMetricsModel(PlatformBaseModel):
    """`_metrics.json` — the manifest's lean, redacted, more-shippable sibling."""

    execution_id: str = Field(description="Same execution_id as the peer _manifest.json")
    deployment: PlatformName = Field(description="Deployment document name")
    workspace: PlatformName = Field(description="Workspace document name")
    timestamp: str = Field(description="ISO-8601 timestamp — the run's started_at")

    dimensions: MetricsDimensionsModel = Field(description="What this record can be sliced by")
    measures: MetricsMeasuresModel = Field(description="What this record can be aggregated on")
    # No `sections` (cost, CVE, drift): nothing measures them. A measure left out means "not measured".

    error_category: ErrorCategory | None = Field(
        default=None,
        description="Bounded failure classification — set only when outcome is 'failed' "
        "(`audit_error_category.error_category()`)",
    )
    label_safe: list[str] = Field(
        default_factory=lambda: ["outcome", "action", "environment", "tenant", "verification"],
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
