"""Single actor-resolution chain used across strata for "who did this" (ADR-0066, ADR-0067).

Before this module existed, the same environment-variable fallback was duplicated
across lock/build/deploy/workitem/promote code with slightly different variants —
exactly the scattered-implementation problem both ADRs warn about elsewhere. This is
the one place that logic lives now.

Resolution order, highest precedence first:

0. **Control-plane session** (ADR-0067) — if the CLI is authenticated to a strata
   control plane via `IdentityController`, that identity outranks everything else,
   because strata itself performed the authentication rather than merely reading an
   ambient credential.
1. **Cloud provider CLI identity** (ADR-0066) — whichever of `azure_cli` / `aws_cli` /
   `gcloud_cli` is configured and authenticated. Checked in that fixed order when more
   than one is configured.
2. **CI actor environment variables** — `CI_ACTOR`, `GITHUB_ACTOR`, `BUILD_REQUESTEDFOR`
   (Azure DevOps).
3. **OS login** — `$USER` / `%USERNAME%` / `getpass.getuser()`.

Steps 0 and 1 are self-asserted only in the narrow sense that strata does not
independently re-verify them; step 1 was authenticated by the cloud provider itself and
is verifiable against that provider's own audit trail. Steps 2 and 3 are pure claims —
see ADR-0067's "What `actor` resolves from" for the full reasoning.

This lives in ``controllers/`` (not ``utils/``) because it depends on the
``identity_controller``, ``services``, and ``integrations`` layers — per ADR-0003,
``utils/`` must never depend on higher layers.
"""

import getpass
import os
from typing import Any, Optional

from strata.logger import get_logger

logger = get_logger(__name__)


def resolve_actor() -> str:
    """Resolve the current operator identity using the full precedence chain.

    Never raises — always returns a usable string, falling back to ``"unknown"``.
    """
    identity = _resolve_control_plane_identity()
    if identity:
        return identity

    identity = _resolve_cloud_cli_identity()
    if identity:
        return identity

    actor = os.environ.get("CI_ACTOR") or os.environ.get("GITHUB_ACTOR") or os.environ.get("BUILD_REQUESTEDFOR")
    if actor:
        return actor

    return os.environ.get("USER") or os.environ.get("USERNAME") or _safe_getpass() or "unknown"


def _resolve_control_plane_identity() -> Optional[str]:
    """Step 0 — an authenticated `identity`-capable integration session (ADR-0067)."""
    try:
        from strata.controllers.identity_controller import IdentityController

        return IdentityController().get_actor_identity()
    except Exception as exc:
        logger.debug("actor_control_plane_resolution_failed", error=str(exc))
        return None


def _resolve_cloud_cli_identity() -> Optional[str]:
    """Step 1 — whichever configured cloud CLI is authenticated, checked azure/aws/gcloud."""
    try:
        from strata.controllers.integration_lookup import find_available_integration_with_capability
        from strata.models.capabilities import IAWSTool, IAzureTool, IGCloudTool

        for capability, extractor in (
            (IAzureTool, _extract_azure_identity),
            (IAWSTool, _extract_aws_identity),
            (IGCloudTool, _extract_gcloud_identity),
        ):
            integration = find_available_integration_with_capability(capability)
            if integration is None:
                continue
            identity = extractor(integration)
            if identity:
                return identity
    except Exception as exc:
        logger.debug("actor_cloud_cli_resolution_failed", error=str(exc))
    return None


def _extract_azure_identity(integration: Any) -> Optional[str]:
    if not hasattr(integration, "get_signed_in_user"):
        return None
    user = integration.get_signed_in_user()
    return user.get("name") if user else None


def _extract_aws_identity(integration: Any) -> Optional[str]:
    identity = integration.get_identity()
    if not identity:
        return None
    arn = identity.get("Arn", "")
    return arn.rsplit("/", 1)[-1] if arn else None


def _extract_gcloud_identity(integration: Any) -> Optional[str]:
    return integration.get_account()


def _safe_getpass() -> Optional[str]:
    try:
        return getpass.getuser()
    except Exception:
        return None
