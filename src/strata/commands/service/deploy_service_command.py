"""Deploy a single service (namespace or module) by name."""

from typing import Optional

import click

from strata.commands.service.base_service_command import BaseServiceCommand, ServiceTarget
from strata.controllers.value_controller import ResolvedValues, ValueController
from strata.models.common_models import ServiceDeployerType
from strata.models.integration_model import IntegrationModel


class DeployServiceCommand(BaseServiceCommand):
    """Deploy (or redeploy) a single service by name."""

    OPERATION = "service_deploy"

    def __init__(
        self,
        file: Optional[str] = None,
        name: Optional[str] = None,
        work_path: Optional[str] = None,
        force: bool = False,
        dry_run: bool = False,
        ai: bool = False,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ):
        super().__init__(
            file=file,
            name=name,
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self._force = force
        self._dry_run = dry_run
        self._ai = ai
        self._resolved_values: Optional[ResolvedValues] = None

    def _execute(self) -> bool:
        if not self._name:
            self._errors.append("Service name is required. Use: strata service deploy <name>")
            return False

        targets, errors = self._resolve_targets_by_name(self._name)
        if errors:
            self._errors.extend(errors)
            if self._is_console_output():
                for e in errors:
                    click.echo(f"  ❌  {e}")
            return False

        # Resolve values (variables, secrets, features)
        if not self._resolve_values():
            return False

        if self._is_console_output():
            prefix = "[DRY-RUN] " if self._dry_run else ""
            click.echo(f"\n{prefix}Deploying {len(targets)} service(s)…")

        all_ok = True
        for target in targets:
            errors_before = list(self._errors)
            ok = self._deploy_target(target)
            if not ok:
                all_ok = False
                # Collect errors added by this target's deployment
                new_errors = self._errors[len(errors_before) :]
                if self._ai and new_errors:
                    self._run_ai_failure_diagnosis(
                        error_output="\n".join(new_errors),
                        step=target.deployer_type.value,
                        target=target,
                    )
                if not self._force:
                    break

        if all_ok and self._is_console_output() and not self._dry_run:
            click.echo("\n✅  Service deployment completed.")

        return all_ok

    def _resolve_values(self) -> bool:
        """Resolve variables, secrets, and feature flags."""
        controller = ValueController()
        ok, resolved, errors = controller.resolve_values(
            self._deployment_service,  # type: ignore[arg-type]
            strict=False,
        )
        self._resolved_values = resolved
        if errors:
            for err in errors:
                self.logger.warning("Value resolution warning: %s", err)
        return True

    def _deploy_target(self, target: ServiceTarget) -> bool:
        """Deploy a single service target using the appropriate integration."""
        if self._is_console_output():
            prefix = "[DRY-RUN] " if self._dry_run else ""
            click.echo(f"\n  ▶  {prefix}{target.namespace}/{target.module} ({target.deployer_type.value})")

        if target.deployer_type == ServiceDeployerType.COMPOSE:
            return self._deploy_compose(target)
        elif target.deployer_type == ServiceDeployerType.HELM:
            return self._deploy_helm(target)
        elif target.deployer_type == ServiceDeployerType.SCRIPT:
            return self._deploy_script(target)
        else:
            if self._is_console_output():
                click.echo(f"    ⚠️  Deployer type '{target.deployer_type.value}' not yet supported for service deploy.")
            return True

    def _deploy_compose(self, target: ServiceTarget) -> bool:
        """Deploy a compose service via docker stack deploy."""
        from strata.controllers.value_controller import inject_compose_env
        from strata.integrations.docker import DockerIntegration

        docker = DockerIntegration(config=IntegrationModel(name="docker", type="docker"))
        available, error = docker.ensure_available()
        if not available:
            self._errors.append(f"Docker not available: {error}")
            return False

        # Compose file is at the namespace level
        ns_build_path = target.build_path.parent  # Up from namespace/module to namespace
        compose_file = ns_build_path / "docker-compose.yml"
        if not compose_file.exists():
            self._errors.append(f"Compose file not found: {compose_file}")
            return False

        if self._dry_run:
            if self._is_console_output():
                click.echo(f"    Would run: docker stack deploy -c {compose_file} {target.namespace}")
            return True

        if self._is_console_output():
            click.echo(f"    docker stack deploy {target.namespace}")

        with inject_compose_env(self._resolved_values):
            result = docker._run_integration(
                ["stack", "deploy", "--with-registry-auth", "-c", str(compose_file), target.namespace],
                cwd=str(ns_build_path),
                timeout=300,
            )

        if result.returncode != 0:
            output = "\n".join(filter(None, [result.stderr, result.stdout]))
            self._errors.append(f"docker stack deploy failed:\n{output}")
            return False

        if self._is_console_output():
            click.echo(f"    ✓  Stack '{target.namespace}' deployed.")
        return True

    def _deploy_helm(self, target: ServiceTarget) -> bool:
        """Deploy a helm release via helm upgrade --install."""
        from strata.integrations.helm import HelmIntegration

        helm = HelmIntegration(config=IntegrationModel(name="helm", type="helm"))
        available, error = helm.ensure_available()
        if not available:
            self._errors.append(f"Helm not available: {error}")
            return False

        values_file = target.build_path / "values.yaml"
        meta_file = target.build_path / "meta.yaml"

        if not values_file.exists():
            self._errors.append(f"Helm values file not found: {values_file}")
            return False

        # Read meta.yaml for release name and chart reference
        import yaml

        release_name = target.module or target.namespace
        chart_ref = ""
        if meta_file.exists():
            try:
                with meta_file.open("r", encoding="utf-8") as fh:
                    meta = yaml.safe_load(fh) or {}
                release_name = meta.get("releaseName", release_name)
                chart_ref = meta.get("chart", "")
            except Exception:
                pass

        if not chart_ref:
            self._errors.append(f"Cannot determine chart reference for {target.namespace}/{target.module}")
            return False

        if self._dry_run:
            if self._is_console_output():
                click.echo(
                    f"    Would run: helm upgrade --install {release_name} {chart_ref} "
                    f"-n {target.namespace} -f {values_file}"
                )
            return True

        if self._is_console_output():
            click.echo(f"    helm upgrade --install {release_name} -n {target.namespace}")

        args = [
            "upgrade",
            "--install",
            release_name,
            chart_ref,
            "-n",
            target.namespace,
            "-f",
            str(values_file),
            "--create-namespace",
        ]
        result = helm._run_integration(args, cwd=str(target.build_path), timeout=300)

        if result.returncode != 0:
            output = "\n".join(filter(None, [result.stderr, result.stdout]))
            self._errors.append(f"helm upgrade --install failed:\n{output}")
            return False

        if self._is_console_output():
            click.echo(f"    ✓  Release '{release_name}' deployed to namespace '{target.namespace}'.")
        return True

    def _deploy_script(self, target: ServiceTarget) -> bool:
        """Deploy using a script-based module."""
        if self._is_console_output():
            click.echo(f"    ⚠️  Script-based service deploy not yet implemented for {target.namespace}/{target.module}")
        return True

    # ------------------------------------------------------------------
    # AI failure diagnosis
    # ------------------------------------------------------------------

    def _run_ai_failure_diagnosis(self, error_output: str, step: str, target: ServiceTarget) -> None:
        """Call AI failure diagnosis after a service deploy step fails."""
        from strata.integrations.ai import find_ai_integration

        integration = find_ai_integration(self._configuration_service)
        if integration is None or not integration.ensure_available()[0]:
            return

        deployment_name = (
            str(self._deployment_service.model.meta.name)  # type: ignore[union-attr]
            if self._deployment_service and self._deployment_service.model
            else "unknown"
        )
        context = {
            "deployment": deployment_name,
            "stage": f"{target.namespace}/{target.module or ''}",
            "provisioner": step,
            "work_path": str(self._work_path),
        }

        if self._is_console_output():
            click.echo(f"\n  🤖  AI failure diagnosis ({integration.integration_name}) …")

        try:
            response = integration.diagnose_failure(error_output, step, context)
        except Exception as exc:
            self._messages.append(f"AI failure diagnosis failed: {exc}")
            return

        if self._is_console_output():
            self._print_ai_diagnosis(response.content)

    def _print_ai_diagnosis(self, content: str) -> None:
        import json as _json

        try:
            parsed = _json.loads(content)
            category = parsed.get("category", "unknown").upper()
            click.echo(f"\n  🔍  Root cause [{category}]: {parsed.get('root_cause', '')}")
            if parsed.get("remediation"):
                click.echo("  Remediation:")
                for i, step in enumerate(parsed["remediation"], 1):
                    click.echo(f"    {i}. {step}")
        except (_json.JSONDecodeError, TypeError):
            click.echo(content)
        click.echo("")
