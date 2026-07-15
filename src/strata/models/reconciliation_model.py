"""Reconciliation result model for GitOps controller health queries."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class ReconciliationResult:
    """Reconciliation status returned by a GitOps controller health query.

    Populated by ``ArgocdDeployer.health()`` / ``FluxDeployer.health()`` and
    consumed by ``strata deploy health`` to report sync state.

    Fields:
        sync_status:       Controller sync state — ``Synced``, ``OutOfSync``, or ``Unknown``.
        health_status:     Application health — ``Healthy``, ``Degraded``, ``Progressing``,
                           ``Suspended``, or ``Unknown``.
        last_synced_at:    UTC timestamp of the controller's last successful reconciliation,
                           or ``None`` if the controller has not synced yet.
        revision:          Git SHA the controller last reconciled to, or ``None`` if unknown.
        intended_revision: Git SHA strata committed during ``strata deploy run`` (the expected
                           state). Used to compute ``drift``.
        drift:             ``True`` when ``revision != intended_revision``, indicating the
                           controller has not yet applied the latest commit.
        message:           Human-readable status message from the controller, or ``None``.
    """

    sync_status: str
    health_status: str
    last_synced_at: Optional[datetime]
    revision: Optional[str]
    intended_revision: str
    drift: bool
    message: Optional[str]
