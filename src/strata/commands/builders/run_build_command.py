"""Command to execute the platform build pipeline."""

from typing import Optional

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
    ):
        super().__init__(
            file=file,
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self._dry_run = dry_run

    def get_required_integrations(self):
        return {}

    def execute(self) -> bool:
        try:
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

        return True
