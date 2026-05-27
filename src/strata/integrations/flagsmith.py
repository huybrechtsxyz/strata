"""Flagsmith integration for feature flag management (BSD-3-Clause)."""

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from strata.integrations.capabilities import IFeatureStore
from strata.integrations.store_integration import StoreIntegration
from strata.logger import get_logger
from strata.models.integration_model import IntegrationModel

logger = get_logger(__name__)


class FlagsmithIntegration(StoreIntegration):
    """
    Flagsmith integration for feature flag management.

    Flagsmith is an open-source feature flag platform (BSD-3-Clause) that
    supports both SaaS (https://edge.api.flagsmith.com) and self-hosted
    deployments. This integration is API-only — there is no CLI binary.

    Authentication is via an environment API key sent as the
    X-Environment-Key header.

    https://flagsmith.com
    """

    # No widely-used CLI binary; is_available() is overridden below
    COMMAND = "flagsmith"

    # Declare supported capabilities
    CAPABILITIES = [IFeatureStore]

    @classmethod
    def _get_instance_key_static(cls, class_ref, *args, **kwargs) -> str:
        """Get instance key based on Flagsmith server address."""
        config = kwargs.get("config") or (args[0] if args else None)
        if not config:
            return "default"
        addr = ""
        if config.endpoints and config.endpoints.address:
            addr = config.endpoints.address.rstrip("/")
        return addr or config.name or "default"

    def __init__(self, config: IntegrationModel):
        """
        Initialize Flagsmith integration.

        Args:
            config: Integration configuration model
        """
        super().__init__(config)

        # Server address
        self.flagsmith_addr = "https://edge.api.flagsmith.com"
        if self.config.endpoints and self.config.endpoints.address:
            self.flagsmith_addr = self._resolve_env_vars(self.config.endpoints.address).rstrip("/")

        # Auth: environment API key
        self.env_key = self._get_env_var(self._get_env_key_var_name())

        # Per-instance flag cache (refreshed each call to _fetch_flags)
        self._flags_cache: Optional[List[Dict[str, Any]]] = None

        logger.debug(
            "Flagsmith integration initialized",
            name=self.integration_name,
            address=self.flagsmith_addr,
            has_key=bool(self.env_key),
        )

    # Auth helpers

    def _get_env_key_var_name(self) -> str:
        """Return the env-var name holding the Flagsmith environment API key."""
        auth = self.config.authentication
        if auth and auth.method == "api_key" and auth.api_key:
            return auth.api_key.api_key or "FLAGSMITH_ENVIRONMENT_KEY"
        return "FLAGSMITH_ENVIRONMENT_KEY"

    # Base integration overrides

    def is_available(self, use_cache: bool = True) -> bool:
        """
        Check whether Flagsmith is available by probing the flags API endpoint.

        Overrides the base implementation which checks for a CLI binary — there
        is no widely-used Flagsmith CLI binary, so we check HTTP reachability
        instead.

        Returns:
            True if the API endpoint responds with HTTP 2xx, False otherwise
        """
        if not self.env_key:
            return False
        try:
            url = f"{self.flagsmith_addr}/api/v1/flags/"
            req = urllib.request.Request(url, method="GET")
            req.add_header("X-Environment-Key", self.env_key)
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def get_version_command(self) -> List[str]:
        """Return a placeholder version command (no real CLI exists)."""
        return [self.command, "--version"]

    def get_version(self, use_cache: bool = True) -> Optional[str]:
        """Return a placeholder version string (API-only, no binary version)."""
        return "api"

    def parse_version(self, version_output: str) -> str:
        """Parse a version string from flagsmith output (placeholder)."""
        match = re.search(r"(\d+\.\d+\.\d+)", version_output)
        return match.group(1) if match else "api"

    def get_setup_info(self) -> dict:
        """Return setup metadata for Flagsmith."""
        return {
            "name": "flagsmith",
            "command": "flagsmith (API-only — no CLI binary required)",
            "install_url": "https://flagsmith.com/docs/deployment/overview",
            "env_vars": [
                {
                    "name": "FLAGSMITH_ENVIRONMENT_KEY",
                    "purpose": "Environment API key (X-Environment-Key header)",
                    "required": True,
                },
            ],
            "auth_methods": [
                {
                    "method": "Environment API key",
                    "description": "Set FLAGSMITH_ENVIRONMENT_KEY to the environment key from the Flagsmith dashboard.",
                },
            ],
            "yaml_example": ("type: flagsmith\nspec:\n  endpoints:\n    address: https://edge.api.flagsmith.com"),
        }

    def ensure_available(self) -> Tuple[bool, str]:
        """
        Ensure the Flagsmith environment key is configured and the API is reachable.

        Returns:
            Tuple of (success, error_message)
        """
        if not self.env_key:
            self._info = f"{self.integration_name} environment key not configured."
            return (
                False,
                f"{self.integration_name} environment key not configured. Set FLAGSMITH_ENVIRONMENT_KEY.",
            )

        if not self.is_available():
            self._info = f"{self.integration_name} API is not reachable at {self.flagsmith_addr}."
            return (
                False,
                f"{self.integration_name} API is not reachable at {self.flagsmith_addr}. "
                "Check FLAGSMITH_ENVIRONMENT_KEY and network connectivity.",
            )

        self._info = f"{self.integration_name} is available"
        logger.debug("Flagsmith is available", name=self.integration_name, address=self.flagsmith_addr)
        return True, ""

    def get_info(self) -> Dict[str, Any]:
        """Return integration information including configuration status."""
        info = super().get_info()
        info["flagsmith_addr"] = self.flagsmith_addr
        info["has_env_key"] = bool(self.env_key)
        return info

    # Unified Store Interface Implementation (IFeatureStore)

    def get_feature(self, key: str, **kwargs) -> Optional[Any]:
        """
        Get a feature flag value from Flagsmith.

        Implements IFeatureStore interface.

        Args:
            key: Feature flag name
            **kwargs: identity (optional Flagsmith identity key), refresh (bool)

        Returns:
            Feature flag enabled state (bool), or feature value string if set,
            or None if the flag is not found
        """
        identity = kwargs.get("identity")
        refresh = kwargs.get("refresh", False)

        available, error = self.ensure_available()
        if not available:
            logger.warning(
                "Cannot retrieve feature from Flagsmith",
                error=error,
                name=self.integration_name,
            )
            return None

        flags = self._fetch_flags(identity=identity, refresh=refresh)
        for flag in flags:
            feature = flag.get("feature", {})
            if feature.get("name") == key:
                value = flag.get("feature_state_value")
                if value is not None:
                    return value
                return flag.get("enabled", False)

        logger.debug("Feature flag not found in Flagsmith", key=key, name=self.integration_name)
        return None

    def set_feature(self, key: str, value: Any, **kwargs) -> bool:
        """
        Set a feature flag value in Flagsmith.

        Implements IFeatureStore interface.

        Note:
            Environment API keys are read-only for remote evaluation.
            Modifying flags requires a server-side key and the management API.
            This method logs a warning and returns False.

        Args:
            key: Feature flag name
            value: Feature flag value

        Returns:
            Always False (not supported via environment key)
        """
        logger.warning(
            "Flagsmith does not support setting feature flags via the environment API key. "
            "Use the management API with a server-side key.",
            key=key,
            name=self.integration_name,
        )
        return False

    def list_features(self, prefix: str = "", **kwargs) -> List[str]:
        """
        List feature flag names from Flagsmith.

        Implements IFeatureStore interface.

        Args:
            prefix: Optional name prefix filter
            **kwargs: identity, refresh

        Returns:
            List of feature flag names
        """
        identity = kwargs.get("identity")
        refresh = kwargs.get("refresh", False)

        available, _ = self.ensure_available()
        if not available:
            return []

        flags = self._fetch_flags(identity=identity, refresh=refresh)
        names = [f["feature"]["name"] for f in flags if "feature" in f]
        return [n for n in names if n.startswith(prefix)] if prefix else names

    # API helpers

    def _api_headers(self) -> Dict[str, str]:
        return {
            "X-Environment-Key": self.env_key or "",
            "Content-Type": "application/json",
        }

    def _fetch_flags(self, identity: Optional[str] = None, refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Fetch all flags from the Flagsmith API, using a per-instance cache.

        Args:
            identity: Optional Flagsmith identity key for identity-based evaluation
            refresh: If True, bypass the cache and re-fetch

        Returns:
            List of flag objects from the Flagsmith API
        """
        if not refresh and self._flags_cache is not None:
            return self._flags_cache

        try:
            if identity:
                url = f"{self.flagsmith_addr}/api/v1/identities/?identifier={urllib.parse.quote(identity)}"
            else:
                url = f"{self.flagsmith_addr}/api/v1/flags/"

            req = urllib.request.Request(url, method="GET")
            for k, v in self._api_headers().items():
                req.add_header(k, v)

            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            # Identities endpoint wraps flags under "flags" key
            flags = data.get("flags", data) if isinstance(data, dict) else data
            if isinstance(flags, list):
                self._flags_cache = flags
                return flags

            return []
        except Exception as e:
            logger.debug(
                "Flagsmith API flag fetch failed",
                error_type=type(e).__name__,
                name=self.integration_name,
            )
            return []
