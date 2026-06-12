"""Collect Helm chart components from platform provisioners."""

from pathlib import Path
from typing import List

from strata.builders.sbom.base_sbom_collector import BaseSbomCollector
from strata.models.common_models import ProvisionerType
from strata.models.platform_artifact_model import PlatformArtifactModel
from strata.models.sbom_model import SbomComponentModel
from strata.utils.sbom_utils import helm_chart_to_purl


class HelmChartCollector(BaseSbomCollector):
    """Collects Helm chart components from provisioners with ``type=helm``.

    Reads ``source.chart_name``, ``source.chart_version``, and
    ``source.chart_repository`` from each Helm provisioner in
    ``platform.spec.provisioners``.  Provisioners without a ``chart_name``
    are skipped silently.
    """

    def get_collector_name(self) -> str:
        return "helm"

    def collect(
        self,
        platform: PlatformArtifactModel,
        work_path: Path,
        deployment_build_path: Path,
    ) -> List[SbomComponentModel]:
        self._reset_warnings()
        components: List[SbomComponentModel] = []

        if not platform.spec or not platform.spec.provisioners:
            return components

        for provisioner in platform.spec.provisioners:
            if provisioner.provisioner != ProvisionerType.HELM:
                continue
            if not provisioner.source.chart_name:
                continue

            chart_name = provisioner.source.chart_name
            chart_version = provisioner.source.chart_version
            chart_repo = provisioner.source.chart_repository

            purl = helm_chart_to_purl(chart_name, chart_version, chart_repo)

            components.append(
                SbomComponentModel(
                    component_type="library",
                    name=chart_name,
                    version=chart_version,
                    purl=purl,
                    properties={},
                    source_collector=self.get_collector_name(),
                )
            )

        return components
