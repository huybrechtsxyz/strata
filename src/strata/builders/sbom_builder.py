"""Build the SBOM artifact from the assembled platform model."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

from strata.builders.base_builder import BaseBuilder
from strata.builders.sbom.ansible_collector import AnsibleCollectionCollector
from strata.builders.sbom.base_sbom_collector import BaseSbomCollector
from strata.builders.sbom.collector_plugin_loader import CollectorPluginLoader
from strata.builders.sbom.compose_collector import ComposeImageCollector
from strata.builders.sbom.deps_collector import DependencyFileCollector
from strata.builders.sbom.helm_collector import HelmChartCollector
from strata.builders.sbom.image_collector import ContainerImageCollector
from strata.builders.sbom.terraform_collector import TerraformProviderCollector
from strata.builders.sbom.terraform_module_collector import TerraformModuleCollector
from strata.models.platform_artifact_model import PlatformArtifactModel
from strata.models.sbom_model import SbomComponentModel, SbomReferenceModel
from strata.services.deployment_service import DeploymentService
from strata.services.platform_artifact_service import PlatformService

if TYPE_CHECKING:
    from strata.controllers.solution_controller import SolutionController

_SBOM_FILENAME = "sbom.json"
_SBOM_FORMAT = "cyclonedx-1.6"

# Maps source_collector → human-readable group heading in inventory output.
# Collectors not in this map appear under their collector name as-is.
_INVENTORY_GROUP_LABELS: Dict[str, str] = {
    "image": "Container Images",
    "compose": "Compose Services",
    "helm": "Helm Charts",
    "terraform": "Terraform Providers",
    "terraform-module": "Terraform Modules",
    "ansible": "Ansible Collections",
    "deps": "Application Dependencies",
}


def _default_collectors() -> List[BaseSbomCollector]:
    """Return a fresh list of all built-in collectors."""
    return [
        ContainerImageCollector(),
        ComposeImageCollector(),
        HelmChartCollector(),
        TerraformProviderCollector(),
        TerraformModuleCollector(),
        AnsibleCollectionCollector(),
        DependencyFileCollector(),
    ]


class SbomBuilder(BaseBuilder):
    """Builder that generates a CycloneDX 1.6 SBOM from an assembled platform artifact.

    Runs after ``PlatformBuilder`` and all other builders in the build pipeline.
    Reads ``platform.json`` from the deployment build directory (or accepts a
    pre-assembled ``PlatformArtifactModel`` for dry-run mode), collects
    components via the registered collectors, serialises to CycloneDX 1.6 JSON,
    writes ``sbom.json`` alongside ``platform.json``, and stores a
    ``SbomReferenceModel`` for the caller to embed in the deployment manifest.

    The ``cyclonedx-python-lib`` import is isolated to this class — the
    individual collectors have no external SBOM library dependency and can be
    tested in isolation.

    Args:
        verbose: Enable progress messages.
        collectors: Injectable list of collectors (defaults to all seven
            built-in collectors when ``None``).
        no_deps: When ``True`` and *collectors* is ``None``, exclude
            ``DependencyFileCollector`` from the default set.  Has no effect
            when *collectors* is provided explicitly.
    """

    def __init__(
        self,
        verbose: bool = False,
        collectors: Optional[List[BaseSbomCollector]] = None,
        no_deps: bool = False,
    ) -> None:
        super().__init__(verbose=verbose)
        if collectors is not None:
            self._collectors: List[BaseSbomCollector] = collectors
        elif no_deps:
            self._collectors = [c for c in _default_collectors() if not isinstance(c, DependencyFileCollector)]
        else:
            self._collectors = _default_collectors()
        self._sbom_reference: Optional[SbomReferenceModel] = None

    @property
    def sbom_reference(self) -> Optional[SbomReferenceModel]:
        """Return the ``SbomReferenceModel`` produced by the last successful ``build()``."""
        return self._sbom_reference

    # ------------------------------------------------------------------
    # BaseBuilder interface
    # ------------------------------------------------------------------

    def before_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        if not deployment_service.is_validated():
            self._errors.append("Deployment service is not validated")
            return False

        if not dry_run:
            platform_path = (
                solution_controller.get_platform_path(deployment_service, build_path)
                if solution_controller is not None
                else deployment_service.get_build_path(build_path) / "platform.json"
            )
            if not platform_path.exists():
                self._errors.append(f"Platform model not found at: {platform_path}. Run platform build first.")
                return False

        if self.verbose:
            self._messages.append("Pre-build validation passed for SBOM")

        return True

    def build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        platform_model: Optional[PlatformArtifactModel] = None,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        self._sbom_reference = None

        try:
            if platform_model is None:
                platform_path = (
                    solution_controller.get_platform_path(deployment_service, build_path)
                    if solution_controller is not None
                    else deployment_service.get_build_path(build_path) / "platform.json"
                )
                if not platform_path.exists():
                    self._errors.append("Platform model not found. Run platform build first.")
                    return False

                platform_service = PlatformService.load(str(platform_path), validate=True)
                if not platform_service.is_validated() or not platform_service.model:
                    self._errors.append("Platform model validation failed")
                    return False

                platform_model = platform_service.model
                if self.verbose and getattr(platform_model, "meta", None):
                    self._messages.append(f"Loaded platform model: {platform_model.meta.name}")

            if platform_model is None:
                self._errors.append("Platform model is None after loading")
                return False

            deployment_build_path = deployment_service.get_build_path(build_path)

            # Load workspace collector plugins (additive — does not mutate self._collectors)
            extra_collectors = CollectorPluginLoader.load(work_path)
            active_collectors = self._collectors + extra_collectors

            # Collect components — drain warnings immediately after each collector
            components: List[SbomComponentModel] = []
            for collector in active_collectors:
                collected = collector.collect(platform_model, work_path, deployment_build_path)
                components.extend(collected)
                for warning in collector.get_warnings():
                    self._messages.append(f"[{collector.get_collector_name()}] {warning}")

            if dry_run:
                sbom_path = deployment_build_path / _SBOM_FILENAME
                self._messages.append(f"[DRY-RUN] Would write SBOM ({len(components)} components) to: {sbom_path}")
                return True

            # Serialise to CycloneDX 1.6 JSON
            bom_json = self._build_cyclonedx_json(components)
            if bom_json is None:
                return False  # errors already appended in _build_cyclonedx_json

            # Write sbom.json into the deployment build directory
            deployment_build_path.mkdir(parents=True, exist_ok=True)
            sbom_path = deployment_build_path / _SBOM_FILENAME
            sbom_bytes = bom_json.encode("utf-8")
            sbom_path.write_bytes(sbom_bytes)

            sha256 = hashlib.sha256(sbom_bytes).hexdigest()
            rel_path = str(sbom_path.relative_to(work_path))

            self._sbom_reference = SbomReferenceModel(
                path=rel_path,
                format=_SBOM_FORMAT,
                sha256=f"sha256:{sha256}",
                component_count=len(components),
            )

            if self.verbose:
                self._messages.append(f"SBOM written: {sbom_path} ({len(components)} components)")

            return True

        except Exception as exc:
            self._errors.append(f"Failed to build SBOM: {exc}")
            self.logger.exception("Failed to build SBOM", error=str(exc))
            return False

    def after_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        if dry_run:
            return True

        sbom_path = deployment_service.get_build_path(build_path) / _SBOM_FILENAME
        if not sbom_path.exists():
            self._errors.append(f"SBOM file not found after build: {sbom_path}")
            return False

        if self.verbose:
            self._messages.append(f"SBOM verified at: {sbom_path}")

        return True

    # ------------------------------------------------------------------
    # Inventory rendering
    # ------------------------------------------------------------------

    def render_inventory(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        platform_model: Optional[PlatformArtifactModel] = None,
        solution_controller: Optional["SolutionController"] = None,
    ) -> Optional[str]:
        """Collect all components and return a human-readable inventory string.

        Runs the same collector pipeline as ``build()`` but does not write any
        output files.  Returns ``None`` on failure — errors are appended to
        ``self._errors``.
        """
        try:
            if platform_model is None:
                platform_path = (
                    solution_controller.get_platform_path(deployment_service, build_path)
                    if solution_controller is not None
                    else deployment_service.get_build_path(build_path) / "platform.json"
                )
                if not platform_path.exists():
                    self._errors.append("Platform model not found. Run platform build first.")
                    return None

                platform_service = PlatformService.load(str(platform_path), validate=True)
                if not platform_service.is_validated() or not platform_service.model:
                    self._errors.append("Platform model validation failed")
                    return None

                platform_model = platform_service.model

            deployment_build_path = deployment_service.get_build_path(build_path)

            extra_collectors = CollectorPluginLoader.load(work_path)
            active_collectors = self._collectors + extra_collectors

            components: List[SbomComponentModel] = []
            for collector in active_collectors:
                collected = collector.collect(platform_model, work_path, deployment_build_path)
                components.extend(collected)
                for warning in collector.get_warnings():
                    self._messages.append(f"[{collector.get_collector_name()}] {warning}")

            deployment_name = str(deployment_service.model.meta.name) if deployment_service.model else None
            return self._format_inventory(components, deployment_name)

        except Exception as exc:
            self._errors.append(f"Failed to render inventory: {exc}")
            self.logger.exception("Failed to render inventory", error=str(exc))
            return None

    def _format_inventory(
        self,
        components: List[SbomComponentModel],
        deployment_name: Optional[str],
    ) -> str:
        """Format *components* into a human-readable grouped inventory string."""

        def _get_purl_cls():
            try:
                from packageurl import PackageURL

                return PackageURL
            except ImportError:
                return None

        _purl_cls = _get_purl_cls()

        def _source_from_purl(purl_str: str) -> str:
            if _purl_cls is None:
                return ""
            try:
                purl = _purl_cls.from_string(purl_str)
                repo_url: str = (purl.qualifiers or {}).get("repository_url", "")  # type: ignore[union-attr]
                if repo_url:
                    return repo_url
                if purl.type == "docker":
                    return "docker.io"
                if purl.type in ("github", "gitlab", "bitbucket"):
                    return f"{purl.type}.com"
                return ""
            except Exception:
                return ""

        # Group by source_collector, preserving insertion order
        groups: Dict[str, List[SbomComponentModel]] = {}
        for comp in components:
            groups.setdefault(comp.source_collector, []).append(comp)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        title = f"Platform Inventory — {deployment_name}" if deployment_name else "Platform Inventory"
        separator = "━" * 64

        lines: List[str] = [
            "",
            f"{title}  (built {now})",
            separator,
        ]

        floating_count = 0

        for collector_name, group_components in groups.items():
            label = _INVENTORY_GROUP_LABELS.get(collector_name, collector_name.title())
            lines.append(f"\n{label} ({len(group_components)})")

            for comp in group_components:
                is_floating = comp.properties.get("strata:tag-stability") == "floating"
                if is_floating:
                    floating_count += 1
                source = _source_from_purl(comp.purl)
                ver = comp.version or "—"
                flag = "  ⚠ floating" if is_floating else ""
                name_col = comp.name.ljust(24)
                ver_col = ver.ljust(14)
                src_col = source
                lines.append(f"  {name_col}{ver_col}{src_col}{flag}")

        total = len(components)
        summary = f"Total: {total} component{'s' if total != 1 else ''}"
        if floating_count:
            summary += f"  |  ⚠ {floating_count} floating tag{'s' if floating_count != 1 else ''}"
        lines.append(f"\n{summary}")
        lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # CycloneDX serialisation — only this class imports cyclonedx-python-lib
    # ------------------------------------------------------------------

    def _build_cyclonedx_json(self, components: List[SbomComponentModel]) -> Optional[str]:
        """Convert collected components to a CycloneDX 1.6 JSON string."""
        try:
            from cyclonedx.model import Property
            from cyclonedx.model.bom import Bom
            from cyclonedx.model.component import Component, ComponentType
            from cyclonedx.output.json import JsonV1Dot6
            from packageurl import PackageURL
        except ImportError as exc:
            self._errors.append(f"cyclonedx-python-lib is not installed: {exc}")
            return None

        _type_map = {
            "container": ComponentType.CONTAINER,
            "library": ComponentType.LIBRARY,
            "framework": ComponentType.FRAMEWORK,
        }

        bom = Bom()

        for comp in components:
            cdx_type = _type_map.get(comp.component_type, ComponentType.LIBRARY)

            try:
                purl = PackageURL.from_string(comp.purl)
            except Exception:
                purl = None

            properties = [Property(name=k, value=v) for k, v in comp.properties.items()]

            cdx_component = Component(
                name=comp.name,
                type=cdx_type,
                version=comp.version,
                purl=purl,
                properties=properties,
            )
            bom.components.add(cdx_component)

        try:
            output = JsonV1Dot6(bom)
            return output.output_as_string()
        except Exception as exc:
            self._errors.append(f"CycloneDX serialisation failed: {exc}")
            return None
