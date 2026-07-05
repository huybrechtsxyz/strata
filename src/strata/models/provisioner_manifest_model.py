"""Pydantic models for provisioner plugin manifests.

An optional ``provisioner.yaml`` file placed alongside a plugin ``.py`` file
in ``.strata/provisioners/`` provides metadata for ``strata tools status``
and the onboarding guide.
"""

from typing import List, Optional

from pydantic import Field

from strata.models.common_models import PlatformBaseModel, PlatformName


class ProvisionerHealthCheckModel(PlatformBaseModel):
    """Health check configuration for a provisioner plugin."""

    command: str = Field(description="Shell command to verify the tool is available (e.g. 'pulumi version')")
    expected_exit_code: int = Field(default=0, description="Expected exit code for a healthy check")


class ProvisionerManifestModel(PlatformBaseModel):
    """Schema for ``provisioner.yaml`` — optional metadata for provisioner plugins.

    Example::

        name: pulumi
        version: "1.0.0"
        description: "Pulumi IaC provisioner for strata"
        supported_steps:
          - setup
          - plan
          - apply
          - destroy
          - output
        health_check:
          command: "pulumi version"
          expected_exit_code: 0
        requires:
          - pulumi
    """

    name: PlatformName = Field(description="Canonical provisioner name (matches get_deployer_name())")
    version: str = Field(description="Plugin version string")
    description: Optional[str] = Field(default=None, description="Human-readable plugin description")
    supported_steps: Optional[List[str]] = Field(
        default=None,
        description="List of lifecycle step names this provisioner supports",
    )
    health_check: Optional[ProvisionerHealthCheckModel] = Field(
        default=None,
        description="Health check command for verifying tool availability",
    )
    requires: Optional[List[str]] = Field(
        default=None,
        description="External tool binary names required on PATH (e.g. ['pulumi', 'node'])",
    )
