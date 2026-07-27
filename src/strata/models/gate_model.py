"""Gate configuration models for deployment workflow orchestration — ADR-0057."""

from __future__ import annotations

from typing import List, Literal, Optional, Union

from pydantic import Field

from strata.models.common_models import PlatformBaseModel


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
    """A single hand-off gate declared in an environment's spec.gates list."""

    type: str = Field(
        description="Gate type: approval | cost_review | security_review | verify | scheduled | incident | cab",
        examples=["approval", "cost_review", "security_review", "verify", "scheduled"],
    )
    when: Union[Literal["always"], GateWhenConditionsModel] = Field(
        default="always",
        description='Condition to trigger the gate: "always" or a conditions object.',
        examples=["always", {"cost_delta_monthly": ">= 1000"}],
    )
    approvers: Optional[List[str]] = Field(
        None,
        description="List of approver groups, emails, or git usernames required to resolve this gate.",
        examples=[["ops-team"], ["alice@example.com", "security-team"]],
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
