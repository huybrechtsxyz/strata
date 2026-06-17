"""Collect Helm chart components from Chart.yaml files on disk."""

from pathlib import Path
from typing import List, Set

from strata.builders.sbom.base_sbom_collector import BaseSbomCollector
from strata.logger import get_logger
from strata.models.platform_artifact_model import PlatformArtifactModel
from strata.models.sbom_model import SbomComponentModel
from strata.utils.sbom_utils import helm_chart_to_purl

_SKIP_DIRS = frozenset(("node_modules", ".venv", "venv", "dist", "build", ".git", "__pycache__"))

logger = get_logger(__name__)


class HelmChartFileCollector(BaseSbomCollector):
    """Collect Helm chart components from ``Chart.yaml`` files on disk.

    Scans ``deployment_build_path`` recursively for ``Chart.yaml`` files.
    For each chart found, emits the chart itself (name + version) and every
    entry in its ``dependencies`` list.

    Complements ``HelmChartCollector`` which reads chart references from the
    strata platform model.  Both collectors use the ``"helm"`` collector name
    so their results are grouped together in inventory output and deduplicated
    by purl.

    Directories in ``_SKIP_DIRS`` are pruned during the walk.
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

        if not deployment_build_path.exists():
            return components

        seen_purls: Set[str] = set()

        for chart_file in self._find_chart_files(deployment_build_path):
            try:
                import yaml

                with chart_file.open("r", encoding="utf-8") as fh:
                    data = yaml.safe_load(fh)
                if not isinstance(data, dict):
                    continue
                self._extract_chart(data, chart_file, components, seen_purls)
            except Exception as exc:
                warning = f"Failed to parse {chart_file}: {exc}"
                self._warnings.append(warning)
                logger.warning(
                    "Failed to parse Chart.yaml",
                    file=str(chart_file),
                    error=str(exc),
                )

        return components

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_chart_files(self, root: Path) -> List[Path]:
        """Walk *root* for Chart.yaml files, pruning skip directories."""
        results: List[Path] = []
        for item in sorted(root.iterdir()):
            if item.is_dir():
                if item.name in _SKIP_DIRS:
                    continue
                results.extend(self._find_chart_files(item))
            elif item.is_file() and item.name == "Chart.yaml":
                results.append(item)
        return results

    def _extract_chart(
        self,
        data: dict,
        chart_file: Path,
        components: List[SbomComponentModel],
        seen_purls: Set[str],
    ) -> None:
        """Extract the chart itself and its declared dependencies."""
        chart_name = data.get("name")
        chart_version = data.get("version")

        if chart_name:
            # The chart's own repository is not in Chart.yaml — it's wherever
            # this file lives.  We omit repository_url for the top-level chart.
            purl = helm_chart_to_purl(str(chart_name), str(chart_version) if chart_version else None)
            if purl not in seen_purls:
                seen_purls.add(purl)
                components.append(
                    SbomComponentModel(
                        component_type="library",
                        name=str(chart_name),
                        version=str(chart_version) if chart_version else None,
                        purl=purl,
                        properties={},
                        source_collector=self.get_collector_name(),
                    )
                )

        # Chart dependencies
        dependencies = data.get("dependencies")
        if not isinstance(dependencies, list):
            return

        for dep in dependencies:
            if not isinstance(dep, dict):
                continue
            dep_name = dep.get("name")
            dep_version = dep.get("version")
            dep_repo = dep.get("repository")

            if not dep_name:
                continue

            # Skip file:// dependencies (local charts)
            if isinstance(dep_repo, str) and dep_repo.startswith("file://"):
                continue

            purl = helm_chart_to_purl(
                str(dep_name),
                str(dep_version) if dep_version else None,
                str(dep_repo) if dep_repo else None,
            )
            if purl not in seen_purls:
                seen_purls.add(purl)
                components.append(
                    SbomComponentModel(
                        component_type="library",
                        name=str(dep_name),
                        version=str(dep_version) if dep_version else None,
                        purl=purl,
                        properties={},
                        source_collector=self.get_collector_name(),
                    )
                )
