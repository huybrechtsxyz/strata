"""Deploy Docker Compose/Stack artifacts from the build output.

For each namespace that has a ``docker-compose.yml`` in the build path, this
deployer runs ``docker stack deploy``.

Supported steps (in execution order):
  setup    — verify Docker daemon is reachable (docker info)
  check    — verify all expected docker-compose.yml files exist in build_path
  plan     — list namespaces and service counts that would be deployed (no true dry-run)
  apply    — docker stack deploy --with-registry-auth -c {file} {namespace}
  destroy  — docker stack rm {namespace}  (requires force=True)
  plan_destroy — list currently running stacks matching namespace names
  output   — docker stack services {namespace}
  show_plan    — no-op, returns empty dict

Working directory: build_path/{deployment_name}/{namespace}/docker-compose.yml
"""

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

from strata.controllers.value_controller import ResolvedValues
from strata.deployers.base_deployer import (
    STEP_APPLY,
    STEP_CHECK,
    STEP_DESTROY,
    STEP_OUTPUT,
    STEP_PLAN,
    STEP_PLAN_DESTROY,
    STEP_SETUP,
    STEP_SHOW_PLAN,
    BaseDeployer,
)
from strata.integrations.docker import DockerIntegration
from strata.models.deployment_model import DeploymentStageModel
from strata.models.integration_model import IntegrationModel
from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_service import DeploymentService


