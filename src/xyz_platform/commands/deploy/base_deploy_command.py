"""Base class for deploy commands."""

from abc import abstractmethod
from pathlib import Path
from typing import Optional

from xyz_platform.commands.base_command import BaseCommand
from xyz_platform.controllers.solution_controller import SolutionController
from xyz_platform.services.configuration_service import ConfigurationService
from xyz_platform.services.deployment_service import DeploymentService


class BaseDeployCommand(BaseCommand):
    """Base class for deploy command implementations."""

    OPERATION = "deploy"
    INIT_REQUIRED = True

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ):
        super().__init__(
            work_path=work_path,
            output=output,
            verbose=verbose or False,
            quiet=quiet or False,
        )
        self._raw_file: Optional[str] = file
        self._file_path: Optional[Path] = Path(file) if file else None
        self._deployment_service: Optional[DeploymentService] = None
        self._configuration_service: Optional[ConfigurationService] = None
        self._build_path: Path = SolutionController.get_state_dir(self._work_path) / "build"

    @abstractmethod
    def execute(self) -> bool:
        raise NotImplementedError

    def get_required_integrations(self):
        return {}

    def _before_execute(self) -> bool:
        """Load and validate the deployment file + configuration service."""
        if not super()._before_execute():
            return False

        if not self._file_path:
            self._errors.append("No deployment file specified. Use --file.")
            return False

        # Resolve to absolute path, supporting @repo-name/... references
        from xyz_platform.utils.system import resolve_path

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

        # Load configuration service (required for cross-validation)
        self._configuration_service = self._load_configuration_service()
        if self._configuration_service is None:
            return False

        # Phase 1: load + Pydantic-validate the deployment file
        deployment_service = DeploymentService.load(str(self._file_path), validate=True)
        if not deployment_service.is_validated():
            self._errors.extend(deployment_service.get_validation_errors())
            return False

        # Phase 2: cross-validate against configuration
        config_model = self._configuration_service.model if self._configuration_service else None
        ok, errors = deployment_service.validate(
            configuration_model=config_model,
            work_path=self._work_path,
            repo_map=repo_map,
        )
        if not ok:
            self._errors.extend(errors)
            return False

        # Load related services (workspace, environment, providers, resources, ...)
        if not deployment_service.load_deploy_services(str(self._work_path), repo_map=repo_map):
            self._errors.extend(deployment_service.get_validation_errors())
            return False

        # Cross-validate related services
        ok, errors = deployment_service.validate_related_services()
        if not ok:
            self._errors.extend(errors)
            return False

        # Apply environment overrides
        ok, errors = deployment_service.apply_environment_overrides()
        if not ok:
            critical = [e for e in errors if "skipped" not in e.lower()]
            if critical:
                self._errors.extend(critical)
                return False
            self._messages.extend(errors)  # non-critical warnings

        self._deployment_service = deployment_service

        self.logger.debug(
            "Deployment loaded",
            file=str(self._file_path),
            build_path=str(self._build_path),
        )
        return True

    def _load_configuration_service(self) -> Optional[ConfigurationService]:
        """Load ConfigurationService from the active profile's configfile_paths."""
        from xyz_platform.utils.system import resolve_path

        if self._solution_controller.solution is None:
            self._errors.append("Deploy requires an initialized workspace. Run `xyz solution init` first.")
            return None

        profile, _ = self._solution_controller.get_active_profile()
        if profile is None:
            self._errors.append("Deploy requires an active profile. Run `xyz profile activate <name>`.")
            return None

        configfile_paths = profile.configfile_paths or []
        if not configfile_paths:
            self._errors.append(
                "Deploy requires at least one configfile path on the active profile. "
                "Add one with `xyz ref configfile add`."
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

            self.logger.debug(
                "ConfigurationService loaded",
                profile=str(profile.name),
                files=len(resolved_paths),
            )
            return config_svc
        except Exception as exc:
            self._errors.append(f"Unexpected error loading configuration: {exc}")
            return None

    def _after_execute(self) -> bool:
        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        return super()._finalize(success=success, show_footer=show_footer)
