"""Platform file validator — resolves kind and delegates to the appropriate service."""

from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from xyz_platform.controllers.lifecycle_controller import LifecycleController
from xyz_platform.logger import get_logger
from xyz_platform.models.common_models import CommonLifecycleModel, PlatformKind
from xyz_platform.models.configuration_model import ConfigurationModel
from xyz_platform.services.base_service import BaseService
from xyz_platform.services.deployment_service import DeploymentService
from xyz_platform.services.environment_service import EnvironmentService
from xyz_platform.services.firewall_service import FirewallService
from xyz_platform.services.module_service import ModuleService
from xyz_platform.services.namespace_service import NamespaceService
from xyz_platform.services.platform_artifact_service import PlatformService
from xyz_platform.services.provider_service import ProviderService
from xyz_platform.services.resource_service import ResourceService
from xyz_platform.services.workspace_service import WorkspaceService
from xyz_platform.validators.base_validator import BaseValidator

# ConfigurationService is intentionally excluded: it is a path-less singleton
# whose __init__ accepts no constructor arguments, making it incompatible with
# BaseService.load(path).  CONFIGURATION kind files are validated directly via
# ConfigurationModel.model_validate() in the validate() method below.
_KIND_TO_SERVICE: Dict[PlatformKind, Any] = {
    PlatformKind.DEPLOYMENT: DeploymentService,
    PlatformKind.ENVIRONMENT: EnvironmentService,
    PlatformKind.FIREWALL: FirewallService,
    PlatformKind.MODULE: ModuleService,
    PlatformKind.NAMESPACE: NamespaceService,
    PlatformKind.PLATFORM_MODEL: PlatformService,
    PlatformKind.PROVIDER: ProviderService,
    PlatformKind.RESOURCE: ResourceService,
    PlatformKind.WORKSPACE: WorkspaceService,
}


