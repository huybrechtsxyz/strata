#!/usr/bin/env python3
"""Pydantic model for policy declarations in configuration.spec.policies."""

from typing import Any, Dict, Optional

from pydantic import Field

from strata.models.common_models import PlatformBaseModel, PlatformName


class PolicyModel(PlatformBaseModel):
    """Declares a single policy evaluated at a given lifecycle phase.

    Example YAML::

        policies:
          - name: zone_enforcement
            type: customer_zone
            phase: plan
            enforcement: deny
            description: "Ensure all planned resources are in customer-allowed zones"
    """

    name: PlatformName
    type: str = Field(..., description="Policy type: customer_zone | required_tags | naming_pattern | script")
    phase: str = Field(..., description="Evaluation phase: validate | build | plan | deploy")
    enforcement: str = Field("deny", description="Enforcement level: deny | warn | audit")
    description: Optional[str] = None
    configuration: Optional[Dict[str, Any]] = None
    enabled: bool = True
