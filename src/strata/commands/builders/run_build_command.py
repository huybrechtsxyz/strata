"""Command to execute the platform build pipeline."""

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
from strata.builders.sync_builder import SyncBuilder
from strata.builders.terraform_builder import TerraformBuilder
from strata.commands.builders.base_build_command import BaseBuildCommand
from strata.services.manifest_artifact_collector import (
    collect_platform_artifact,
    collect_provider_info,
    collect_repository_info,
)


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
        require_lock: bool = False,
        ai: bool = False,
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
        self._require_lock = require_lock
        self._ai = ai
        self._build_started_at: Optional[str] = None
        self._sbom_reference = None
        self._policy_results: List[Dict[str, Any]] = []

    def get_required_integrations(self):
        return {}

    def _execute(self) -> bool:
        try:
            self._build_started_at = datetime.now(timezone.utc).isoformat()

            # Strict lock mode check (--require-lock or ring.require_lock: true)
            if self._deployment_service is not None:
                config_model = self._configuration_service.model if self._configuration_service else None
                lock_error = self._deployment_service.check_require_lock_mode(
                    self._work_path, config_model, flag=self._require_lock
                )
                if lock_error:
                    self._errors.append(lock_error)
                    if self._is_console_output():
                        click.echo(f"\n❌  {lock_error}")
                    return False

            if self._dry_run and self._is_console_output():
                click.echo("\n[DRY-RUN] Validating and planning build — no files will be written")

            if not self._run_lifecycle_phase(
                "build_run_before",
                context={"file": str(self._file_path), "dry_run": self._dry_run},
            ):
                if self._is_console_output():
                    click.echo("\n❌  Pre-build lifecycle hook failed")
                return False
            if not self._run_lifecycle_phase(
                "build_validate",
                context={"file": str(self._file_path), "dry_run": self._dry_run},
            ):
                if self._is_console_output():
                    click.echo("\n\u274c  Build validate lifecycle hook failed")
                return False
            for _phase, _phase_fn in (
                ("platform", self._execute_platform_build),
                ("terraform", self._execute_terraform_build),
                ("ansible", self._execute_ansible_build),
                ("compose", self._execute_compose_build),
                ("helm", self._execute_helm_build),
                ("sync", self._execute_sync_build),
                ("sbom", self._execute_sbom_build),
            ):
                if self._is_ndjson_output():
                    self.emit_ndjson(
                        {
                            "event": "stage_start",
                            "stage": f"{_phase}_build",
                            "ts": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                _phase_ok = _phase_fn()
                if self._is_ndjson_output():
                    self.emit_ndjson(
                        {
                            "event": "stage_end",
                            "stage": f"{_phase}_build",
                            "success": _phase_ok,
                            "ts": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                if not _phase_ok:
                    if self._is_console_output():
                        click.echo(f"\n❌  {_phase.capitalize()} build failed")
                    return False

            if self._audit and not self._dry_run:
                sbom_path = (
                    self._deployment_service.get_build_path(self._build_path) / "sbom.json"
                    if self._deployment_service
                    else None
                )
                if sbom_path and sbom_path.exists():
                    if not self._execute_audit(sbom_path):
                        return False
                    if self._ai and self._cve_audit_result and self._cve_audit_result.total_findings > 0:
                        self._run_ai_cve_analysis()

            if not self._run_lifecycle_phase(
                "build_generate",
                context={"file": str(self._file_path), "dry_run": self._dry_run},
            ):
                if self._is_console_output():
                    click.echo("\n\u274c  Build generate lifecycle hook failed")
                return False
            if not self._evaluate_build_policies():
                if self._is_console_output():
                    click.echo("\n\u274c  Build policy check failed")
                return False
            if not self._run_lifecycle_phase(
                "build_run_after",
                context={"file": str(self._file_path), "dry_run": self._dry_run},
            ):
                if self._is_console_output():
                    click.echo("\n❌  Post-build lifecycle hook failed")
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

            return True

        except Exception as exc:
            self._errors.append(f"Failed to execute build_run: {exc}")
            self.logger.exception("build_run failed")
            return False

    def _load_related_services(self) -> bool:
        """Services are already loaded by BaseBuildCommand._before_execute."""
        return True

    # ------------------------------------------------------------------
    # AI CVE analysis
    # ------------------------------------------------------------------

    def _run_ai_cve_analysis(self) -> None:
        """Explain CVE findings from the audit scan using the configured AI integration."""
        from strata.integrations.ai import find_ai_integration

        integration = find_ai_integration(self._configuration_service)
        if integration is None:
            if self._is_console_output():
                click.echo("  ⚠  --ai flag set but no ai_agent integration configured")
            return
        ok, msg = integration.ensure_available()
        if not ok:
            self._messages.append(f"AI provider unavailable: {msg}")
            return

        result = self._cve_audit_result
        if result is None:
            return

        # Build a serialisable dict including the full findings list
        cve_data = {
            "scanner": result.scanner,
            "scanner_version": result.scanner_version,
            "total_findings": result.total_findings,
            "critical": result.critical,
            "high": result.high,
            "medium": result.medium,
            "low": result.low,
            "unknown": result.unknown,
            "findings": [
                {
                    "vulnerability_id": f.vulnerability_id,
                    "severity": f.severity,
                    "package_name": f.package_name,
                    "installed_version": f.installed_version,
                    "fixed_version": f.fixed_version,
                    "title": f.title,
                }
                for f in result.findings
            ],
        }
        deployment_name = (
            str(self._deployment_service.model.meta.name)  # type: ignore[union-attr]
            if self._deployment_service and self._deployment_service.model
            else "unknown"
        )
        context = {"deployment": deployment_name, "work_path": str(self._work_path)}

        if self._is_console_output():
            click.echo(f"\n  🤖  AI CVE analysis ({integration.integration_name}) …")

        try:
            response = integration.analyse_cve_results(cve_data, context)
        except Exception as exc:
            self._messages.append(f"AI CVE analysis failed: {exc}")
            return

        self._output_data["ai_analysis"] = {
            "provider": response.provider,
            "model": response.model,
            "content": response.content,
        }

        if self._is_console_output():
            self._print_ai_cve(response.content)

    def _print_ai_cve(self, content: str) -> None:
        import json as _json

        sep = "\u2500" * 48
        click.echo(f"\n  {sep}")
        click.echo("  🤖  AI CVE Analysis")
        click.echo(f"  {sep}")
        try:
            parsed = _json.loads(content)
            risk = str(parsed.get("risk", "?")).upper()
            risk_icon = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}.get(risk, "⚪")
            click.echo(f"\n  {risk_icon}  {parsed.get('summary', '')}")
            no_fix = parsed.get("no_fix_count", 0)
            if no_fix:
                click.echo(f"  ⚠  {no_fix} finding(s) have no available fix")
            if parsed.get("priorities"):
                click.echo("\n  Priority upgrades:")
                for pkg in parsed["priorities"]:
                    click.echo(f"    • {pkg}")
            if parsed.get("recommendations"):
                click.echo("\n  Recommendations:")
                for r in parsed["recommendations"]:
                    click.echo(f"    → {r}")
        except (_json.JSONDecodeError, TypeError):
            click.echo(content)
        click.echo("")

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

    def _execute_sync_build(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False
        builder = SyncBuilder(
            verbose=self._is_verbose(),
            configuration_service=self._configuration_service,
        )

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
        return collect_platform_artifact(self._deployment_service, self._build_path, self._work_path)

    def _collect_repository_info(self) -> Optional[Dict[str, Any]]:
        """Walk solution repositories and collect URL/ref/commit info."""
        return collect_repository_info(self._solution_controller)

    def _collect_provider_info(self) -> Optional[List[Any]]:
        """Collect provisioner metadata from the workspace model."""
        return collect_provider_info(self._deployment_service)
