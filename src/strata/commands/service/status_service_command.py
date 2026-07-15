"""Show runtime status of a service (namespace or module)."""

import click

from strata.commands.service.base_service_command import BaseServiceCommand, ServiceTarget
from strata.models.common_models import ServiceDeployerType
from strata.models.integration_model import IntegrationModel


class StatusServiceCommand(BaseServiceCommand):
    """Show runtime status of a service by name."""

    OPERATION = "service_status"

    def _execute(self) -> bool:
        if not self._name:
            self._errors.append("Service name is required. Use: strata service status <name>")
            return False

        targets, errors = self._resolve_targets_by_name(self._name)
        if errors:
            self._errors.extend(errors)
            if self._is_console_output():
                for e in errors:
                    click.echo(f"  ❌  {e}")
            return False

        all_ok = True
        for target in targets:
            ok = self._show_status(target)
            if not ok:
                all_ok = False

        return all_ok

    def _show_status(self, target: ServiceTarget) -> bool:
        """Query status for a single service target."""
        if self._is_console_output():
            click.echo(f"\n  [{target.namespace}/{target.module}] type={target.deployer_type.value}")

        if target.deployer_type == ServiceDeployerType.COMPOSE:
            return self._status_compose(target)
        elif target.deployer_type == ServiceDeployerType.HELM:
            return self._status_helm(target)
        else:
            if self._is_console_output():
                click.echo(f"    ⚠️  Status not supported for deployer type: {target.deployer_type.value}")
            return True

    def _status_compose(self, target: ServiceTarget) -> bool:
        """Show docker service status for a compose-deployed namespace."""
        from strata.integrations.docker import DockerIntegration

        docker = DockerIntegration(config=IntegrationModel(name="docker", type="docker"))
        available, error = docker.ensure_available()
        if not available:
            if self._is_console_output():
                click.echo(f"    ❌  Docker not available: {error}")
            return False

        result = docker._run_integration(
            ["stack", "services", target.namespace, "--format", "table {{.Name}}\t{{.Replicas}}\t{{.Image}}"],
            cwd=str(self._work_path),
            timeout=30,
        )
        if result.returncode != 0:
            if self._is_console_output():
                click.echo(f"    ⚠️  Stack '{target.namespace}' not deployed or not reachable.")
            return True  # Not an error — stack may not exist yet
        if self._is_console_output() and result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                click.echo(f"    {line}")
        return True

    def _status_helm(self, target: ServiceTarget) -> bool:
        """Show helm release status."""
        from strata.integrations.helm import HelmIntegration

        helm = HelmIntegration(config=IntegrationModel(name="helm", type="helm"))
        available, error = helm.ensure_available()
        if not available:
            if self._is_console_output():
                click.echo(f"    ❌  Helm not available: {error}")
            return False

        release_name = target.module or target.namespace
        result = helm._run_integration(
            ["status", release_name, "-n", target.namespace, "--show-desc"],
            cwd=str(self._work_path),
            timeout=30,
        )
        if result.returncode != 0:
            if self._is_console_output():
                click.echo(f"    ⚠️  Release '{release_name}' not found in namespace '{target.namespace}'.")
            return True  # Not an error — release may not exist yet
        if self._is_console_output() and result.stdout.strip():
            for line in result.stdout.strip().splitlines()[:10]:
                click.echo(f"    {line}")
        return True
