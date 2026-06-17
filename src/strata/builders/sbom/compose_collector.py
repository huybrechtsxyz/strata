"""Collect container image components from docker-compose.yml service definitions."""

from pathlib import Path
from typing import List

from strata.builders.sbom.base_sbom_collector import BaseSbomCollector
from strata.logger import get_logger
from strata.models.platform_artifact_model import PlatformArtifactModel
from strata.models.sbom_model import SbomComponentModel
from strata.utils.sbom_utils import image_to_purl, is_floating_tag, parse_image_ref

_COMPOSE_FILENAMES = ("docker-compose.yml", "docker-compose.yaml")
_FLOATING_PROPERTY = "strata:tag-stability"
_FLOATING_VALUE = "floating"

logger = get_logger(__name__)


class ComposeImageCollector(BaseSbomCollector):
    """Collect container image components from ``docker-compose.yml`` service definitions.

    Scans the deployment build path recursively for ``docker-compose.yml`` and
    ``docker-compose.yaml`` files.  These files are staged into the build path
    by ``ComposeDeployer`` during ``strata build run``.

    Extracts ``services.<name>.image`` entries.  Services with only a ``build:``
    directive (no ``image:``) are silently skipped.  Deduplicates by PURL.

    Images without an explicit tag receive a ``strata:tag-stability=floating``
    property, consistent with ``ContainerImageCollector``.
    """

    def get_collector_name(self) -> str:
        return "compose"

    def collect(
        self,
        platform: PlatformArtifactModel,
        work_path: Path,
        deployment_build_path: Path,
    ) -> List[SbomComponentModel]:
        self._reset_warnings()
        components: List[SbomComponentModel] = []

        if not deployment_build_path.exists():
            return components

        seen_purls: set[str] = set()

        compose_files = [f for name in _COMPOSE_FILENAMES for f in sorted(deployment_build_path.rglob(name))]

        for compose_file in compose_files:
            try:
                import yaml

                with compose_file.open("r", encoding="utf-8") as fh:
                    data = yaml.safe_load(fh)
                self._extract_images(data, compose_file.name, components, seen_purls)
            except Exception as exc:
                warning = f"Failed to parse {compose_file.name}: {exc}"
                self._warnings.append(warning)
                logger.warning(
                    "Failed to parse compose file",
                    file=str(compose_file),
                    error=str(exc),
                )

        return components

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_images(
        self,
        data: object,
        filename: str,
        components: List[SbomComponentModel],
        seen_purls: set[str],
    ) -> None:
        if not isinstance(data, dict):
            return
        services = data.get("services")
        if not isinstance(services, dict):
            return

        for service_name, service_cfg in services.items():
            if not isinstance(service_cfg, dict):
                continue
            image = service_cfg.get("image")
            if not image or not isinstance(image, str):
                continue

            purl = image_to_purl(image)
            if purl in seen_purls:
                continue
            seen_purls.add(purl)

            _, tag, _ = parse_image_ref(image)
            properties: dict[str, str] = {}
            if is_floating_tag(tag):
                properties[_FLOATING_PROPERTY] = _FLOATING_VALUE
                warning = f"floating image tag detected  service={service_name}  image={image}"
                self._warnings.append(warning)
                logger.warning(
                    "floating image tag detected",
                    service=str(service_name),
                    image=image,
                    compose_file=filename,
                )

            components.append(
                SbomComponentModel(
                    component_type="container",
                    name=str(service_name),
                    version=tag,
                    purl=purl,
                    properties=properties,
                    source_collector=self.get_collector_name(),
                )
            )
