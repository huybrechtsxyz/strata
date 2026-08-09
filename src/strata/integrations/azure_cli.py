"""Azure CLI (`az`) integration — availability, authentication, and subscription context.

This integration serves as the shared foundation for all Azure CLI-based operations:

- **Availability check** — confirms `az` is installed AND authenticated (``az account show``).
  A binary-without-login is useless for any Azure operation.
- **Subscription context** — exposes the active subscription id, name, and tenant.
- **Token helper** — ``get_access_token()`` with in-process caching (avoids repeated
  ``az account get-access-token`` spawns when multiple Azure integrations are active).
- **Bicep version check** — ``bicep_version()`` reports whether the Bicep CLI extension
  is installed alongside Azure CLI.

Not a replacement for ``AzureKeyVaultIntegration`` or ``AzureAppConfigIntegration`` —
those continue to use the REST API directly for secret/variable operations.  This
integration provides the shared *plumbing* (auth verification, subscription info, tokens)
that those integrations and the upcoming Bicep deployer (ADR-0046) need.

Install Azure CLI: https://learn.microsoft.com/en-us/cli/azure/install-azure-cli

Configuration YAML::

    integrations:
      - name: azure
        type: azure_cli
        capabilities: [azure]
        required: true
        validation:
          command: az account show
"""

import json
import re
import threading
from typing import Any, Dict, Optional, Tuple

from strata.integrations.base_integration import BaseIntegration
from strata.logger import get_logger

logger = get_logger(__name__)

# Azure management resource scope — used by Bicep deployer and other ARM callers
_ARM_RESOURCE = "https://management.azure.com"


