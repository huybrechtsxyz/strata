#!/usr/bin/env python3
"""Pydantic model for policy declarations in configuration.spec.policies."""

from typing import Any, Dict, Optional

from pydantic import Field

from strata.models.common_models import PlatformBaseModel, PlatformName
from strata.models.missing_data_model import MissingDataPolicy


class PolicyModel(PlatformBaseModel):
    """Declares a single policy evaluated at a given lifecycle phase.

    Example YAML::

        policies:
          - name: zone_enforcement
            type: tenant_zone
            phase: plan
            enforcement: deny
            description: "Ensure all planned resources are in tenant-allowed zones"
    """

    name: PlatformName
    type: str = Field(
        ...,
        description="Policy type: tenant_zone | required_labels | naming_pattern | resource_type_restrictions | script | cve_max_severity | cost_threshold | path_convention | layer_agreement | checkov | opa | change_reference_required",
    )
    phase: str = Field(
        ..., description="Evaluation phase: validate | build | plan | deploy | deploy_before | destroy_before"
    )
    enforcement: str = Field("deny", description="Enforcement level: deny | warn | audit")
    description: Optional[str] = None
    configuration: Optional[Dict[str, Any]] = None
    enabled: bool = True
    on_missing_data: MissingDataPolicy = Field(
        MissingDataPolicy.SKIP,
        description=(
            "How this policy behaves when its required input data was never produced "
            "(as opposed to simply not applying in this context): skip (default, pass "
            "silently) | warn (pass, but surface a visible warning) | block (treat as "
            "a violation, enforced per `enforcement`). See ADR-0082."
        ),
    )
