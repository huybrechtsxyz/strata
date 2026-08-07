"""Controller owning every client-side identity concern (ADR-0067).

The single place `IdentityController` owns:
- resolving which `identity`-capable integration to use (via `IntegrationService`)
- the lazy login trigger — no dedicated `strata login` command
- supplying the authenticated identity to `actor` resolution (ADR-0066)

Token persistence and the OIDC mechanics themselves live on each identity-provider
integration (see `strata.integrations.identity`) — this controller never talks to an
identity provider directly, it only orchestrates.
"""

from typing import Any, Dict, Optional, Tuple

from strata.controllers.base_controller import BaseController


class IdentityController(BaseController):
    """Resolves, logs in, and supplies identity for `identity`-capable integrations."""

    def _get_integration(self, name: Optional[str] = None):
        """Resolve an `identity`-capable integration by name, or the first one declared."""
        from strata.integrations.capabilities import IIdentityProvider
        from strata.services.integration_service import IntegrationService

        svc = IntegrationService.get_instance()
        if not svc.is_initialized():
            svc.initialize_integrations()

        if name:
            integration = svc.get_integration(name)
            return integration

        names = svc.get_integrations_with_capability(IIdentityProvider)
        for candidate in names:
            integration = svc.get_integration(candidate)
            if integration is not None:
                return integration
        return None

    def ensure_logged_in(self, name: Optional[str] = None) -> Tuple[bool, str]:
        """Check for a valid session and lazily log in if none exists.

        This is the mechanism every control-plane-touching command calls instead of
        requiring a separate login step first.
        """
        integration = self._get_integration(name)
        if integration is None:
            return False, "No 'identity'-capable integration configured."

        ok, detail = integration.check_auth()
        if ok:
            return True, detail

        ok, detail = integration.login()
        return ok, detail

    def get_token(self, name: Optional[str] = None) -> Optional[str]:
        """Return a valid bearer token, logging in lazily if needed."""
        integration = self._get_integration(name)
        if integration is None:
            return None

        ok, _ = self.ensure_logged_in(name)
        if not ok:
            return None
        return integration.get_access_token()

    def get_actor_identity(self, name: Optional[str] = None) -> Optional[str]:
        """Return the authenticated session's identity for ADR-0066 `actor` resolution.

        Returns None if no `identity`-capable integration is configured or logged in —
        callers fall back to ADR-0066's CLI-side resolution chain in that case.
        """
        integration = self._get_integration(name)
        if integration is None or not hasattr(integration, "get_identity_claims"):
            return None

        ok, _ = integration.check_auth()
        if not ok:
            return None

        claims: Optional[Dict[str, Any]] = integration.get_identity_claims()
        if not claims:
            return None
        return claims.get("email") or claims.get("preferred_username") or claims.get("sub")
