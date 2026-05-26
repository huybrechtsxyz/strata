"""Controller for inspecting external tool integration status and setup info."""

from __future__ import annotations

from typing import Optional

from strata.controllers.base_controller import BaseController
from strata.integrations.factory import IntegrationFactory
from strata.logger import get_logger

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

# Store enum value → integration type name (None-valued stores need no integration)
_STORE_TO_INTEGRATION: dict[str, str] = {
    "bitwarden": "bitwarden",
    "azure-keyvault": "azure_keyvault",
    "vault": "hashicorp_vault",
    "azure-appconfig": "azure_appconfig",
    "consul": "hashicorp_consul",
}

# Provisioner type value → integration type name
_PROVISIONER_TO_INTEGRATION: dict[str, str] = {
    "terraform": "terraform",
    "ansible": "ansible",
}


class ToolsController(BaseController):
    """Lists and checks the status of registered integrations."""

    def status(
        self,
        deployment_file: Optional[str] = None,
        work_path: Optional[str] = None,
    ) -> tuple[bool, list[dict], list[str]]:
        """
        Return availability status for all known integrations.

        When ``deployment_file`` is provided each row includes a ``requirement``
        field: ``"required"`` / ``"optional"`` / ``None`` (not referenced).

        Returns:
            Tuple of (success, rows, errors).
            Each row: {
                "name": str,
                "available": bool,
                "version": Optional[str],
                "capabilities": list[str],
                "command": Optional[str],
                "requirement": Optional[str],  # "required" | "optional" | None
            }
        """
        requirements: dict[str, str] = {}
        errors: list[str] = []

        if deployment_file:
            requirements, derive_errors = self._derive_required(deployment_file, work_path)
            errors.extend(derive_errors)

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
                        "requirement": requirements.get(type_str),
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
                        "requirement": requirements.get(type_str),
                    }
                )
        return True, rows, errors

    def _derive_required(
        self,
        deployment_file: str,
        work_path: Optional[str] = None,
    ) -> tuple[dict[str, str], list[str]]:
        """
        Derive integration requirement levels from a deployment file.

        Scans environments (store types), workspace (provisioner types), and
        configurations (declared integrations) to build a mapping of
        integration_type → "required" | "optional".

        Returns:
            ({integration_type: "required"|"optional"}, errors)
        """
        from pathlib import Path

        from strata.services.configuration_service import ConfigurationService
        from strata.services.deployment_service import DeploymentService
        from strata.services.environment_service import EnvironmentService
        from strata.services.workspace_service import WorkspaceService

        requirements: dict[str, str] = {}
        errors: list[str] = []

        dep_svc = DeploymentService.load(deployment_file)
        if not dep_svc.is_validated() or dep_svc.model is None:
            return {}, [f"Cannot load deployment file: {deployment_file}"]
        dep = dep_svc.model
        base = Path(deployment_file).parent

        def _mark(integration_type: str, is_required: bool) -> None:
            # "required" always beats "optional" if the key is already present
            if integration_type not in requirements or is_required:
                requirements[integration_type] = "required" if is_required else "optional"

        # Environments: store usage implies a required integration
        for env_path in dep.spec.environments:
            try:
                env_svc = EnvironmentService.load(str(base / env_path))
                if not env_svc.is_validated() or env_svc.model is None:
                    continue
                env = env_svc.model
                for item in env.spec.secrets or []:
                    mapped = _STORE_TO_INTEGRATION.get(item.store.value)
                    if mapped:
                        _mark(mapped, True)
                for item in env.spec.variables or []:
                    mapped = _STORE_TO_INTEGRATION.get(item.store.value)
                    if mapped:
                        _mark(mapped, True)
                for item in env.spec.features or []:
                    mapped = _STORE_TO_INTEGRATION.get(item.store.value)
                    if mapped:
                        _mark(mapped, True)
            except Exception as exc:
                errors.append(f"Environment '{env_path}': {exc}")

        # Workspace: provisioner usage implies a required integration
        try:
            ws_svc = WorkspaceService.load(str(base / dep.spec.workspace.file))
            if ws_svc.is_validated() and ws_svc.model is not None:
                for prov in ws_svc.model.spec.provisioners or []:
                    mapped = _PROVISIONER_TO_INTEGRATION.get(prov.provisioner.value)
                    if mapped:
                        _mark(mapped, True)
        except Exception as exc:
            errors.append(f"Workspace '{dep.spec.workspace.file}': {exc}")

        # Configurations: explicit required/optional flags from IntegrationModel
        for cfg_ref in dep.spec.configurations or []:
            try:
                cfg_svc = ConfigurationService.load(str(base / cfg_ref.file))
                if not cfg_svc.is_validated() or cfg_svc.model is None:
                    continue
                for integration in cfg_svc.model.spec.integrations or []:
                    _mark(str(integration.type), integration.required)
            except Exception as exc:
                errors.append(f"Configuration '{cfg_ref.file}': {exc}")

        return requirements, errors

    def install_info(self, name: str) -> tuple[bool, dict, list[str]]:
        """
        Return static setup/install guidance for a single integration.

        Unlike ``check``, this does *not* probe the runtime — it only reads
        the integration's ``get_setup_info()`` metadata.  Works even when the
        tool is not installed.

        Returns:
            Tuple of (success, setup_info_dict, errors).
        """
        if name not in _BUILTIN_TYPES:
            return False, {}, [f"Unknown integration: '{name}'. Known: {', '.join(_BUILTIN_TYPES)}"]

        try:
            integration = IntegrationFactory.create_by_type(name)
        except Exception as exc:
            return False, {}, [f"Failed to load integration '{name}': {exc}"]

        return True, integration.get_setup_info(), []

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
