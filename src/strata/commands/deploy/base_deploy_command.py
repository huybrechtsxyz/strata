"""Base class for deploy commands."""

import hashlib
import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.integrations.lock.base_lock_backend import (
    BaseLockBackend,
    LockBackendError,
    LockConflictError,
    LockHandle,
)
from strata.models.common_models import ProvisionerType
from strata.models.deployment_manifest_model import (
    DeploymentManifestMetaModel,
    DeploymentManifestModel,
    DeploymentManifestSpecModel,
    ManifestArtifactImageModel,
    ManifestArtifactProviderModel,
    ManifestArtifactsModel,
    ManifestLockReferenceModel,
    ManifestOutputsReferenceModel,
    ManifestPlatformModel,
    ManifestPolicyResultModel,
    ManifestRepositoryModel,
    ManifestStageModel,
)
from strata.models.deployment_model import DeploymentStageModel
from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_manifest_service import DeploymentManifestService
from strata.services.deployment_service import DeploymentService
from strata.utils.duration import parse_duration


class BaseDeployCommand(BaseCommand):
    """Base class for deploy command implementations."""

    OPERATION = "deploy"

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
            verbose=verbose,
            quiet=quiet,
        )
        self._raw_file: Optional[str] = file
        self._file_path: Optional[Path] = Path(file) if file else None
        self._deployment_service: Optional[DeploymentService] = None
        self._configuration_service: Optional[ConfigurationService] = None
        self._build_path: Path = self._work_path / "build"
        self._deploy_started_at: Optional[str] = None
        self._stage_results: List[ManifestStageModel] = []
        self._policy_results: List[ManifestPolicyResultModel] = []
        self._lock_ref: Optional[ManifestLockReferenceModel] = None
        self._audit_log_path: Optional[str] = None
        self._lock_conflict: bool = False
        # Subclasses override these before calling _execute_provisioning.
        self._dry_run: bool = False
        self._force_lock: bool = False

    def get_required_integrations(self):
        return {}

    def has_lock_conflict(self) -> bool:
        """True when the last execute() failed due to a deployment lock conflict.

        Checked by ``handle_command_exit`` to emit exit code 4 instead of 1.
        Only reachable for ``deploy run`` and ``deploy destroy`` — ``LockConflictError``
        is only raised by the lock-acquisition path in these commands.
        """
        return self._lock_conflict

    # -------------------------------------------------------------------------
    # Lock helpers — shared by RunDeployCommand and DestroyDeployCommand
    # -------------------------------------------------------------------------

    def _should_lock(self) -> bool:
        """Return True if locking is enabled and this is not a dry-run.

        Returns False immediately for dry-runs and for the ``delegate``
        strategy (which trusts the backend's native locking, e.g. TFC run queue).
        """
        if self._dry_run:
            return False
        if self._deployment_service is None:
            return False
        spec = self._deployment_service.model.spec  # type: ignore[union-attr]
        locking = getattr(spec, "locking", None)
        if locking is None or not locking.enabled:
            return False
        if locking.strategy == "delegate":
            return False
        return True

    def _resolve_lock_backend(self, stages: List[DeploymentStageModel]) -> BaseLockBackend:
        """Return the lock backend from the first Terraform provisioner with a backend.

        Falls back to ``LocalLockBackend`` when no matching provisioner is found.
        """
        from strata.integrations.lock.lock_factory import LockFactory

        if self._deployment_service is not None:
            workspace_service = self._deployment_service.get_workspace_service()
            if workspace_service is not None:
                spec = workspace_service.model.spec  # type: ignore[union-attr]
                provisioners = spec.provisioners or []
                for stage in stages:
                    if stage.provisioner:
                        iac = next(
                            (p for p in provisioners if p.name == stage.provisioner),
                            None,
                        )
                        if iac and iac.provisioner == ProvisionerType.TERRAFORM and iac.backend:
                            return LockFactory.create(iac.backend, self._work_path)

        return LockFactory.create(None, self._work_path)

    def _acquire_lock(self, backend: BaseLockBackend) -> Optional[LockHandle]:
        """Acquire the deployment lock. Returns the handle or ``None`` on failure.

        When ``self._force_lock`` is True and a lock is already held, the held
        lock is force-released before acquiring.  A warning is printed so the
        audit trail is clear about the override.
        """
        if self._deployment_service is None:
            return None
        deploy_name = str(self._deployment_service.model.meta.name)  # type: ignore[union-attr]
        spec = self._deployment_service.model.spec  # type: ignore[union-attr]
        locking = getattr(spec, "locking", None)
        wait_timeout: str = getattr(locking, "wait_timeout", "30m") if locking else "30m"
        timeout_seconds = parse_duration(wait_timeout)
        holder = os.environ.get("GITHUB_ACTOR") or os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"

        # Force-release a held lock when --force-lock is set.
        if self._force_lock:
            try:
                existing = backend.status(deploy_name)
                if existing is not None:
                    if self._is_console_output():
                        click.echo(
                            f"\n⚠️   --force-lock: releasing lock held by '{existing.holder}' "
                            f"on {existing.hostname} (lock_id: {existing.lock_id})"
                        )
                    backend.force_release(deploy_name)
                    self.logger.warning(
                        "deploy_lock_force_released_before_acquire",
                        deployment=deploy_name,
                        previous_holder=existing.holder,
                    )
            except LockBackendError as exc:
                self._errors.append(f"Force-lock release failed: {exc}")
                return None

        reason = f"strata {self.OPERATION.replace('_', ' ')}"
        try:
            handle = backend.acquire(
                deployment_name=deploy_name,
                holder=holder,
                reason=reason,
                timeout_seconds=timeout_seconds,
            )
            self._lock_ref = ManifestLockReferenceModel(
                lock_id=handle.lock_id,
                backend=handle.backend_type,
                acquired_at=handle.acquired_at,
                holder=holder,
                hostname=socket.gethostname(),
            )
            if self._is_console_output():
                click.echo(f"\n\U0001f512  Lock acquired ({handle.backend_type}) for '{deploy_name}'")
            self.logger.info(
                "deploy_lock_acquired",
                lock_id=handle.lock_id,
                backend=handle.backend_type,
            )
            return handle
        except LockConflictError as exc:
            self._lock_conflict = True
            self._errors.append(str(exc))
            if self._is_console_output():
                holder = getattr(exc, "holder", "unknown")
                click.echo(
                    f"\n\U0001f512  Could not acquire lock \u2014 held by {holder!r}. "
                    "Run `strata deploy lock status` for details, or use --force-lock to override."
                )
            self.logger.error(
                "deploy_lock_conflict",
                deployment=deploy_name,
                error=str(exc),
            )
            return None
        except LockBackendError as exc:
            self._errors.append(f"Lock backend error: {exc}")
            self.logger.error("deploy_lock_backend_error", error=str(exc))
            return None

    def _release_lock(self, backend: BaseLockBackend, handle: LockHandle) -> None:
        """Release the deployment lock. Safe to call in a ``finally`` block."""
        released_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            backend.release(handle)
            if self._lock_ref is not None:
                self._lock_ref = self._lock_ref.model_copy(update={"released_at": released_at})
            if self._is_console_output():
                click.echo(f"\n\U0001f513  Lock released ({handle.backend_type})")
            self.logger.info("deploy_lock_released", lock_id=handle.lock_id)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(
                "deploy_lock_release_failed",
                lock_id=handle.lock_id,
                error=str(exc),
            )

    # -------------------------------------------------------------------------
    # Hierarchical lifecycle helper
    # -------------------------------------------------------------------------

    def _run_hierarchy_lifecycle_phase(self, phase_name: str, context: Optional[dict] = None) -> bool:
        """Execute a lifecycle phase across the full service hierarchy.

        Traversal order:
          configuration → workspace → namespaces → providers → resources → modules

        Each level runs its own scripts for *phase_name*.  A missing phase at any
        level is silently skipped.  A non-zero script exit at any level aborts the
        remaining levels and returns False.

        Additional ``STRATA_*`` context variables are injected per level:
          - namespace level  → ``STRATA_NAMESPACE``
          - provider level   → ``STRATA_PROVIDER``
          - resource level   → ``STRATA_RESOURCE``
          - module level     → ``STRATA_MODULE``
        """
        from strata.controllers.lifecycle_controller import LifecycleController

        lc = LifecycleController()

        # 1 — configuration level
        if not lc.execute_configuration_phase(phase_name=phase_name, work_path=self._work_path, context=context):
            for err in lc.get_errors():
                self._errors.append(f"Lifecycle hook '{phase_name}' (config) failed: {err}")
            return False

        if self._deployment_service is None:
            return True

        # 2 — workspace level
        workspace = self._deployment_service.get_workspace_service()
        if workspace is not None:
            lc.clear_errors()
            lc.clear_messages()
            if not lc.execute_workspace_phase(
                base_service=workspace, phase_name=phase_name, work_path=self._work_path, context=context
            ):
                for err in lc.get_errors():
                    self._errors.append(f"Lifecycle hook '{phase_name}' (workspace) failed: {err}")
                return False

        # 3 — namespace level
        for ns_name, ns_service in (self._deployment_service.get_namespace_services() or {}).items():
            lc.clear_errors()
            lc.clear_messages()
            ns_ctx = {**(context or {}), "namespace": str(ns_name)}
            if not lc.execute_workspace_phase(
                base_service=ns_service, phase_name=phase_name, work_path=self._work_path, context=ns_ctx
            ):
                for err in lc.get_errors():
                    self._errors.append(f"Lifecycle hook '{phase_name}' (namespace:{ns_name}) failed: {err}")
                return False

        # 4 — provider level
        for prov_name, prov_service in (self._deployment_service.get_provider_services() or {}).items():
            lc.clear_errors()
            lc.clear_messages()
            prov_ctx = {**(context or {}), "provider": str(prov_name)}
            if not lc.execute_workspace_phase(
                base_service=prov_service, phase_name=phase_name, work_path=self._work_path, context=prov_ctx
            ):
                for err in lc.get_errors():
                    self._errors.append(f"Lifecycle hook '{phase_name}' (provider:{prov_name}) failed: {err}")
                return False

        # 5 — resource level
        for res_name, res_service in (self._deployment_service.get_resource_services() or {}).items():
            lc.clear_errors()
            lc.clear_messages()
            res_ctx = {**(context or {}), "resource": str(res_name)}
            if not lc.execute_workspace_phase(
                base_service=res_service, phase_name=phase_name, work_path=self._work_path, context=res_ctx
            ):
                for err in lc.get_errors():
                    self._errors.append(f"Lifecycle hook '{phase_name}' (resource:{res_name}) failed: {err}")
                return False

        # 6 — module level
        for mod_key, mod_service in (self._deployment_service.get_module_services() or {}).items():
            lc.clear_errors()
            lc.clear_messages()
            mod_ctx = {**(context or {}), "module": str(mod_key)}
            if not lc.execute_workspace_phase(
                base_service=mod_service, phase_name=phase_name, work_path=self._work_path, context=mod_ctx
            ):
                for err in lc.get_errors():
                    self._errors.append(f"Lifecycle hook '{phase_name}' (module:{mod_key}) failed: {err}")
                return False

        return True

    # -------------------------------------------------------------------------
    # Deployment lifecycle
    # -------------------------------------------------------------------------

    def _before_execute(self) -> bool:
        """Load and validate the deployment file + configuration service."""
        if not super()._before_execute():
            return False

        # Load user provisioner plugins from .strata/provisioners/
        from strata.deployers.factory import DeployerFactory

        DeployerFactory.load_plugins(self._work_path)

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
        # ADR 0039: resolve spec.extends before loading into DeploymentService.
        from strata.services.deployment_extension_resolver import DeploymentExtensionResolver

        resolver = DeploymentExtensionResolver(work_path=Path(self._work_path), repo_map=repo_map)
        if resolver.needs_resolution(self._file_path):
            try:
                merged_data = resolver.resolve(self._file_path)
            except (ValueError, FileNotFoundError) as exc:
                self._errors.append(f"Deployment extends resolution failed: {exc}")
                return False
            deployment_service = DeploymentService(path=str(self._file_path), data=merged_data)
            deployment_service.validate()
        else:
            deployment_service = DeploymentService.load(str(self._file_path), validate=True)

        if not deployment_service.is_validated():
            self._errors.extend(deployment_service.get_validation_errors())
            return False

        # Pre-flight: reject partial deployments before any infrastructure operation.
        if deployment_service.model and deployment_service.model.spec.partial:
            self._errors.append(
                f"'{self._file_path.name}' is a partial deployment (spec.partial: true) "
                "and cannot be deployed. A leaf deployment file that extends this base is required."
            )
            return False

        # Phase 2: cross-validate against configuration
        config_model = self._configuration_service.model if self._configuration_service else None
        ok, errors = deployment_service.validate(
            configuration_model=config_model,
            work_path=str(self._work_path),
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

    def _create_deployer(self, stage: DeploymentStageModel):
        """Instantiate the deployer for *stage*, or None on failure.

        Resolution order (mutually exclusive):
        - stage.provisioner → named provisioner entry in the workspace YAML
        - stage.topology    → topology name → inferred provisioner type

        Errors are appended to ``self._errors`` and None is returned on failure.
        Subclass attributes ``_force`` and ``_resolved_values`` are consumed when
        present; commands that do not declare them receive safe defaults.
        """
        from strata.deployers.factory import DeployerFactory

        if self._deployment_service is None:
            self._errors.append(f"Stage '{stage.name}': deployment service not loaded.")
            return None

        resolved_type, errors = DeployerFactory.resolve_type(stage, self._deployment_service)
        if errors:
            self._errors.extend(errors)
        if resolved_type is None:
            return None

        _resolved_values = getattr(self, "_resolved_values", None)
        _stage_values = _resolved_values.for_stage(stage.secrets) if _resolved_values else None

        try:
            return DeployerFactory.create(
                resolved_type,
                stage=stage,
                deployment_service=self._deployment_service,  # type: ignore[arg-type]
                configuration_service=self._configuration_service,  # type: ignore[arg-type]
                build_path=self._build_path,
                work_path=self._work_path,
                verbose=self._is_verbose(),
                force=getattr(self, "_force", False),
                resolved_values=_stage_values,
                solution_controller=self._solution_controller,
            )
        except ValueError as exc:
            self._errors.append(str(exc))
            return None

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
        outputs_artifact: Optional[ManifestOutputsReferenceModel] = None,
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
                outputs_artifact=outputs_artifact,
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

        Uses the manifest configuration from ``spec.deployment.manifest`` in
        the platform configuration.  When no manifest config is defined, logs
        an info message and skips writing.

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

        # Check manifest configuration
        manifest_config = self._get_manifest_config()
        if manifest_config is None:
            self.logger.info(
                "Deployment manifest not configured — skipping. "
                "Define spec.deployment.manifest in the configuration to enable manifest storage."
            )
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

            # Deployment identity
            deploy_meta = self._deployment_service.model.meta  # type: ignore[union-attr]
            workspace_service = self._deployment_service.get_workspace_service()
            workspace_name = (
                str(workspace_service.model.meta.name) if workspace_service and workspace_service.model else "unknown"
            )

            # Environment and version from deployment labels
            labels = deploy_meta.labels or {}
            environment = labels.get("environment")

            # Version from deployment labels
            version = labels.get("version")

            # Actor
            deployed_by = (
                os.environ.get("GITHUB_ACTOR") or os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
            )

            # Full artifact BOM
            artifacts = self._collect_artifacts()

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
                    artifacts=artifacts,
                    stages=self._stage_results if self._stage_results else None,
                    policy_results=self._policy_results if self._policy_results else None,
                    lock=self._lock_ref,
                    audit_log=self._audit_log_path,
                ),
            )

            svc = DeploymentManifestService()
            path = svc.save_with_config(
                manifest=manifest,
                manifest_config=manifest_config,
                work_path=self._work_path,
                version=version,
            )
            self.logger.info("Deployment manifest written", path=str(path))

            if manifest_config.push_manifest and path:
                from strata.controllers.manifest_controller import ManifestController

                manifest_ctrl = ManifestController(work_path=self._work_path)
                pushed = manifest_ctrl.push_to_remote([path])
                if pushed:
                    self.logger.info("Deployment manifest pushed to remote", path=str(path))
                else:
                    self.logger.warning("Deployment manifest push failed — continuing", path=str(path))

            return path

        except Exception as exc:
            self.logger.warning("Failed to write deployment manifest", error=str(exc))
            return None

    def _get_manifest_config(self):
        """Retrieve manifest configuration from the configuration service.

        Returns:
            ConfigurationManifestModel or None if not configured.
        """
        if self._configuration_service is None:
            return None
        model = self._configuration_service.model
        if model is None:
            return None
        if model.spec.deployment is None:
            return None
        return model.spec.deployment.manifest

    def _get_outputs_config(self):
        """Retrieve outputs configuration from the configuration service.

        Returns:
            ConfigurationOutputsModel or None if not configured.
        """
        if self._configuration_service is None:
            return None
        model = self._configuration_service.model
        if model is None:
            return None
        if model.spec.deployment is None:
            return None
        return model.spec.deployment.outputs

    def _write_outputs_artifact(
        self,
        stage_name: str,
        non_sensitive: Dict[str, Any],
        sensitive: Dict[str, Any],
    ) -> Optional[Path]:
        """Write per-stage Terraform outputs to the configured durable store.

        Uses ``spec.deployment.outputs`` from the platform configuration.
        When not configured or ``enabled=False`` the call is a no-op.
        Sensitive output handling follows ``outputs.sensitive``:

        * ``redact`` — include the key with value ``"(sensitive)"``
        * ``omit``   — drop the key entirely

        The artifact is written to::

            {work_path}/{outputs.path}/{deployment_name}/{version}/{stage_name}.json

        Non-fatal: write failures are logged as warnings and do not affect
        the deploy outcome.

        Args:
            stage_name: The stage whose outputs are being persisted.
            non_sensitive: Outputs with ``sensitive=false`` from Terraform.
            sensitive: Outputs with ``sensitive=true`` from Terraform.

        Returns:
            Path written, or None when skipped/failed.
        """
        from strata.models.configuration_model import SensitiveOutputHandling

        outputs_config = self._get_outputs_config()
        if outputs_config is None:
            self.logger.debug("Outputs artifact not configured — skipping", stage=stage_name)
            return None
        if not outputs_config.enabled:
            self.logger.debug("Outputs artifact disabled — skipping", stage=stage_name)
            return None

        if self._deployment_service is None:
            return None

        try:
            deploy_meta = self._deployment_service.model.meta  # type: ignore[union-attr]
            deployment_name = str(deploy_meta.name)
            labels = deploy_meta.labels or {}
            version = str(labels.get("version", "unknown"))

            # Apply sensitive handling
            if outputs_config.sensitive == SensitiveOutputHandling.OMIT:
                stored: Dict[str, Any] = dict(non_sensitive)
            else:  # REDACT (default)
                stored = dict(non_sensitive)
                for key in sensitive:
                    stored[key] = "(sensitive)"

            artifact_path = self._work_path / outputs_config.path / deployment_name / version / f"{stage_name}.json"
            artifact_path.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "deployment": deployment_name,
                "version": version,
                "stage": stage_name,
                "written_at": datetime.now(timezone.utc).isoformat(),
                "outputs": stored,
            }
            with open(artifact_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, default=str)

            self.logger.info(
                "Outputs artifact written",
                stage=stage_name,
                path=str(artifact_path),
                output_count=len(non_sensitive),
                sensitive_count=len(sensitive),
            )
            return artifact_path

        except Exception as exc:
            self.logger.warning("Failed to write outputs artifact", stage=stage_name, error=str(exc))
            return None

    def _collect_artifacts(self) -> ManifestArtifactsModel:
        """Assemble the full artifact BOM from available runtime data."""
        return ManifestArtifactsModel(
            platform=self._collect_platform_artifact(),
            repositories=self._collect_repository_info(),
            providers=self._collect_provider_info(),
            images=self._collect_image_info(),
        )

    def _collect_platform_artifact(self) -> ManifestPlatformModel:
        """Compute SHA-256 of platform.json and embed its full content."""
        if self._deployment_service is None:
            return ManifestPlatformModel(hash="unknown")

        platform_path = self._deployment_service.get_build_path(self._build_path) / "platform.json"
        if not platform_path.exists():
            return ManifestPlatformModel(hash="unknown")

        import json as _json

        content_bytes = platform_path.read_bytes()
        digest = hashlib.sha256(content_bytes).hexdigest()
        rel_path = str(platform_path.relative_to(self._work_path))
        try:
            content = _json.loads(content_bytes.decode("utf-8"))
        except Exception:
            content = None

        return ManifestPlatformModel(hash=f"sha256:{digest}", path=rel_path, content=content)

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

    def _collect_provider_info(self) -> Optional[List[ManifestArtifactProviderModel]]:
        """Collect provisioner metadata from the workspace model.

        Walks ``workspace.spec.provisioners`` and captures each provisioner's
        name, tool type, and state backend configuration.
        """
        if self._deployment_service is None:
            return None
        workspace_service = self._deployment_service.get_workspace_service()
        if workspace_service is None or workspace_service.model is None:
            return None

        provisioners = getattr(workspace_service.model.spec, "provisioners", None) or []
        if not provisioners:
            return None

        result: List[ManifestArtifactProviderModel] = []
        for prov in provisioners:
            backend_dict: Optional[Dict[str, Any]] = None
            if getattr(prov, "backend", None) is not None:
                backend_dict = {
                    "type": prov.backend.type,
                    "configuration": prov.backend.configuration,
                }

            details: Optional[Dict[str, Any]] = None
            if getattr(prov, "properties", None) is not None:
                details = prov.properties.model_dump(exclude_none=True)

            result.append(
                ManifestArtifactProviderModel(
                    name=str(prov.name),
                    type=prov.provisioner,
                    backend=backend_dict,
                    details=details,
                )
            )

        return result if result else None

    def _collect_image_info(self) -> Optional[List[ManifestArtifactImageModel]]:
        """Collect container image references from stage outputs.

        Compose stages emit service image references in their outputs under
        the ``services`` key.  This method walks all recorded stage results
        and extracts any image data found there.
        """
        images: List[ManifestArtifactImageModel] = []
        for stage in self._stage_results:
            if not stage.outputs:
                continue
            services = stage.outputs.get("services")
            if not isinstance(services, list):
                continue
            for svc in services:
                if not isinstance(svc, dict):
                    continue
                name = svc.get("name") or svc.get("service")
                image = svc.get("image")
                if name and image:
                    images.append(
                        ManifestArtifactImageModel(
                            name=str(name),
                            image=str(image),
                            digest=svc.get("digest"),
                        )
                    )
        return images if images else None
