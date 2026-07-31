"""Gate configuration models for deployment workflow orchestration — ADR-0057 / ADR-0059."""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Literal, Optional, Union

from pydantic import Field

from strata.models.common_models import PlatformBaseModel


class ApproverType(str, Enum):
    """Supported approver identity types."""

    GITHUB_TEAM = "github-team"
    ADO_GROUP = "ado-group"
    USER = "user"


class ApproverRef(PlatformBaseModel):
    """A single named approver entry."""

    type: ApproverType = Field(description="Approver identity type: github-team | ado-group | user")
    value: str = Field(description="Approver identifier — team slug, group name, or user address")


class GateWhenConditionsModel(PlatformBaseModel):
    """Condition clauses for a gate's `when:` field.

    Each field accepts an operator + value expression: ">= 1000", ">= 1", etc.
    All non-None conditions must ALL be true for the gate to trigger.
    """

    cost_delta_monthly: Optional[str] = Field(
        None,
        description='Trigger when monthly cost delta matches expression (e.g. ">= 1000").',
        examples=[">= 1000", "> 500"],
    )
    cve_critical: Optional[str] = Field(
        None,
        description='Trigger when count of critical CVEs matches expression (e.g. ">= 1").',
        examples=[">= 1"],
    )
    cve_high: Optional[str] = Field(
        None,
        description='Trigger when count of high CVEs matches expression (e.g. ">= 5").',
        examples=[">= 5"],
    )
    ai_risk: Optional[str] = Field(
        None,
        description='Trigger when AI risk level matches expression (e.g. ">= high").',
        examples=[">= high", ">= critical"],
    )
    time_utc: Optional[str] = Field(
        None,
        description='Only trigger outside the given UTC deployment window (e.g. "02:00-04:00").',
        examples=["02:00-04:00", "01:00-05:00"],
    )


class DeploymentGateModel(PlatformBaseModel):
    """A single hand-off gate declared in a deployment's ``spec.gates`` list.

    Absorbs both ADR-0057's enforcing gate mechanism and the old ``spec.approvals``
    audit-only metadata (ADR-0059) into one schema — see ``mode`` below.
    """

    name: str = Field(
        description="Unique gate name within this deployment's spec.gates list. "
        "Used as the merge key when a child deployment's `extends` overrides a base "
        "deployment's gate — identical semantics to `stages[].name`.",
        examples=["prod-approval", "cost-guard"],
    )
    type: str = Field(
        description="Gate type: approval | cost_review | security_review | verify | scheduled | incident | cab",
        examples=["approval", "cost_review", "security_review", "verify", "scheduled"],
    )
    mode: Literal["declare", "enforce"] = Field(
        default="enforce",
        description=(
            "'enforce' — strata creates a real WorkItem, pauses the deploy, and requires "
            "--resume (exit code 5). 'declare' — strata only records this gate in the audit "
            "trail; it never blocks. Use 'declare' when enforcement already happens "
            "externally (e.g. Azure DevOps environment approvals, GitHub Actions protection rules)."
        ),
    )
    scope: Union[Literal["all"], List[str]] = Field(
        default="all",
        description="Which stage(s) this gate applies to: a list of stage names, or 'all'.",
        examples=["all", ["production"]],
    )
    when: Union[Literal["always"], GateWhenConditionsModel] = Field(
        default="always",
        description='Condition to trigger the gate: "always" or a conditions object.',
        examples=["always", {"cost_delta_monthly": ">= 1000"}],
    )
    approvers: Optional[Dict[str, ApproverRef]] = Field(
        None,
        description="Named approver entries required to resolve this gate. Key is a short "
        "identifier (e.g. used in audit logs); value is a typed approver reference.",
        examples=[{"ops-team": {"type": "github-team", "value": "org/ops-team"}}],
    )
    min_approvals: int = Field(
        default=1,
        description="Minimum number of approvers required.",
        ge=1,
    )
    timeout_minutes: Optional[int] = Field(
        None,
        description="Minutes before the work item expires. None = no expiry.",
        ge=1,
        examples=[60, 240, 480],
    )
    auto_resolve: bool = Field(
        default=False,
        description="Automatically resolve the gate without human action (used with type: scheduled).",
    )
    description: Optional[str] = Field(
        None,
        description="Human-readable note shown in the work-item context.",
    )
