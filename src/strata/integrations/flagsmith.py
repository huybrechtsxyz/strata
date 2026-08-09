"""Flagsmith integration for feature flag management (BSD-3-Clause)."""

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from strata.integrations.store_integration import StoreIntegration
from strata.logger import get_logger
from strata.models.capabilities import IFeatureStore, IVariableStore
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
    CAPABILITIES = [IFeatureStore, IVariableStore]

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

        # Optional: management API key for write operations (feature flag creation/toggle)
        self.management_key = self._get_env_var(self._get_management_key_var_name())

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

    def _get_management_key_var_name(self) -> str:
        """Return the env-var name holding the optional Flagsmith management API key."""
        return "FLAGSMITH_MANAGEMENT_KEY"

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
                {
                    "name": "FLAGSMITH_MANAGEMENT_KEY",
                    "purpose": "Management API key for write operations (optional)",
                    "required": False,
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
        info["has_management_key"] = bool(self.management_key)
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
        Create or verify a feature flag in Flagsmith (create-if-not-exists semantics).

        Implements IFeatureStore interface.

        Feature flags that do not yet exist in Flagsmith must be created manually in
        the Flagsmith dashboard or via the management API.  This method returns
        ``True`` when the flag already exists (the intended "seed-on-missing" contract
        for Flagsmith is to verify presence, not to toggle state) and ``False`` when
        the flag is absent.

        Args:
            key: Feature flag name
            value: Ignored — existing flags are never modified

        Returns:
            True if the flag exists in Flagsmith, False if not found or on error
        """
        available, error = self.ensure_available()
        if not available:
            logger.warning("Cannot set feature in Flagsmith", error=error, name=self.integration_name)
            return False

        flags = self._fetch_flags(refresh=True)
        for flag in flags:
            if flag.get("feature", {}).get("name") == key:
                logger.info(
                    "Feature flag already exists in Flagsmith — skipping write",
                    name=self.integration_name,
                    key=key,
                )
                return True

        logger.warning(
            "Feature flag not found in Flagsmith — create it in the Flagsmith dashboard first",
            name=self.integration_name,
            key=key,
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

    def _management_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Api-Key {self.management_key}",
            "Content-Type": "application/json",
        }

    # Unified Store Interface Implementation (IVariableStore)

    def get_variable(self, key: str, **kwargs) -> Optional[Any]:
        """
        Get a trait value for a Flagsmith identity.

        Implements IVariableStore interface.  Flagsmith traits are per-identity
        key-value pairs used for remote configuration.

        Args:
            key: Trait key
            **kwargs: identity (Flagsmith identity identifier, defaults to ``"default"``),
                      refresh (bool)

        Returns:
            Trait value, or None if not found
        """
        identity = kwargs.get("identity", "default")
        refresh = kwargs.get("refresh", False)

        available, error = self.ensure_available()
        if not available:
            logger.warning("Cannot retrieve variable from Flagsmith", error=error, name=self.integration_name)
            return None

        traits = self._fetch_traits(identity=identity, refresh=refresh)
        for trait in traits:
            if trait.get("trait_key") == key:
                return trait.get("trait_value")

        logger.debug("Trait not found in Flagsmith", key=key, identity=identity, name=self.integration_name)
        return None

    def set_variable(self, key: str, value: Any, **kwargs) -> bool:
        """
        Set a trait for a Flagsmith identity (create-if-not-exists semantics).

        Implements IVariableStore interface.  Writes identity traits via the Flagsmith
        traits API.  Requires a server-side (not client-side) environment API key.
        Never overwrites an existing trait — if the key already exists the method
        returns ``True`` without writing.

        Args:
            key: Trait key
            value: Trait value (coerced to str)
            **kwargs: identity (Flagsmith identity identifier, defaults to ``"default"``)

        Returns:
            True if the trait exists (created now or already present), False on failure
        """
        identity = kwargs.get("identity", "default")

        available, error = self.ensure_available()
        if not available:
            logger.warning("Cannot write variable to Flagsmith", error=error, name=self.integration_name)
            return False

        # Check existence first — never overwrite
        existing = self.get_variable(key, identity=identity, refresh=True)
        if existing is not None:
            logger.info(
                "Trait already exists in Flagsmith — skipping write",
                name=self.integration_name,
                key=key,
                identity=identity,
            )
            return True

        logger.debug("Writing trait to Flagsmith", name=self.integration_name, key=key, identity=identity)

        ok = self._set_trait_via_api(identity, key, str(value))
        if ok:
            logger.info("Trait written to Flagsmith", name=self.integration_name, key=key, identity=identity)
        else:
            logger.warning("Failed to write trait to Flagsmith", name=self.integration_name, key=key, identity=identity)
        return ok

    def list_variables(self, prefix: str = "", **kwargs) -> List[str]:
        """
        List trait keys for a Flagsmith identity.

        Implements IVariableStore interface.

        Args:
            prefix: Optional key prefix filter
            **kwargs: identity (Flagsmith identity identifier, defaults to ``"default"``),
                      refresh (bool)

        Returns:
            List of trait keys
        """
        identity = kwargs.get("identity", "default")
        refresh = kwargs.get("refresh", False)

        available, _ = self.ensure_available()
        if not available:
            return []

        traits = self._fetch_traits(identity=identity, refresh=refresh)
        keys = [t.get("trait_key", "") for t in traits if "trait_key" in t]
        return [k for k in keys if k.startswith(prefix)] if prefix else keys

    def _fetch_traits(self, identity: str = "default", refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Fetch all traits for a Flagsmith identity.

        Args:
            identity: Flagsmith identity identifier
            refresh: If True, bypass any internal cache

        Returns:
            List of trait objects (each has ``trait_key`` and ``trait_value``)
        """
        try:
            url = f"{self.flagsmith_addr}/api/v1/identities/?identifier={urllib.parse.quote(identity)}"
            req = urllib.request.Request(url, method="GET")
            for k, v in self._api_headers().items():
                req.add_header(k, v)

            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            return data.get("traits", []) if isinstance(data, dict) else []
        except Exception as e:
            logger.debug(
                "Flagsmith API trait fetch failed",
                error_type=type(e).__name__,
                name=self.integration_name,
            )
            return []

    def _set_trait_via_api(self, identity: str, key: str, value: str) -> bool:
        """
        Write a trait for a Flagsmith identity via the traits API.

        Requires a server-side environment API key; client-side (browser) keys
        are read-only for trait writes.

        Args:
            identity: Flagsmith identity identifier
            key: Trait key
            value: Trait value string

        Returns:
            True on HTTP 200/201, False on any error
        """
        try:
            url = f"{self.flagsmith_addr}/api/v1/traits/"
            body = json.dumps({"identity": {"identifier": identity}, "trait_key": key, "trait_value": value}).encode(
                "utf-8"
            )
            req = urllib.request.Request(url, data=body, method="POST")
            for k, v in self._api_headers().items():
                req.add_header(k, v)

            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status in (200, 201)
        except Exception:
            return False

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
