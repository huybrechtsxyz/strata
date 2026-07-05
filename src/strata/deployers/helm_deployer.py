"""Deploy Helm chart artifacts from the build output.

For each namespace + module combination that has a ``values.yaml`` and
``meta.yaml`` in the build path, this deployer runs ``helm upgrade --install``.

Supported steps (in execution order):
  setup    — helm repo update (for chart registry sources)
  check    — helm lint per module
  plan     — helm upgrade --dry-run --install per module
  apply    — helm upgrade --install -n {namespace} -f values.yaml {release} {chart}
  destroy  — helm uninstall -n {namespace} {release}  (requires force=True)
  plan_destroy — helm get manifest -n {namespace} {release}
  output   — helm get values -n {namespace} {release}
  show_plan    — no-op, returns empty dict

Chart source resolution:
  meta.yaml provides releaseName, namespace, and chart coordinates
  (chartName, chartVersion, chartRepository).  The chart reference is resolved
  from meta.yaml:
    - chartRepository + chartName + optional chartVersion → registry chart
    - No chart fields → local chart path in build directory
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

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
from strata.integrations.helm import HelmIntegration
from strata.models.common_models import ServiceDeployerType
from strata.models.deployment_model import DeploymentStageModel
from strata.models.integration_model import IntegrationModel
from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_service import DeploymentService
from strata.services.module_service import ModuleService
from strata.utils.resolved_values import ResolvedValues, inject_compose_env
from strata.utils.system import resolve_path


@dataclass
class HelmModuleTarget:
    """Resolved deployment target for a single Helm module."""

    ns_name: str
    module_name: str
    values_file: Path
    meta_file: Path
    release_name: str
    chart_namespace: str
    chart_ref: str
    chart_version: Optional[str]
    repo_url: Optional[str]
    repo_name: Optional[str]


def _sanitize_repo_name(url: str) -> str:
    """Derive a Helm-compatible repo alias from a chart repository URL.

    Strips the scheme, replaces non-alphanumeric characters with ``-``,
    and truncates to 20 characters.
    """
    name = re.sub(r"^https?://", "", url)
    name = re.sub(r"[^a-zA-Z0-9]", "-", name)
    name = name.strip("-")
    return name[:20]


class HelmDeployer(BaseDeployer):
    """Runs a deployment stage using Helm (repo update → lint → dry-run → upgrade).

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
            resolved_values=resolved_values,
        )
        self._helm_modules: List[HelmModuleTarget] = []
        self._helm: Optional[HelmIntegration] = None

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def get_deployer_name(self) -> str:
        return "helm"

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
        """Discover Helm modules in the build path and resolve chart references."""
        messages: List[str] = []

        deployment_build_path = self.deployment_service.get_build_path(self.build_path)
        namespace_services = self.deployment_service.get_namespace_services() or {}

        modules: List[HelmModuleTarget] = []
        for ns_name, ns_service in namespace_services.items():
            if not ns_service.is_validated() or not ns_service.model:
                continue

            ns_name_str = str(ns_name)
            module_refs = ns_service.model.spec.modules or []

            for module_ref in module_refs:
                try:
                    module_path = resolve_path(str(self.work_path), module_ref.file)
                except Exception as exc:
                    messages.append(
                        f"Namespace '{ns_name_str}', module '{module_ref.name}': "
                        f"cannot resolve file '{module_ref.file}': {exc}"
                    )
                    continue

                if not module_path.exists():
                    messages.append(
                        f"Namespace '{ns_name_str}', module '{module_ref.name}': file not found: '{module_path}'"
                    )
                    continue

                mod_service = ModuleService.load(str(module_path), validate=True)
                if not mod_service.is_validated() or not mod_service.model:
                    errs = mod_service.get_validation_errors()
                    messages.append(
                        f"Namespace '{ns_name_str}', module '{module_ref.name}': validation failed: {'; '.join(errs)}"
                    )
                    continue

                module = mod_service.model
                if module.spec.type != ServiceDeployerType.HELM:
                    continue

                module_name = str(module.meta.name)
                values_file = deployment_build_path / ns_name_str / module_name / "values.yaml"
                meta_file = deployment_build_path / ns_name_str / module_name / "meta.yaml"

                if not values_file.exists() or not meta_file.exists():
                    continue

                # Read meta.yaml for release name and k8s namespace
                try:
                    with meta_file.open("r", encoding="utf-8") as fh:
                        meta_doc = yaml.safe_load(fh) or {}
                except Exception as exc:
                    messages.append(f"Namespace '{ns_name_str}', module '{module_name}': cannot read meta.yaml: {exc}")
                    continue

                release_name = meta_doc.get("releaseName") or module_name
                chart_namespace = meta_doc.get("namespace") or ns_name_str

                # Resolve chart reference from meta.yaml (self-contained build artifact)
                chart_repository = meta_doc.get("chartRepository")
                chart_name = meta_doc.get("chartName")
                chart_version = meta_doc.get("chartVersion")

                if chart_repository:
                    repo_name = _sanitize_repo_name(chart_repository)
                    chart_ref = f"{repo_name}/{chart_name}"
                    repo_url = chart_repository
                else:
                    chart_ref = str(deployment_build_path / ns_name_str / module_name)
                    repo_url = None
                    repo_name = None
                    chart_version = None

                target = HelmModuleTarget(
                    ns_name=ns_name_str,
                    module_name=module_name,
                    values_file=values_file,
                    meta_file=meta_file,
                    release_name=release_name,
                    chart_namespace=chart_namespace,
                    chart_ref=chart_ref,
                    chart_version=chart_version,
                    repo_url=repo_url,
                    repo_name=repo_name,
                )
                modules.append(target)

        if not modules:
            messages.append("No helm modules found in build path — nothing to deploy")
            return True, messages

        self._helm_modules = modules
        for target in modules:
            messages.append(
                f"Found helm module: {target.ns_name}/{target.module_name} → "
                f"{target.chart_ref} (release: {target.release_name})"
            )
        return True, messages

    def validate_environment(self) -> Tuple[bool, List[str]]:
        """Verify helm is available on PATH."""
        messages: List[str] = []

        helm = HelmIntegration(config=IntegrationModel(name="helm", type="helm"))
        available, error = helm.ensure_available()
        if not available:
            messages.append(error)
            return False, messages

        self._helm = helm
        messages.append(f"helm {helm.get_version()} available")
        return True, messages

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ready(self, messages: List[str]) -> bool:
        """Guard: validate_environment must have been called first."""
        if self._helm is None:
            messages.append("Deployer not initialized — call validate_workspace/validate_environment first.")
            return False
        return True

    def _run_helm(
        self,
        args: List[str],
        cwd: Optional[Path] = None,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """Run a helm command via the integration."""
        messages: List[str] = []
        assert self._helm is not None
        result = self._helm._run_integration(
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
        """Add and update Helm chart repositories for registry-based modules."""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages

        # Collect unique (repo_name, repo_url) pairs
        seen: Dict[str, str] = {}
        for target in self._helm_modules:
            if target.repo_url and target.repo_name and target.repo_name not in seen:
                seen[target.repo_name] = target.repo_url

        if not seen:
            messages.append("No chart registries to update")
            return True, messages

        for repo_name, repo_url in seen.items():
            messages.append(f"helm repo add {repo_name} {repo_url}")
            # Ignore failure — repo may already be registered
            self._helm._run_integration(  # type: ignore[union-attr]
                ["repo", "add", repo_name, repo_url],
                cwd=str(self.build_path),
                timeout=60,
            )

        messages.append("helm repo update")
        ok, run_messages = self._run_helm(["repo", "update"], line_callback=line_callback)
        messages.extend(run_messages)
        if not ok:
            return False, messages

        return True, messages

    def check(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """Run helm lint for each module."""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages

        if not self._helm_modules:
            messages.append("No helm modules to lint")
            return True, messages

        for target in self._helm_modules:
            messages.append(f"helm lint {target.ns_name}/{target.module_name}")
            # helm lint only works with local chart directories/archives.
            # Skip lint for registry charts (repo_url set) — they're linted at
            # deploy time via --dry-run in plan().
            if target.repo_url is not None:
                messages.append("  (registry chart — lint skipped; use plan for dry-run)")
                continue
            ok, run_messages = self._run_helm(
                ["lint", "-f", str(target.values_file), target.chart_ref],
                line_callback=line_callback,
            )
            messages.extend(run_messages)
            if not ok:
                return False, messages

        return True, messages

    def plan(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """Dry-run upgrade for each module."""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages

        if not self._helm_modules:
            messages.append("No helm modules to plan")
            return True, messages

        with inject_compose_env(self.resolved_values):
            for target in self._helm_modules:
                messages.append(
                    f"helm upgrade --dry-run --install {target.release_name} -n {target.chart_namespace} {target.chart_ref}"
                )
                args = [
                    "upgrade",
                    "--dry-run",
                    "--install",
                    "--namespace",
                    target.chart_namespace,
                    "-f",
                    str(target.values_file),
                    target.release_name,
                    target.chart_ref,
                ]
                if target.chart_version:
                    args += ["--version", target.chart_version]
                ok, run_messages = self._run_helm(args, line_callback=line_callback)
                messages.extend(run_messages)
                if not ok:
                    return False, messages

        return True, messages

    def apply(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """Upgrade/install each module."""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages

        if not self._helm_modules:
            messages.append("No helm modules to deploy")
            return True, messages

        with inject_compose_env(self.resolved_values):
            for target in self._helm_modules:
                messages.append(
                    f"helm upgrade --install {target.release_name} -n {target.chart_namespace} {target.chart_ref}"
                )
                args = [
                    "upgrade",
                    "--install",
                    "--create-namespace",
                    "--wait",
                    "--atomic",
                    "--timeout",
                    "5m",
                    "--namespace",
                    target.chart_namespace,
                    "-f",
                    str(target.values_file),
                    target.release_name,
                    target.chart_ref,
                ]
                if target.chart_version:
                    args += ["--version", target.chart_version]
                ok, run_messages = self._run_helm(args, line_callback=line_callback)
                messages.extend(run_messages)
                if not ok:
                    return False, messages

        return True, messages

    def destroy(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """Uninstall each release (requires force=True)."""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages

        if not self.force:
            messages.append("--force is required for destroy")
            return False, messages

        if not self._helm_modules:
            messages.append("No helm modules to destroy")
            return True, messages

        with inject_compose_env(self.resolved_values):
            for target in self._helm_modules:
                messages.append(f"helm uninstall {target.release_name} -n {target.chart_namespace}")
                ok, run_messages = self._run_helm(
                    ["uninstall", "--namespace", target.chart_namespace, target.release_name],
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
        """Preview what would be removed by inspecting installed releases."""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages

        if not self._helm_modules:
            messages.append("No helm modules to inspect")
            return True, messages

        with inject_compose_env(self.resolved_values):
            for target in self._helm_modules:
                messages.append(f"helm get manifest {target.release_name} -n {target.chart_namespace}")
                result = self._helm._run_integration(  # type: ignore[union-attr]
                    ["get", "manifest", "--namespace", target.chart_namespace, target.release_name],
                    cwd=str(self.build_path),
                    timeout=60,
                )
                if result.returncode == 0:
                    messages.append(f"  {target.ns_name}/{target.module_name}: installed (would uninstall)")
                else:
                    messages.append(f"  {target.ns_name}/{target.module_name}: not installed")

        return True, messages

    def output(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Retrieve deployed values for each release."""
        messages: List[str] = []
        if not self._ready(messages):
            return False, {}, messages

        if not self._helm_modules:
            messages.append("No helm modules to inspect")
            return True, {}, messages

        outputs: Dict[str, Any] = {}
        for target in self._helm_modules:
            result = self._helm._run_integration(  # type: ignore[union-attr]
                ["get", "values", "--namespace", target.chart_namespace, target.release_name],
                cwd=str(self.build_path),
                timeout=60,
                line_callback=line_callback,
            )
            key = f"{target.ns_name}/{target.module_name}"
            if result.returncode == 0 and result.stdout.strip():
                try:
                    parsed = yaml.safe_load(result.stdout) or {}
                    outputs[key] = parsed
                except Exception:
                    outputs[key] = {"raw": result.stdout.strip()}
            else:
                outputs[key] = {}

        return True, outputs, messages

    def show_plan(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """No-op — helm has no persisted plan format."""
        return True, {}, []
