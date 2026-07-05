"""Command to execute the platform build pipeline."""

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import click

from strata.builders.ansible_builder import AnsibleBuilder
from strata.builders.compose_builder import ComposeBuilder
from strata.builders.helm_builder import HelmBuilder
from strata.builders.platform_builder import PlatformBuilder
from strata.builders.sbom_builder import SbomBuilder
from strata.builders.terraform_builder import TerraformBuilder
from strata.commands.builders.base_build_command import BaseBuildCommand


class RunBuildCommand(BaseBuildCommand):
    """Run build pipeline (platform + terraform)."""

    OPERATION = "build_run"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        dry_run: bool = False,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
        audit: bool = False,
        audit_severity: str = "MEDIUM",
        fail_on: Optional[str] = None,
        audit_report: Optional[str] = None,
    ):
        super().__init__(
            file=file,
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self._dry_run = dry_run
        self._audit = audit
        self._audit_severity = audit_severity
        self._fail_on = fail_on
        self._audit_report = audit_report
        self._build_started_at: Optional[str] = None
        self._sbom_reference = None
        self._policy_results: List[Dict[str, Any]] = []

    def get_required_integrations(self):
        return {}

    def execute(self) -> bool:
        try:
            self._build_started_at = datetime.now(timezone.utc).isoformat()

            if not self._initialize():
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(success=False)
                return False

            if not self._before_execute():
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(success=False)
                return False

            if not self._load_related_services():
                if self._is_console_output():
                    click.echo("\n❌  Failed to load deployment related services")
                self._finalize(success=False)
                return False

            if self._dry_run and self._is_console_output():
                click.echo("\n[DRY-RUN] Validating and planning build — no files will be written")

            if not self._run_lifecycle_phase(
                "build_run_before",
                context={"file": str(self._file_path), "dry_run": self._dry_run},
            ):
                if self._is_console_output():
                    click.echo("\n❌  Pre-build lifecycle hook failed")
                self._finalize(success=False)
                return False
            if not self._run_lifecycle_phase(
                "build_validate",
                context={"file": str(self._file_path), "dry_run": self._dry_run},
            ):
                if self._is_console_output():
                    click.echo("\n\u274c  Build validate lifecycle hook failed")
                self._finalize(success=False)
                return False
            if not self._execute_platform_build():
                if self._is_console_output():
                    click.echo("\n❌  Platform build failed")
                self._finalize(success=False)
                return False

            if not self._execute_terraform_build():
                if self._is_console_output():
                    click.echo("\n❌  Terraform build failed")
                self._finalize(success=False)
                return False

            if not self._execute_ansible_build():
                if self._is_console_output():
                    click.echo("\n❌  Ansible build failed")
                self._finalize(success=False)
                return False

            if not self._execute_compose_build():
                if self._is_console_output():
                    click.echo("\n❌  Compose build failed")
                self._finalize(success=False)
                return False

            if not self._execute_helm_build():
                if self._is_console_output():
                    click.echo("\n❌  Helm build failed")
                self._finalize(success=False)
                return False

            if not self._execute_sbom_build():
                if self._is_console_output():
                    click.echo("\n❌  SBOM build failed")
                self._finalize(success=False)
                return False

            if self._audit and not self._dry_run:
                sbom_path = (
                    self._deployment_service.get_build_path(self._build_path) / "sbom.json"
                    if self._deployment_service
                    else None
                )
                if sbom_path and sbom_path.exists():
                    if not self._execute_audit(sbom_path):
                        self._finalize(success=False)
                        return False

            if not self._run_lifecycle_phase(
                "build_generate",
                context={"file": str(self._file_path), "dry_run": self._dry_run},
            ):
                if self._is_console_output():
                    click.echo("\n\u274c  Build generate lifecycle hook failed")
                self._finalize(success=False)
                return False
            if not self._evaluate_build_policies():
                if self._is_console_output():
                    click.echo("\n\u274c  Build policy check failed")
                self._finalize(success=False)
                return False
            if not self._run_lifecycle_phase(
                "build_run_after",
                context={"file": str(self._file_path), "dry_run": self._dry_run},
            ):
                if self._is_console_output():
                    click.echo("\n❌  Post-build lifecycle hook failed")
                self._finalize(success=False)
                return False

            if not self._after_execute():
                if self._is_console_output():
                    click.echo("\n❌  Post-execution hook failed")
                self._finalize(success=False)
                return False

            self._output_data.update(
                {
                    "file": str(self._file_path),
                    "build_path": str(self._build_path),
                    "dry_run": self._dry_run,
                }
            )

            # Write build manifest as the final build artifact
            manifest_path = self._write_build_manifest()
            if manifest_path:
                self._output_data["manifest_path"] = str(manifest_path)

            self._finalize(success=True)
            return True

        except Exception as exc:
            self._errors.append(f"Failed to execute build_run: {exc}")
            self.logger.exception("build_run failed")
            self._finalize(success=False)
            return False

    def _load_related_services(self) -> bool:
        """Services are already loaded by BaseBuildCommand._before_execute."""
        return True

    def _evaluate_build_policies(self) -> bool:
        """Evaluate 'build' phase policies. Returns False if any deny-enforcement policy fails."""
        from strata.validators.policies.base_policy import PolicyContext
        from strata.validators.policies.policy_engine import PolicyEngine

        if self._configuration_service is None:
            return True

        spec = self._configuration_service.model.spec if self._configuration_service.model else None
        policy_models = getattr(spec, "policies", None) or []
        build_policies = [p for p in policy_models if p.phase == "build" and p.enabled]
        if not build_policies:
            return True

        platform_artifact = getattr(self, "_platform_model", None)

        context = PolicyContext(
            phase="build",
            work_path=self._work_path,
            configuration_service=self._configuration_service,
            platform_artifact=platform_artifact,
            build_path=self._build_path,
            sbom_components=getattr(self, "_sbom_components", None),
            cve_audit_result=getattr(self, "_cve_audit_result", None),
        )

        engine = PolicyEngine(build_policies)
        results = engine.evaluate("build", context)

        denied = False
        for result in results:
            if result.passed:
                if self._is_verbose() and self._is_console_output():
                    click.echo(f"    \u2713  Policy '{result.policy_name}' passed")
            else:
                for v in result.violations:
                    if result.enforcement == "deny":
                        click.echo(f"    \u2717  Policy '{result.policy_name}' DENIED: {v}")
                        self._errors.append(f"Policy '{result.policy_name}': {v}")
                        denied = True
                    elif result.enforcement == "warn":
                        click.echo(f"    \u26a0  Policy '{result.policy_name}' warning: {v}")
                    elif result.enforcement == "audit" and self._is_verbose():
                        click.echo(f"    \u00b7  Policy '{result.policy_name}' audit: {v}")

        # Record policy results for the build manifest
        for result in results:
            self._policy_results.append(
                {
                    "policy_name": result.policy_name,
                    "policy_type": result.policy_type,
                    "phase": "build",
                    "enforcement": result.enforcement,
                    "passed": result.passed,
                    "violations": result.violations,
                }
            )

        return not denied

    def _execute_platform_build(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False
        builder = PlatformBuilder(
            verbose=self._is_verbose(),
            configuration_service=self._configuration_service,
        )

        ok = builder.before_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        ok = builder.build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        # Store assembled model so terraform builder can reuse it in dry-run
        self._platform_model = builder._last_platform_model

        ok = builder.after_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        return True

    def _execute_terraform_build(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False
        builder = TerraformBuilder(verbose=self._is_verbose())

        repo_map = self._solution_controller.get_repo_map() if self._solution_controller is not None else {}

        ok = builder.before_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        ok = builder.build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
            platform_model=getattr(self, "_platform_model", None),
            repo_map=repo_map,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        ok = builder.after_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        return True

    def _execute_ansible_build(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False
        builder = AnsibleBuilder(verbose=self._is_verbose())

        repo_map = self._solution_controller.get_repo_map() if self._solution_controller is not None else {}

        ok = builder.before_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        ok = builder.build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
            platform_model=getattr(self, "_platform_model", None),
            repo_map=repo_map,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        ok = builder.after_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        return True

    def _execute_compose_build(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False
        builder = ComposeBuilder(verbose=self._is_verbose())

        ok = builder.before_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        ok = builder.build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        ok = builder.after_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        return True

    def _execute_helm_build(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False
        builder = HelmBuilder(verbose=self._is_verbose())

        repo_map = self._solution_controller.get_repo_map() if self._solution_controller is not None else {}

        ok = builder.before_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        ok = builder.build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
            repo_map=repo_map,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        ok = builder.after_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        return True

    def _execute_sbom_build(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False
        builder = SbomBuilder(verbose=self._is_verbose())

        ok = builder.before_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        ok = builder.build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
            platform_model=getattr(self, "_platform_model", None),
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        ok = builder.after_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        # Store components for policy evaluation
        self._sbom_components = builder.last_components
        self._sbom_reference = builder.sbom_reference

        return True

    # ------------------------------------------------------------------
    # Build manifest
    # ------------------------------------------------------------------

    def _write_build_manifest(self) -> Optional[Path]:
        """Assemble and persist a build manifest alongside the build artifacts.

        The build manifest captures the complete bill of materials at build
        time: platform artifact hash, pinned repository commits, provisioner
        metadata, SBOM reference, policy evaluation results, and build
        environment details.  It is written to
        ``{build_path}/{deployment_name}/manifest.json``.

        Returns:
            Path to the written manifest, or None on skip/error.
        """
        if self._dry_run:
            self.logger.debug("Dry-run — skipping build manifest write")
            return None

        if self._deployment_service is None:
            self.logger.warning("Cannot write build manifest — deployment service not loaded")
            return None

        try:
            from strata.models.deployment_manifest_model import (
                DeploymentManifestMetaModel,
                DeploymentManifestModel,
                DeploymentManifestSpecModel,
                ManifestArtifactsModel,
                ManifestPolicyResultModel,
            )

            completed_at = datetime.now(timezone.utc).isoformat()
            started_at = self._build_started_at or completed_at

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

            labels = deploy_meta.labels or {}
            environment = labels.get("environment")
            version = labels.get("version")

            # Actor
            built_by = (
                os.environ.get("GITHUB_ACTOR") or os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
            )

            # Artifact BOM
            artifacts = ManifestArtifactsModel(
                platform=self._collect_platform_artifact(),
                repositories=self._collect_repository_info(),
                providers=self._collect_provider_info(),
            )

            # Policy results
            policy_results: Optional[List[ManifestPolicyResultModel]] = None
            if self._policy_results:
                policy_results = [ManifestPolicyResultModel(**pr) for pr in self._policy_results]

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
                    action="build",
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_seconds=duration,
                    status="success",
                    dry_run=False,
                    deployed_by=built_by,
                    artifacts=artifacts,
                    sbom=self._sbom_reference,
                    policy_results=policy_results,
                ),
            )

            # Write to build output directory
            deployment_build_path = self._deployment_service.get_build_path(self._build_path)
            deployment_build_path.mkdir(parents=True, exist_ok=True)
            manifest_path = deployment_build_path / "manifest.json"
            manifest_path.write_text(
                manifest.model_dump_json(indent=2, exclude_none=True),
                encoding="utf-8",
            )

            self.logger.info("Build manifest written", path=str(manifest_path))

            if self._is_console_output():
                click.echo(f"\n📋  Build manifest: {manifest_path}")

            # Also write to the configured manifest store (if enabled)
            manifest_config = self._get_manifest_config()
            if manifest_config is not None:
                from strata.services.deployment_manifest_service import DeploymentManifestService

                svc = DeploymentManifestService()
                stored_path = svc.save_with_config(
                    manifest=manifest,
                    manifest_config=manifest_config,
                    work_path=self._work_path,
                    version=version,
                )
                self.logger.info("Build manifest stored", path=str(stored_path))

            return manifest_path

        except Exception as exc:
            self.logger.warning("Failed to write build manifest", error=str(exc))
            return None

    def _get_manifest_config(self):
        """Retrieve manifest configuration from the configuration service."""
        if self._configuration_service is None:
            return None
        model = self._configuration_service.model
        if model is None:
            return None
        if model.spec.deployment is None:
            return None
        return model.spec.deployment.manifest

    def _collect_platform_artifact(self):
        """Compute SHA-256 of platform.json and embed its content."""
        from strata.models.deployment_manifest_model import ManifestPlatformModel

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

    def _collect_repository_info(self) -> Optional[Dict[str, Any]]:
        """Walk solution repositories and collect URL/ref/commit info."""
        from strata.models.deployment_manifest_model import ManifestRepositoryModel

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
                            commit = head_content
                    except OSError:
                        pass

            result[name] = ManifestRepositoryModel(
                url=str(url) if url else None,
                ref=str(ref) if ref else None,
                commit=commit,
            )

        return result if result else None

    def _collect_provider_info(self) -> Optional[List[Any]]:
        """Collect provisioner metadata from the workspace model."""
        from strata.models.deployment_manifest_model import ManifestArtifactProviderModel

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
