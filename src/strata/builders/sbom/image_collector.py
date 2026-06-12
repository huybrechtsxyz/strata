"""Collect container image components from platform module services."""

from pathlib import Path
from typing import List

from strata.builders.sbom.base_sbom_collector import BaseSbomCollector
from strata.logger import get_logger
from strata.models.platform_artifact_model import PlatformArtifactModel
from strata.models.sbom_model import SbomComponentModel
from strata.utils.sbom_utils import image_to_purl, is_floating_tag, parse_image_ref

_FLOATING_PROPERTY = "strata:tag-stability"
_FLOATING_VALUE = "floating"

logger = get_logger(__name__)


class ContainerImageCollector(BaseSbomCollector):
    """Collects container image components from ``platform.spec.modules[].services[].image``.

    Deduplicates by PURL.  Floating tags (``latest``, ``main``, ``dev``, …)
    emit a ``WARNING`` log entry and get a ``strata:tag-stability=floating``
    property on the CycloneDX component.
    """

    def get_collector_name(self) -> str:
        return "image"

    def collect(
        self,
        platform: PlatformArtifactModel,
        work_path: Path,
        deployment_build_path: Path,
    ) -> List[SbomComponentModel]:
        self._reset_warnings()
        components: List[SbomComponentModel] = []

        if not platform.spec or not platform.spec.modules:
            return components

        seen_purls: set[str] = set()

        for module in platform.spec.modules:
            if not module.services:
                continue
            for service in module.services:
                if not service.image:
                    continue

                purl = image_to_purl(service.image)
                if purl in seen_purls:
                    continue
                seen_purls.add(purl)

                _, tag, _ = parse_image_ref(service.image)
                properties: dict[str, str] = {}
                if is_floating_tag(tag):
                    properties[_FLOATING_PROPERTY] = _FLOATING_VALUE
                    warning = f"floating image tag detected  service={service.name}  image={service.image}"
                    self._warnings.append(warning)
                    logger.warning(
                        "floating image tag detected",
                        service=str(service.name),
                        image=service.image,
                    )

                components.append(
                    SbomComponentModel(
                        component_type="container",
                        name=str(service.name),
                        version=tag,
                        purl=purl,
                        properties=properties,
                        source_collector=self.get_collector_name(),
                    )
                )

        return components
