"""Shared "find an available sibling integration by capability" helper (ADR-0003).

Several places need to find whichever configured integration implements a given
capability protocol and is currently available (``azure_cli``, ``aws_cli``,
``gcloud_cli``, ...): actor resolution (ADR-0066) and identity-provider CLI-reuse
paths (ADR-0067) both do the same "loop over integrations with this capability,
return the first one whose ``ensure_available()`` succeeds" lookup.

This lives in ``controllers/`` (not ``utils/`` or ``integrations/``) because it
depends on ``services.integration_service`` — per ADR-0003, neither ``utils/`` nor
``integrations/`` may depend on ``services/``. Identity-provider integrations that
need this lookup receive it as an injected callback (see
``IdentityController._get_integration``) rather than importing this module
themselves, so ``integrations/`` stays free of a ``services/`` dependency.
"""

from typing import Any, Optional, Type

from strata.logger import get_logger

logger = get_logger(__name__)


def find_available_integration_with_capability(capability: Type) -> Optional[Any]:
    """Return the first configured *capability*-implementing integration that is available.

    Never raises — returns ``None`` on any lookup failure.
    """
    try:
        from strata.services.integration_service import IntegrationService

        svc = IntegrationService.get_instance()
        if not svc.is_initialized():
            svc.initialize_integrations()
        ok, errors = svc.validate_required_integrations(capabilities={capability})
        if not ok:
            logger.debug(
                "required_integration_unavailable",
                capability=getattr(capability, "__name__", str(capability)),
                errors=errors,
            )

        for name in svc.get_integrations_with_capability(capability):
            integration = svc.get_integration(name)
            if integration is None:
                continue
            ok, _ = integration.ensure_available()
            if ok:
                return integration
    except Exception as exc:
        logger.debug(
            "sibling_integration_lookup_failed",
            capability=getattr(capability, "__name__", str(capability)),
            error=str(exc),
        )
    return None