class PlatformValidator(BaseValidator):
    """Validates a single platform YAML file by resolving its kind and delegating to the appropriate service."""

    def __init__(
        self,
        file_path: Path,
        configuration_service=None,
    ) -> None:
        super().__init__()
        self._file_path = file_path
        self._configuration_service = configuration_service
        self._detected_kind: Optional[PlatformKind] = None
        self._service: Optional[BaseService] = None
        self._lifecycle_model: Optional[CommonLifecycleModel] = None
        self.logger = get_logger(__name__)

    @property
    def detected_kind(self) -> Optional[PlatformKind]:
        return self._detected_kind

    @property
    def service(self) -> Optional[BaseService]:
        return self._service

    def before_validate(self, work_path: Path) -> bool:
        """Verify file exists, parse YAML, extract and validate ``kind``."""
        if not self._file_path.exists():
            self._errors.append(f"File not found: {self._file_path}")
            self.logger.warning("File not found", path=str(self._file_path))
            return False

        try:
            raw = self._file_path.read_text(encoding="utf-8")
            doc = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            self._errors.append(f"YAML parse error in '{self._file_path}': {exc}")
            self.logger.warning("YAML parse error", path=str(self._file_path), error=str(exc))
            return False
        except OSError as exc:
            self._errors.append(f"Cannot read '{self._file_path}': {exc}")
            self.logger.warning("File read error", path=str(self._file_path), error=str(exc))
            return False

        if not isinstance(doc, dict):
            self._errors.append(f"Expected a YAML mapping in '{self._file_path}', got {type(doc).__name__}.")
            return False

        raw_kind = doc.get("kind")
        if raw_kind is None:
            self._errors.append(f"Missing required field 'kind' in '{self._file_path}'.")
            return False

        try:
            self._detected_kind = PlatformKind(raw_kind)
        except ValueError:
            valid = ", ".join(k.value for k in PlatformKind)
            self._errors.append(f"Unknown kind '{raw_kind}' in '{self._file_path}'. Valid kinds: {valid}.")
            self.logger.warning("Unknown kind", kind=raw_kind, path=str(self._file_path))
            return False

        self.logger.debug("Kind detected", kind=self._detected_kind.value, path=str(self._file_path))

        # Extract lifecycle model from spec.lifecycle for hook execution
        raw_spec = doc.get("spec")
        spec: Dict[str, Any] = raw_spec if isinstance(raw_spec, dict) else {}
        lifecycle_raw = spec.get("lifecycle")
        if isinstance(lifecycle_raw, dict):
            try:
                self._lifecycle_model = CommonLifecycleModel.model_validate(lifecycle_raw)
            except Exception as exc:
                self.logger.debug(
                    "Could not parse lifecycle from YAML — hooks will be skipped",
                    path=str(self._file_path),
                    error=str(exc),
                )

        # Execute validate_before lifecycle hook (file-level + configuration-level scripts)
        lc = LifecycleController()
        if not lc.execute_phase(
            phase_name="validate_before",
            lifecycle_model=self._lifecycle_model,
            work_path=work_path,
            context={"file": str(self._file_path), "kind": self._detected_kind.value},
            add_config_model=True,
        ):
            for err in lc.get_errors():
                self._errors.append(f"validate_before lifecycle hook: {err}")
            return False

        return True

    def validate(self, work_path: Path) -> bool:
        """Map kind → service, run Phase 1 (Pydantic) and optional Phase 2 (dynamic)."""
        if self._detected_kind is None:
            self._errors.append("validate() called before before_validate() — no kind detected.")
            return False

        # CONFIGURATION kind: validate directly via ConfigurationModel (no path-based service)
        if self._detected_kind == PlatformKind.CONFIGURATION:
            return self._validate_configuration_model(work_path)

        service_class = _KIND_TO_SERVICE.get(self._detected_kind)
        if service_class is None:
            self._errors.append(
                f"No service registered for kind '{self._detected_kind}' — cannot validate '{self._file_path}'."
            )
            return False

        # Phase 1: structural (Pydantic) validation via BaseService.load()
        try:
            service = service_class.load(str(self._file_path))
        except Exception as exc:
            self._errors.append(f"Failed to load '{self._file_path}' as {service_class.__name__}: {exc}")
            self.logger.error(
                "Service load raised an exception",
                service=service_class.__name__,
                path=str(self._file_path),
                error=str(exc),
            )
            return False

        if not service.is_validated():
            self._errors.extend(service.get_validation_errors())
            self.logger.warning(
                "Phase 1 validation failed",
                service=service_class.__name__,
                error_count=len(service._errors),
                path=str(self._file_path),
            )
            return False

        self._service = service

        # Phase 2: dynamic validation against configuration (optional)
        if self._configuration_service is not None:
            is_valid, dynamic_errors = service.validate(
                configuration_model=self._configuration_service.model,
                work_path=str(work_path),
            )
            if not is_valid:
                self._errors.extend(dynamic_errors)
                self.logger.warning(
                    "Phase 2 dynamic validation failed",
                    service=service_class.__name__,
                    error_count=len(dynamic_errors),
                    path=str(self._file_path),
                )
                return False

        return True

    def _validate_configuration_model(self, work_path: Path) -> bool:
        """Validate a CONFIGURATION kind file directly via ConfigurationModel."""
        from pydantic import ValidationError

        try:
            raw = self._file_path.read_text(encoding="utf-8")
            import yaml as _yaml

            doc = _yaml.safe_load(raw)
        except Exception as exc:
            self._errors.append(f"Failed to read '{self._file_path}': {exc}")
            return False

        try:
            ConfigurationModel.model_validate(doc)
        except ValidationError as exc:
            for err in exc.errors():
                loc = " -> ".join(str(p) for p in err["loc"])
                self._errors.append(f"{loc}: {err['msg']}")
            self.logger.warning(
                "Configuration model validation failed",
                error_count=exc.error_count(),
                path=str(self._file_path),
            )
            return False
        except Exception as exc:
            self._errors.append(f"Unexpected error validating '{self._file_path}': {exc}")
            return False

        return True

    def after_validate(self, work_path: Path) -> bool:
        """Execute validate_after lifecycle hook (file-level + configuration-level scripts)."""
        lc = LifecycleController()
        if not lc.execute_phase(
            phase_name="validate_after",
            lifecycle_model=self._lifecycle_model,
            work_path=work_path,
            context={
                "file": str(self._file_path),
                "kind": self._detected_kind.value if self._detected_kind else "unknown",
                "validation_passed": not self.has_errors(),
            },
            add_config_model=True,
        ):
            for err in lc.get_errors():
                self._errors.append(f"validate_after lifecycle hook: {err}")
            return False

        return True
