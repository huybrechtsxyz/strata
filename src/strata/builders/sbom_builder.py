"""Build the SBOM artifact from the assembled platform model."""

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from strata.builders.base_builder import BaseBuilder
from strata.builders.sbom.ansible_collector import AnsibleCollectionCollector
from strata.builders.sbom.base_sbom_collector import BaseSbomCollector
from strata.builders.sbom.helm_collector import HelmChartCollector
from strata.builders.sbom.image_collector import ContainerImageCollector
from strata.builders.sbom.terraform_collector import TerraformProviderCollector
from strata.models.platform_artifact_model import PlatformArtifactModel
from strata.models.sbom_model import SbomComponentModel, SbomReferenceModel
from strata.services.deployment_service import DeploymentService
from strata.services.platform_artifact_service import PlatformService

if TYPE_CHECKING:
    from strata.controllers.solution_controller import SolutionController

_SBOM_FILENAME = "sbom.json"
_SBOM_FORMAT = "cyclonedx-1.6"


def _default_collectors() -> List[BaseSbomCollector]:
    """Return a fresh list of all built-in collectors."""
    return [
        ContainerImageCollector(),
        HelmChartCollector(),
        TerraformProviderCollector(),
        AnsibleCollectionCollector(),
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
        collectors: Injectable list of collectors (defaults to all four
            built-in collectors when ``None``).
    """

    def __init__(
        self,
        verbose: bool = False,
        collectors: Optional[List[BaseSbomCollector]] = None,
    ) -> None:
        super().__init__(verbose=verbose)
        self._collectors: List[BaseSbomCollector] = collectors if collectors is not None else _default_collectors()
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

            # Collect components — drain warnings immediately after each collector
            components: List[SbomComponentModel] = []
            for collector in self._collectors:
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