class ComposeDeployer(BaseDeployer):
    """Runs a deployment stage using Docker Compose/Stack.

    Context is passed once to the constructor; step methods carry no arguments
    besides the optional line_callback.
    Call validate_workspace() then validate_environment() before running steps.
    """

    def __init__(
        self,
        stage: "DeploymentStageModel",
        deployment_service: "DeploymentService",
        configuration_service: "ConfigurationService",
        build_path: Path,
        work_path: Path,
        verbose: bool = False,
        force: bool = False,
        resolved_values: Optional[ResolvedValues] = None,
        solution_controller=None,
    ) -> None:
        super().__init__(
            stage=stage,
            deployment_service=deployment_service,
            configuration_service=configuration_service,
            build_path=build_path,
            work_path=work_path,
            verbose=verbose,
            force=force,
            solution_controller=solution_controller,
        )
        self.resolved_values = resolved_values
        self._compose_files: Dict[str, Path] = {}
        self._docker: Optional[DockerIntegration] = None

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def get_deployer_name(self) -> str:
        return "compose"

    def get_supported_steps(self) -> List[str]:
        return [
            STEP_SETUP,
            STEP_CHECK,
            STEP_PLAN,
            STEP_APPLY,
            STEP_DESTROY,
            STEP_PLAN_DESTROY,
            STEP_SHOW_PLAN,
            STEP_OUTPUT,
        ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_workspace(self) -> Tuple[bool, List[str]]:
        """Verify compose files exist in the build path for each namespace."""
        messages: List[str] = []

        deployment_build_path = self.deployment_service.get_build_path(self.build_path)
        namespace_services = self.deployment_service.get_namespace_services() or {}

        found: Dict[str, Path] = {}
        for ns_name, ns_service in namespace_services.items():
            if not ns_service.is_validated() or not ns_service.model:
                continue
            compose_file = deployment_build_path / str(ns_name) / "docker-compose.yml"
            if compose_file.exists():
                found[str(ns_name)] = compose_file

        if not found:
            messages.append("No docker-compose.yml files found in build path — nothing to deploy")
            return True, messages

        self._compose_files = found
        for _ns_name, compose_file in found.items():
            messages.append(f"Found compose file: {compose_file}")
        return True, messages

    def validate_environment(self) -> Tuple[bool, List[str]]:
        """Verify Docker is available on PATH."""
        messages: List[str] = []

        docker = DockerIntegration(config=IntegrationModel(name="docker", type="docker"))
        available, error = docker.ensure_available()
        if not available:
            messages.append(error)
            return False, messages

        self._docker = docker
        messages.append(f"docker {docker.get_version()} available")
        return True, messages

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ready(self, messages: List[str]) -> bool:
        """Guard: validate_environment must have been called first."""
        if self._docker is None:
            messages.append("Deployer not initialized — call validate_workspace/validate_environment first.")
            return False
        return True

    def _get_compose_files(self) -> Dict[str, Path]:
        """Return the compose files discovered during validate_workspace."""
        return self._compose_files

    def _run_docker(
        self,
        args: List[str],
        cwd: Optional[Path] = None,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """Run a docker command via the integration."""
        messages: List[str] = []
        assert self._docker is not None
        result = self._docker._run_integration(
            args,
            cwd=str(cwd or self.build_path),
            timeout=300,
            line_callback=line_callback,
        )
        if result.returncode != 0:
            output = "\n".join(filter(None, [result.stderr, result.stdout]))
            messages.append(output)
            return False, messages
        if self.verbose and result.stdout.strip() and line_callback is None:
            messages.append(result.stdout.strip())
        return True, messages

    # ------------------------------------------------------------------
    # Step methods
    # ------------------------------------------------------------------

    def setup(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """Verify the Docker daemon is reachable."""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages

        ok, run_messages = self._run_docker(["info"], line_callback=line_callback)
        messages.extend(run_messages)
        if not ok:
            messages.append("Docker daemon is not reachable. Ensure Docker is running.")
            return False, messages

        messages.append("Docker daemon reachable")
        return True, messages

    def check(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """Verify all expected docker-compose.yml files exist on disk."""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages

        compose_files = self._get_compose_files()
        if not compose_files:
            messages.append("No compose files to check")
            return True, messages

        all_ok = True
        for ns_name, compose_file in compose_files.items():
            if compose_file.exists():
                messages.append(f"  {ns_name}: {compose_file}")
            else:
                messages.append(f"  {ns_name}: MISSING — {compose_file}")
                all_ok = False

        return all_ok, messages

    def plan(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """List namespaces and service counts that would be deployed."""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages

        compose_files = self._get_compose_files()
        if not compose_files:
            messages.append("No compose files found — nothing to deploy")
            return True, messages

        messages.append("Namespaces to deploy:")
        for ns_name, compose_file in compose_files.items():
            try:
                with compose_file.open("r", encoding="utf-8") as fh:
                    doc = yaml.safe_load(fh) or {}
                services = doc.get("services", {})
                service_count = len(services) if isinstance(services, dict) else 0
                messages.append(f"  {ns_name}: {service_count} service(s)")
            except Exception as exc:
                messages.append(f"  {ns_name}: could not parse compose file — {exc}")

        return True, messages

    def apply(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """Deploy all namespaces via docker stack deploy."""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages

        compose_files = self._get_compose_files()
        if not compose_files:
            messages.append("No compose files found — nothing to deploy")
            return True, messages

        for ns_name, compose_file in compose_files.items():
            messages.append(f"docker stack deploy {ns_name}")
            ok, run_messages = self._run_docker(
                ["stack", "deploy", "--with-registry-auth", "-c", str(compose_file), ns_name],
                line_callback=line_callback,
            )
            messages.extend(run_messages)
            if not ok:
                return False, messages

        return True, messages

    def destroy(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """Remove all stacks via docker stack rm (requires force=True)."""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages

        if not self.force:
            messages.append("--force is required for destroy")
            return False, messages

        compose_files = self._get_compose_files()
        if not compose_files:
            messages.append("No compose files found — nothing to destroy")
            return True, messages

        for ns_name in compose_files:
            messages.append(f"docker stack rm {ns_name}")
            ok, run_messages = self._run_docker(
                ["stack", "rm", ns_name],
                line_callback=line_callback,
            )
            messages.extend(run_messages)
            if not ok:
                return False, messages

        return True, messages

    def plan_destroy(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """List which of our namespaces currently have running stacks."""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages

        compose_files = self._get_compose_files()
        if not compose_files:
            messages.append("No compose files found — nothing to inspect")
            return True, messages

        result = self._docker._run_integration(  # type: ignore[union-attr]
            ["stack", "ls", "--format", "{{.Name}}"],
            cwd=str(self.build_path),
            timeout=60,
        )
        running_stacks: List[str] = []
        if result.returncode == 0 and result.stdout:
            running_stacks = [line.strip() for line in result.stdout.splitlines() if line.strip()]

        messages.append("Stacks matching our namespaces:")
        for ns_name in compose_files:
            status = "RUNNING" if ns_name in running_stacks else "not deployed"
            messages.append(f"  {ns_name}: {status}")

        return True, messages

    def output(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Return service details for each deployed stack."""
        messages: List[str] = []
        if not self._ready(messages):
            return False, {}, messages

        compose_files = self._get_compose_files()
        if not compose_files:
            messages.append("No compose files found — nothing to inspect")
            return True, {}, messages

        outputs: Dict[str, Any] = {}
        for ns_name in compose_files:
            result = self._docker._run_integration(  # type: ignore[union-attr]
                ["stack", "services", ns_name, "--format", "{{.Name}}\t{{.Replicas}}\t{{.Image}}"],
                cwd=str(self.build_path),
                timeout=60,
                line_callback=line_callback,
            )
            services = []
            if result.returncode == 0 and result.stdout:
                for line in result.stdout.splitlines():
                    parts = line.strip().split("\t")
                    if len(parts) == 3:
                        services.append({"name": parts[0], "replicas": parts[1], "image": parts[2]})
                    elif line.strip():
                        services.append({"raw": line.strip()})
            outputs[ns_name] = {"services": services}

        return True, outputs, messages

    def show_plan(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """No-op — compose has no persisted plan format."""
        return True, {}, []
