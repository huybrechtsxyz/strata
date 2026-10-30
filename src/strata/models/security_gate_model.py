#!/usr/bin/env python3
"""Security gate policies (docs/design/security-gates.md D3, section 6.1).

Vendor-neutral on purpose: each policy names the Integration document that answers it, and every
vendor-specific option (Aikido's `fail_on`, image naming, ...) lives on that document instead
(D4). Placed on the Workspace beside `cve_policy` and `checkov_policy`, as workspace-scoped
guardrails; unlike those two they run at deploy preflight, because they need the network and
resolved credentials (ADR-0022 D4).
"""

from typing import Annotated, ClassVar, Literal

from pydantic import Field, model_validator

from strata.models.common_models import PlatformBaseModel, PlatformKind, PlatformName
from strata.models.reference_fields import References


class ReleaseGatePolicyModel(PlatformBaseModel):
    """Gate a deploy on a platform's verdict for the solution repository at the deployed commit."""

    schema_rules: ClassVar[tuple[str, ...]] = ("`poll_interval` must be smaller than `timeout`.",)

    integration: Annotated[PlatformName, References(PlatformKind.INTEGRATION)] = Field(
        description="Integration document declaring the 'release_gate' capability that gives the verdict"
    )
    repository: str = Field(min_length=1, description="The repository as the platform knows it (id or name)")
    enforcement: Literal["deny", "warn"] = Field(
        default="deny", description="'deny' stops the deploy on a failed gate; 'warn' reports and continues"
    )
    on_missing_data: Literal["skip", "warn", "block"] = Field(
        default="block",
        description="When no verdict can be obtained (no credential or commit, outage, timeout). 'block' by "
        "default, as cve_policy: 'skip' would make 'never checked' look the same as 'checked, clean'.",
    )
    timeout: int = Field(default=900, ge=30, le=7200, description="Total seconds to wait for the verdict")
    poll_interval: int = Field(default=10, ge=2, le=300, description="Seconds between checks while waiting")
    commit: str | None = Field(
        None,
        pattern=r"^[0-9a-fA-F]{40}$",
        description="Pin the commit to judge; normally resolved from CI variables or git (design D6)",
    )

    @model_validator(mode="after")
    def validate_poll_interval_below_timeout(self) -> "ReleaseGatePolicyModel":
        if self.poll_interval >= self.timeout:
            raise ValueError("poll_interval must be smaller than timeout")
        return self


class ImageGatePolicyModel(PlatformBaseModel):
    """Gate a deploy on a platform's open issues for the container images in `sbom.json`."""

    integration: Annotated[PlatformName, References(PlatformKind.INTEGRATION)] = Field(
        description="Integration document declaring the 'image_findings' capability that lists the issues"
    )
    enforcement: Literal["deny", "warn"] = Field(
        default="deny", description="'deny' stops the deploy on a breach; 'warn' reports and continues"
    )
    on_missing_data: Literal["skip", "warn", "block"] = Field(
        default="block",
        description="When the issues cannot be obtained (no sbom.json or credential, outage). 'block' by "
        "default, for the same reason as release_gate_policy.",
    )
    max_severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"] = Field(
        default="HIGH",
        description="Issues at or above this severity count against max_count (same vocabulary as cve_policy)",
    )
    max_count: int = Field(default=0, ge=0, description="Counted issues allowed across all images")
    on_unknown_image: Literal["skip", "warn", "block"] = Field(
        default="warn", description="When the platform does not know an image strata is about to deploy"
    )
