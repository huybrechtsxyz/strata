#!/usr/bin/env python3
"""Provisioner lock policy (docs/design/provisioner-lock-policy.md).

An opt-in workspace guard rail: every Terraform/OpenTofu provisioner must ship a `.terraform.lock.hcl` that locks
every provider it declares. The rule reads only the build path, so there is no `on_missing_data`.
"""

from datetime import date
from typing import Annotated, ClassVar, Literal

from pydantic import Field, field_validator, model_validator

from strata.models.common_models import PlatformBaseModel, expires_date_text


class ProvisionerLockAllowEntryModel(PlatformBaseModel):
    """One provisioner exempt from the provisioner lock policy. `provisioner` and `reason` are required; add
    `expires` (an ISO date) to stop honouring the entry after that day.

    Design notes:
    Same shape as `ImageTagAllowEntryModel`: an exemption is a reviewed decision, so the reason is schema.
    """

    provisioner: str = Field(min_length=1, description="The provisioner to exempt, by its name in `provisioners`")
    reason: str = Field(min_length=1, description="Why this provisioner may ship without a complete lock file")
    expires: str | None = Field(None, description="ISO date after which this entry is ignored (e.g. '2026-12-31')")

    @field_validator("expires", mode="before")
    @classmethod
    def accept_an_unquoted_yaml_date(cls, value: object) -> object:
        return expires_date_text(value)

    @field_validator("expires")
    @classmethod
    def validate_expires_is_iso_date(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"expires '{value}' is not a valid ISO date (e.g. '2026-12-31'): {exc}") from exc
        return value


class ProvisionerLockPolicyModel(PlatformBaseModel):
    """Fails `build run` when a Terraform or OpenTofu provisioner has no `.terraform.lock.hcl`, or one that does
    not lock every provider its source declares. Without the lock file those providers are listed in the SBOM
    without a version, so a scanner cannot check them. `provisioner_lock_policy: {}` is already a useful policy.

    Design notes:
    `WorkspaceSpecModel.provisioner_lock_policy`, evaluated over the build path after the SBOM
    (docs/design/provisioner-lock-policy.md). Without the policy `build run` only prints an `info` line.
    """

    schema_rules: ClassVar[tuple[str, ...]] = ("Provisioners in `allow` must be unique.",)

    enforcement: Literal["deny", "warn"] = Field(
        default="deny", description="'deny' stops the run on a breach; 'warn' reports and continues"
    )
    rings: list[Annotated[str, Field(min_length=1)]] | None = Field(
        None,
        min_length=1,
        description="Apply the policy only to deployments whose version document is in one of these rings "
        "(`spec.promotion.ring`). Omit to apply it to every deployment. A deployment with no ring is always "
        "evaluated.",
    )
    allow: list[ProvisionerLockAllowEntryModel] | None = Field(
        None, description="Provisioners exempt from the policy, each with a reason and an optional expiry"
    )

    @model_validator(mode="after")
    def validate_unique_allow_provisioners(self) -> "ProvisionerLockPolicyModel":
        """Two entries for one provisioner would make the reason and expiry ambiguous."""
        names = [entry.provisioner for entry in self.allow or []]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"allow has more than one entry for: {', '.join(duplicates)}")
        return self
