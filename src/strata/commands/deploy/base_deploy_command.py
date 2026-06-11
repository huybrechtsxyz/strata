"""Base class for deploy commands."""

import hashlib
import os
from abc import abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from strata.commands.base_command import BaseCommand
from strata.models.deployment_manifest_model import (
    DeploymentManifestMetaModel,
    DeploymentManifestModel,
    DeploymentManifestSpecModel,
    ManifestPlatformModel,
    ManifestRepositoryModel,
    ManifestStageModel,
)
from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_manifest_service import DeploymentManifestService
from strata.services.deployment_service import DeploymentService


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
        self._build_path: Path = self._work_path / "build"
        self._deploy_started_at: Optional[str] = None
        self._stage_results: List[ManifestStageModel] = []

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
        self.logger.info("Using deployment file", file=str(self._file_path))

        # Load configuration service (required for cross-validation)
        self._configuration_service = self._load_configuration_service()
        if self._configuration_service is None:
            return False
        self._build_path = self._get_build_path()

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
        from strata.utils.system import resolve_path

        if self._solution_controller.solution is None:
            self._errors.append("Deploy requires an initialized workspace. Run `strata sln init` first.")
            return None

        profile, _ = self._solution_controller.get_active_profile()
        if profile is None:
            self._errors.append("Deploy requires an active profile. Run `strata profile activate <name>`.")
            return None

        configfile_paths = profile.configfile_paths or []
        if not configfile_paths:
            self._errors.append(
                "Deploy requires at least one configfile path on the active profile. "
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

    # ------------------------------------------------------------------
    # Deployment manifest helpers
    # ------------------------------------------------------------------

    def _record_deploy_start(self) -> None:
        """Record the start time of the deploy operation."""
        self._deploy_started_at = datetime.now(timezone.utc).isoformat()

    def _record_stage_result(
        self,
        stage_name: str,
        provisioner: Optional[str],
        topology: Optional[str],
        status: str,
        started_at: Optional[str],
        completed_at: Optional[str],
        steps: Optional[List[str]] = None,
        outputs: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """Append a stage result for the deployment manifest."""
        duration: Optional[int] = None
        if started_at and completed_at:
            try:
                t0 = datetime.fromisoformat(started_at)
                t1 = datetime.fromisoformat(completed_at)
                duration = int((t1 - t0).total_seconds())
            except (ValueError, TypeError):
                pass

        self._stage_results.append(
            ManifestStageModel(
                name=stage_name,
                provisioner=provisioner,
                topology=topology,
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
                steps=steps,
                outputs=outputs,
                error=error,
            )
        )

    def _write_deployment_manifest(
        self,
        action: str,
        status: str,
        dry_run: bool = False,
    ) -> Optional[Path]:
        """Assemble and persist a deployment manifest.

        Args:
            action: ``"deploy"`` or ``"destroy"``.
            status: ``"success"``, ``"partial"``, or ``"failed"``.
            dry_run: Whether this was a dry-run (skips writing).

        Returns:
            Path to the written manifest, or None on skip/error.
        """
        if dry_run:
            self.logger.debug("Dry-run — skipping deployment manifest write")
            return None

        if self._deployment_service is None:
            self.logger.warning("Cannot write manifest — deployment service not loaded")
            return None

        try:
            completed_at = datetime.now(timezone.utc).isoformat()
            started_at = self._deploy_started_at or completed_at

            duration: Optional[int] = None
            try:
                t0 = datetime.fromisoformat(started_at)
                t1 = datetime.fromisoformat(completed_at)
                duration = int((t1 - t0).total_seconds())
            except (ValueError, TypeError):
                pass

            # Platform artifact fingerprint
            platform_info = self._hash_platform_artifact()

            # Repository bill of materials
            repositories = self._collect_repository_info()

            # Deployment identity
            deploy_meta = self._deployment_service.model.meta  # type: ignore[union-attr]
            workspace_service = self._deployment_service.get_workspace_service()
            workspace_name = (
                str(workspace_service.model.meta.name) if workspace_service and workspace_service.model else "unknown"
            )

            # Environment from deployment labels
            labels = deploy_meta.labels or {}
            environment = labels.get("environment")

            # Actor
            deployed_by = (
                os.environ.get("GITHUB_ACTOR") or os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
            )

            manifest = DeploymentManifestModel(
                meta=DeploymentManifestMetaModel(
                    name=deploy_meta.name,
                    annotations=deploy_meta.annotations,
                    labels=deploy_meta.labels,
                    tags=deploy_meta.tags,
                ),
                spec=DeploymentManifestSpecModel(
                    deployment_name=deploy_meta.name,
                    workspace_name=workspace_name,
                    environment=environment,
                    action=action,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_seconds=duration,
                    status=status,
                    dry_run=dry_run,
                    deployed_by=deployed_by,
                    platform=platform_info,
                    repositories=repositories if repositories else None,
                    stages=self._stage_results if self._stage_results else None,
                ),
            )

            deployments_dir = self._work_path / ".strata" / "deployments"
            svc = DeploymentManifestService()
            path = svc.save(manifest, deployments_dir)
            self.logger.info("Deployment manifest written", path=str(path))
            return path

        except Exception as exc:
            self.logger.warning("Failed to write deployment manifest", error=str(exc))
            return None

    def _hash_platform_artifact(self) -> ManifestPlatformModel:
        """Compute SHA-256 of platform.json and return a ManifestPlatformModel."""
        if self._deployment_service is None:
            return ManifestPlatformModel(hash="unknown")

        platform_path = self._deployment_service.get_build_path(self._build_path) / "platform.json"
        if platform_path.exists():
            content = platform_path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            rel_path = str(platform_path.relative_to(self._work_path))
            return ManifestPlatformModel(hash=f"sha256:{digest}", path=rel_path)

        return ManifestPlatformModel(hash="unknown")

    def _collect_repository_info(self) -> Optional[Dict[str, ManifestRepositoryModel]]:
        """Walk solution repositories and collect URL/ref/commit info."""
        if self._solution_controller is None or self._solution_controller.solution is None:
            return None

        solution = self._solution_controller.solution
        repos = solution.spec.repositories or []
        if not repos:
            return None

        result: Dict[str, ManifestRepositoryModel] = {}
        for repo in repos:
            name = str(repo.name)
            url = getattr(repo, "url", None)
            ref = getattr(repo, "ref", None)
            commit: Optional[str] = None

            # Try to resolve commit SHA from the local clone
            repo_map = self._solution_controller.get_repo_map()
            if repo_map and name in repo_map:
                repo_path = Path(repo_map[name])
                head_file = repo_path / ".git" / "HEAD"
                if head_file.exists():
                    try:
                        head_content = head_file.read_text(encoding="utf-8").strip()
                        if head_content.startswith("ref:"):
                            ref_path = repo_path / ".git" / head_content[5:]
                            if ref_path.exists():
                                commit = ref_path.read_text(encoding="utf-8").strip()
                        else:
                            commit = head_content  # detached HEAD = commit SHA
                    except OSError:
                        pass

            result[name] = ManifestRepositoryModel(
                url=str(url) if url else None,
                ref=str(ref) if ref else None,
                commit=commit,
            )

        return result if result else None
