"""Destroy a single service (namespace or module) by name."""

from typing import Optional

import click

from strata.commands.service.base_service_command import BaseServiceCommand, ServiceTarget
from strata.models.common_models import ServiceDeployerType
from strata.models.integration_model import IntegrationModel


class DestroyServiceCommand(BaseServiceCommand):
    """Tear down a single service by name."""

    OPERATION = "service_destroy"

    def __init__(
        self,
        file: Optional[str] = None,
        name: Optional[str] = None,
        work_path: Optional[str] = None,
        force: bool = False,
        dry_run: bool = False,
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

    def _execute(self) -> bool:
        if not self._name:
            self._errors.append("Service name is required. Use: strata service destroy <name>")
            return False

        if not self._force and not self._dry_run:
            self._errors.append("--force is required for destroy (or use --dry-run to preview).")
            return False

        targets, errors = self._resolve_targets_by_name(self._name)
        if errors:
            self._errors.extend(errors)
            if self._is_console_output():
                for e in errors:
                    click.echo(f"  ❌  {e}")
            return False

        if self._is_console_output():
            prefix = "[DRY-RUN] " if self._dry_run else ""
            click.echo(f"\n{prefix}Destroying {len(targets)} service(s)…")

        all_ok = True
        for target in targets:
            ok = self._destroy_target(target)
            if not ok:
                all_ok = False
                break

        if all_ok and self._is_console_output() and not self._dry_run:
            click.echo("\n✅  Service destruction completed.")

        return all_ok

    def _destroy_target(self, target: ServiceTarget) -> bool:
        """Destroy a single service target."""
        if self._is_console_output():
            prefix = "[DRY-RUN] " if self._dry_run else ""
            click.echo(f"\n  ▶  {prefix}{target.namespace}/{target.module} ({target.deployer_type.value})")

        if target.deployer_type == ServiceDeployerType.COMPOSE:
            return self._destroy_compose(target)
        elif target.deployer_type == ServiceDeployerType.HELM:
            return self._destroy_helm(target)
        else:
            if self._is_console_output():
                click.echo(f"    ⚠️  Destroy not supported for deployer type: {target.deployer_type.value}")
            return True

    def _destroy_compose(self, target: ServiceTarget) -> bool:
        """Remove a docker stack."""
        from strata.integrations.docker import DockerIntegration

        docker = DockerIntegration(config=IntegrationModel(name="docker", type="docker"))
        available, error = docker.ensure_available()
        if not available:
            self._errors.append(f"Docker not available: {error}")
            return False

        if self._dry_run:
            if self._is_console_output():
                click.echo(f"    Would run: docker stack rm {target.namespace}")
            return True

        if self._is_console_output():
            click.echo(f"    docker stack rm {target.namespace}")

        result = docker._run_integration(
            ["stack", "rm", target.namespace],
            cwd=str(self._work_path),
            timeout=120,
        )
        if result.returncode != 0:
            output = "\n".join(filter(None, [result.stderr, result.stdout]))
            self._errors.append(f"docker stack rm failed:\n{output}")
            return False

        if self._is_console_output():
            click.echo(f"    ✓  Stack '{target.namespace}' removed.")
        return True

    def _destroy_helm(self, target: ServiceTarget) -> bool:
        """Uninstall a helm release."""
        import yaml

        from strata.integrations.helm import HelmIntegration

        helm = HelmIntegration(config=IntegrationModel(name="helm", type="helm"))
        available, error = helm.ensure_available()
        if not available:
            self._errors.append(f"Helm not available: {error}")
            return False

        release_name = target.module or target.namespace
        meta_file = target.build_path / "meta.yaml"
        if meta_file.exists():
            try:
                with meta_file.open("r", encoding="utf-8") as fh:
                    meta = yaml.safe_load(fh) or {}
                release_name = meta.get("releaseName", release_name)
            except Exception:
                pass

        if self._dry_run:
            if self._is_console_output():
                click.echo(f"    Would run: helm uninstall {release_name} -n {target.namespace}")
            return True

        if self._is_console_output():
            click.echo(f"    helm uninstall {release_name} -n {target.namespace}")

        result = helm._run_integration(
            ["uninstall", release_name, "-n", target.namespace],
            cwd=str(self._work_path),
            timeout=120,
        )
        if result.returncode != 0:
            output = "\n".join(filter(None, [result.stderr, result.stdout]))
            self._errors.append(f"helm uninstall failed:\n{output}")
            return False

        if self._is_console_output():
            click.echo(f"    ✓  Release '{release_name}' uninstalled from namespace '{target.namespace}'.")
        return True