class AzureCLIIntegration(BaseIntegration):
    """Azure CLI integration — availability, authentication, and subscription context.

    ``ensure_available()`` checks both binary presence and active login.
    Use ``get_subscription()`` to confirm the correct subscription is active.
    Use ``get_access_token(resource)`` for cached bearer tokens.
    """

    COMMAND = "az"
    CAPABILITIES: list = []  # capability name: "azure" (registered in CAPABILITY_MAP)

    # Token cache: {resource_url: token_string} — process-scoped, cleared on login change
    _token_cache: Dict[str, str] = {}
    _token_lock = threading.Lock()

    def get_version_command(self):
        return [self.command, "version"]

    def parse_version(self, version_output: str) -> str:
        """Parse Azure CLI version from ``az version`` JSON output."""
        try:
            data = json.loads(version_output)
            return data.get("azure-cli", version_output.strip())
        except Exception:
            m = re.search(r'"azure-cli":\s*"([^"]+)"', version_output)
            return m.group(1) if m else version_output.strip()

    def get_setup_info(self) -> Dict[str, Any]:
        sub = self._get_subscription_safe()
        status = f"Logged in (subscription: {sub['name']})" if sub else "Not authenticated"
        return {
            "name": "azure_cli",
            "command": "az",
            "install_url": "https://learn.microsoft.com/en-us/cli/azure/install-azure-cli",
            "env_vars": [
                {
                    "name": "AZURE_SUBSCRIPTION_ID",
                    "purpose": "Override active subscription (optional)",
                    "required": False,
                },
                {"name": "AZURE_TENANT_ID", "purpose": "Service principal tenant (SP auth)", "required": False},
                {"name": "AZURE_CLIENT_ID", "purpose": "Service principal client ID (SP auth)", "required": False},
                {"name": "AZURE_CLIENT_SECRET", "purpose": "Service principal secret (SP auth)", "required": False},
            ],
            "auth_methods": [
                {"method": "az login", "description": "Interactive browser login. Preferred for local development."},
                {"method": "Managed Identity", "description": "Automatic when running on Azure compute. No env vars."},
                {
                    "method": "Service principal",
                    "description": "Set AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET.",
                },
                {
                    "method": "OIDC / Workload Identity",
                    "description": "GitHub Actions / Azure Pipelines — omit AZURE_CLIENT_SECRET.",
                },
            ],
            "yaml_example": ("- name: azure\n  type: azure_cli\n  capabilities: [azure]\n  required: true"),
            "info": status,
        }

    # ------------------------------------------------------------------
    # Availability and authentication
    # ------------------------------------------------------------------

    def ensure_available(self) -> Tuple[bool, str]:
        """Check that ``az`` is installed AND authenticated.

        A bare ``az --version`` check is not sufficient — an unauthenticated CLI
        will fail on every Azure operation.  This method runs ``az account show``
        so the Tools view can immediately surface "not logged in" rather than
        showing a misleading green status.

        Returns:
            (True, "") if installed and logged in.
            (False, message) with an actionable error message otherwise.
        """
        if not self.is_available():
            msg = (
                "Azure CLI is not installed or not in PATH. "
                "Install: https://learn.microsoft.com/en-us/cli/azure/install-azure-cli"
            )
            self._info = msg
            return False, msg

        sub = self._get_subscription_safe()
        if sub is None:
            msg = "Azure CLI is installed but not authenticated. Run: az login"
            self._info = msg
            return False, msg

        self._info = f"Logged in — subscription: {sub.get('name', sub.get('id', '?'))}"
        return True, ""

    # ------------------------------------------------------------------
    # Subscription context
    # ------------------------------------------------------------------

    def get_subscription(self) -> Optional[Dict[str, str]]:
        """Return the active subscription metadata from ``az account show``.

        Returns a dict with ``id``, ``name``, ``tenantId``, ``state``, or
        ``None`` if not logged in or the command fails.
        """
        return self._get_subscription_safe()

    def get_signed_in_user(self) -> Optional[Dict[str, str]]:
        """Return the signed-in principal from ``az account show``'s ``user`` field.

        Returns a dict with ``name`` (UPN/email for a user, or the app ID for a
        service principal) and ``type`` (``user`` or ``servicePrincipal``), or
        ``None`` if not logged in or the command fails.

        Used by ``AzureIdentityIntegration`` (ADR-0067) to supply `actor` identity
        when reusing an already-authenticated ``az`` session instead of running a
        separate OIDC login.
        """
        data = self._account_show_raw()
        if data is None:
            return None
        user = data.get("user") or {}
        return {"name": user.get("name", ""), "type": user.get("type", "")}

    def _get_subscription_safe(self) -> Optional[Dict[str, str]]:
        """Return ``id``/``name``/``tenantId``/``state`` from ``az account show``, or None."""
        data = self._account_show_raw()
        if data is None:
            return None
        return {
            "id": data.get("id", ""),
            "name": data.get("name", ""),
            "tenantId": data.get("tenantId", ""),
            "state": data.get("state", ""),
        }

    def _account_show_raw(self) -> Optional[Dict[str, Any]]:
        """Run ``az account show --output json``; return the parsed dict or None."""
        try:
            result = self._run_integration(["account", "show", "--output", "json"], timeout=15)
            if result.returncode != 0 or not result.stdout:
                return None
            return json.loads(result.stdout)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Token helper
    # ------------------------------------------------------------------

    def get_access_token(self, resource: str = _ARM_RESOURCE) -> Optional[str]:
        """Return a cached bearer token for the given Azure resource scope.

        Uses ``az account get-access-token --resource <resource>``.
        Tokens are cached in-process until the integration instance is replaced.
        Pass ``resource="https://vault.azure.net"`` for Key Vault,
        ``resource="https://management.azure.com"`` for ARM / Bicep.

        Returns the token string, or ``None`` if not authenticated or az fails.
        """
        with self._token_lock:
            if resource in self._token_cache:
                return self._token_cache[resource]

        try:
            result = self._run_integration(
                ["account", "get-access-token", "--resource", resource, "--output", "json"],
                timeout=30,
            )
            if result.returncode != 0 or not result.stdout:
                return None
            data = json.loads(result.stdout)
            token = data.get("accessToken")
            if token:
                with self._token_lock:
                    self._token_cache[resource] = token
            return token
        except Exception as exc:
            logger.debug("az_get_access_token_failed", resource=resource, error=str(exc))
            return None

    def clear_token_cache(self) -> None:
        """Clear the in-process token cache (call after ``az login`` or subscription switch)."""
        with self._token_lock:
            self._token_cache.clear()

    # ------------------------------------------------------------------
    # Bicep extension check
    # ------------------------------------------------------------------

    def bicep_version(self) -> Optional[str]:
        """Return the installed Bicep extension version, or ``None`` if not installed.

        Bicep is a separate extension to Azure CLI, installed via ``az bicep install``.
        """
        try:
            result = self._run_integration(["bicep", "version"], timeout=15)
            if result.returncode != 0:
                return None
            # Output: "Bicep CLI version 0.28.1 (xxxxx)"
            m = re.search(r"(\d+\.\d+\.\d+)", result.stdout)
            return m.group(1) if m else result.stdout.strip()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Convenience: run arbitrary az subcommands
    # ------------------------------------------------------------------

    def run_az(self, args, timeout: int = 120):
        """Run an arbitrary ``az`` subcommand and return the CommandResult.

        Callers (e.g. BicepDeployer) use this to execute ``az deployment group create``
        and similar commands through the authenticated integration instance.
        """
        return self._run_integration(args, timeout=timeout)
