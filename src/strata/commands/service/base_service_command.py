"""Base class for service commands."""

from abc import abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from strata.commands.base_command import BaseCommand
from strata.models.common_models import ServiceDeployerType
from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_service import DeploymentService


@dataclass
class ServiceTarget:
    """A resolved service target (namespace + optional module)."""

    namespace: str
    module: Optional[str]
    deployer_type: ServiceDeployerType
    build_path: Path


class BaseServiceCommand(BaseCommand):
    """Base class for service command implementations.

    Handles deployment file loading and service name resolution.
    A "service" is a module deployed into a namespace. The resolution logic:
      1. If name matches a namespace → target all modules in that namespace
      2. If name matches a module → target that module in its parent namespace
      3. If name is "namespace/module" → target that exact combination
    """

    OPERATION = "service"
    INIT_REQUIRED = True

    def __init__(
        self,
        file: Optional[str] = None,
        name: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ):
        super().__init__(
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self._raw_file: Optional[str] = file
        self._file_path: Optional[Path] = Path(file) if file else None
        self._name: Optional[str] = name
        self._deployment_service: Optional[DeploymentService] = None
        self._configuration_service: Optional[ConfigurationService] = None
        self._build_path: Path = self._work_path / "build"
        self._targets: List[ServiceTarget] = []

    @abstractmethod
    def execute(self) -> bool:
        raise NotImplementedError

    def get_required_integrations(self):
        return {}

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _before_execute(self) -> bool:
        """Load and validate the deployment file + configuration service."""
        if not super()._before_execute():
            return False

        if not self._file_path:
            self._errors.append("No deployment file specified. Use --file.")
            return False

        from strata.utils.system import resolve_path

        repo_map: dict[str, str] = (
            self._solution_controller.get_repo_map() if self._solution_controller is not None else {}
        )

        try:
            candidate = resolve_path(str(self._work_path), self._raw_file, repo_map=repo_map)
        except ValueError as e:
            self._errors.append(f"Deployment file reference error: {e}")
            return False

        if not candidate.exists():
            self._errors.append(f"Deployment file not found: {candidate}")
            return False
        self._file_path = candidate

        self._configuration_service = self._load_configuration_service()
        if self._configuration_service is None:
            return False
        self._build_path = self._get_build_path()

        deployment_service = DeploymentService.load(str(self._file_path), validate=True)
        if not deployment_service.is_validated():
            self._errors.extend(deployment_service.get_validation_errors())
            return False

        config_model = self._configuration_service.model if self._configuration_service else None
        ok, errors = deployment_service.validate(
            configuration_model=config_model,
            work_path=self._work_path,
            repo_map=repo_map,
        )
        if not ok:
            self._errors.extend(errors)
            return False

        if not deployment_service.load_deploy_services(str(self._work_path), repo_map=repo_map):
            self._errors.extend(deployment_service.get_validation_errors())
            return False

        ok, errors = deployment_service.validate_related_services()
        if not ok:
            self._errors.extend(errors)
            return False

        ok, errors = deployment_service.apply_environment_overrides()
        if not ok:
            critical = [e for e in errors if "skipped" not in e.lower()]
            if critical:
                self._errors.extend(critical)
                return False
            self._messages.extend(errors)

        self._deployment_service = deployment_service
        return True

    # ------------------------------------------------------------------
    # Service resolution
    # ------------------------------------------------------------------

    def _resolve_all_targets(self) -> List[ServiceTarget]:
        """Enumerate all service targets (namespace × module) in the deployment."""
        targets: List[ServiceTarget] = []
        if self._deployment_service is None:
            return targets

        namespace_services = self._deployment_service.get_namespace_services() or {}
        deployment_build_path = self._deployment_service.get_build_path(self._build_path)

        for ns_name, ns_service in namespace_services.items():
            if not ns_service.is_validated() or not ns_service.model:
                continue
            ns_modules = ns_service.model.spec.modules or []
            for ns_mod in ns_modules:
                mod_service = self._deployment_service.get_module_service(
                    resource_name=str(ns_name), module_name=str(ns_mod.name)
                )
                if mod_service is None or not mod_service.is_validated() or not mod_service.model:
                    continue
                deployer_type = mod_service.model.spec.type
                targets.append(
                    ServiceTarget(
                        namespace=str(ns_name),
                        module=str(ns_mod.name),
                        deployer_type=deployer_type,
                        build_path=deployment_build_path / str(ns_name) / str(ns_mod.name),
                    )
                )
        return targets

    def _resolve_targets_by_name(self, name: str) -> Tuple[List[ServiceTarget], List[str]]:
        """Resolve a user-supplied name to one or more ServiceTargets.

        Resolution order:
          1. "namespace/module" → exact match
          2. Namespace name → all modules in that namespace
          3. Module name → all occurrences of that module across namespaces
        """
        errors: List[str] = []
        all_targets = self._resolve_all_targets()

        if not all_targets:
            errors.append("No service targets found in deployment. Check namespace/module configuration.")
            return [], errors

        # 1. Qualified "namespace/module" format
        if "/" in name:
            parts = name.split("/", 1)
            ns_name, mod_name = parts[0], parts[1]
            matched = [t for t in all_targets if t.namespace == ns_name and t.module == mod_name]
            if matched:
                return matched, []
            errors.append(f"Service '{name}' not found. No module '{mod_name}' in namespace '{ns_name}'.")
            return [], errors

        # 2. Match as namespace (all modules within it)
        ns_matches = [t for t in all_targets if t.namespace == name]
        if ns_matches:
            return ns_matches, []

        # 3. Match as module name (across all namespaces)
        mod_matches = [t for t in all_targets if t.module == name]
        if mod_matches:
            return mod_matches, []

        # No match
        available_ns = sorted(set(t.namespace for t in all_targets))
        available_mod = sorted(set(t.module for t in all_targets if t.module))
        errors.append(
            f"Service '{name}' not found.\n"
            f"  Available namespaces: {', '.join(available_ns)}\n"
            f"  Available modules: {', '.join(available_mod)}"
        )
        return [], errors

    # ------------------------------------------------------------------
    # Configuration service loading (shared with BaseDeployCommand)
    # ------------------------------------------------------------------

    def _load_configuration_service(self) -> Optional[ConfigurationService]:
        """Load ConfigurationService from the active profile's configfile_paths."""
        from strata.utils.system import resolve_path

        if self._solution_controller.solution is None:
            self._errors.append("Service commands require an initialized workspace. Run `strata sln init` first.")
            return None

        profile, _ = self._solution_controller.get_active_profile()
        if profile is None:
            self._errors.append("Service commands require an active profile. Run `strata profile activate <name>`.")
            return None

        configfile_paths = profile.configfile_paths or []
        if not configfile_paths:
            self._errors.append(
                "Service commands require at least one configfile path on the active profile. "
                "Add one with `strata ref configfile add`."
            )
            return None

        repo_map = self._solution_controller.get_repo_map()

        resolved_paths = []
        for entry in configfile_paths:
            try:
                resolved = resolve_path(str(self._work_path), str(entry.path), repo_map=repo_map)
            except ValueError as exc:
                self.logger.debug("Config source skipped", name=str(entry.name), reason=str(exc))
                continue
            if not resolved.exists():
                self.logger.debug("Config source not found", name=str(entry.name), path=str(resolved))
                continue
            resolved_paths.append(str(resolved))

        if not resolved_paths:
            self._errors.append("No configfile_paths resolved to existing files. Check your profile refs.")
            return None

        try:
            ConfigurationService.reset()
            config_svc = ConfigurationService.get_instance()
            success, load_errors = config_svc.load_from_paths(resolved_paths)
            if not success:
                self._errors.append(f"Failed to load configuration: {'; '.join(load_errors)}")
                return None
            return config_svc
        except Exception as exc:
            self._errors.append(f"Unexpected error loading configuration: {exc}")
            return None
