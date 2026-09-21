#!/usr/bin/env python3
"""Service for loading and validating DNS zone configuration."""

from strata.models.dns_model import DnsModel
from strata.services.base_service import BaseService


class DnsService(BaseService[DnsModel]):
    """Service for handling DNS zone configuration.

    No Phase 2 dynamic validation yet: checking a record's `${var:KEY}`/
    `${secret:KEY}` token keys against a real Environment (ADR-0002) requires
    the `environment` kind, which doesn't exist in v2 yet. Add
    `_validate_dynamic(environment_model=...)` when it does.
    """

    def _get_model_class(self) -> type[DnsModel]:
        """Return the DnsModel class for validation."""
        return DnsModel
