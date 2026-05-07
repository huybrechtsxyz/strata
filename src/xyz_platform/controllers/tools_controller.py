"""Controller for inspecting external tool integration status and setup info."""

from __future__ import annotations

from xyz_platform.controllers.base_controller import BaseController
from xyz_platform.integrations.factory import IntegrationFactory
from xyz_platform.logger import get_logger

logger = get_logger(__name__)

# Friendly type strings supported by create_by_type()
_BUILTIN_TYPES = [
    "git",
    "terraform",
    "docker",
    "bitwarden",
    "hashicorp_vault",
    "hashicorp_consul",
    "azure_keyvault",
    "azure_appconfig",
]


class ToolsController(BaseController):
    """Lists and checks the status of registered integrations."""

    def status(self) -> tuple[bool, list[dict], list[str]]:
        """
        Return availability status for all known integrations.

        Returns:
            Tuple of (success, rows, errors).
            Each row: {
                "name": str,
                "available": bool,
                "version": Optional[str],
                "capabilities": list[str],
                "command": Optional[str],
            }
        """
        rows = []
        for type_str in _BUILTIN_TYPES:
            try:
                integration = IntegrationFactory.create_by_type(type_str)
                available = integration.is_available()
                version = integration.get_version() if available else None
                caps = [c.__name__ for c in (integration.CAPABILITIES if hasattr(integration, "CAPABILITIES") else [])]
                command = getattr(integration, "command", None)
                rows.append(
                    {
                        "name": type_str,
                        "available": available,
                        "version": version,
                        "capabilities": caps,
                        "command": command,
                    }
                )
            except Exception as exc:
                logger.warning("Failed to probe integration", integration=type_str, error=str(exc))
                rows.append(
                    {
                        "name": type_str,
                        "available": False,
                        "version": None,
                        "capabilities": [],
                        "command": None,
                    }
                )
        return True, rows, []

    def check(self, name: str) -> tuple[bool, dict, list[str]]:
        """
        Deep-check a single integration by type name.

        Returns:
            Tuple of (success, detail_dict, errors).
            detail_dict includes setup_info + runtime availability + version.
        """
        if name not in _BUILTIN_TYPES:
            return False, {}, [f"Unknown integration: '{name}'. Known: {', '.join(_BUILTIN_TYPES)}"]

        try:
            integration = IntegrationFactory.create_by_type(name)
        except Exception as exc:
            return False, {}, [f"Failed to load integration '{name}': {exc}"]

        available = integration.is_available()
        version = integration.get_version() if available else None
        setup_info = integration.get_setup_info()
        caps = [c.__name__ for c in (integration.CAPABILITIES if hasattr(integration, "CAPABILITIES") else [])]

        detail = {
            **setup_info,
            "available": available,
            "version": version,
            "capabilities": caps,
        }
        success = available
        errors = (
            []
            if success
            else [f"Integration '{name}' is not available. Install: {setup_info.get('install_url') or 'see docs'}"]
        )
        return success, detail, errors
