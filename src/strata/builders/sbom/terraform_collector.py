"""Collect Terraform provider components from build-output .tf files."""

from pathlib import Path
from typing import Any, Dict, List, Optional

from strata.builders.sbom.base_sbom_collector import BaseSbomCollector
from strata.logger import get_logger
from strata.models.platform_artifact_model import PlatformArtifactModel
from strata.models.sbom_model import SbomComponentModel
from strata.utils.sbom_utils import terraform_provider_to_purl

logger = get_logger(__name__)


class TerraformProviderCollector(BaseSbomCollector):
    """Collects Terraform provider components from ``*.tf`` files in the build directory.

    Recursively scans the deployment build path for ``.tf`` files and extracts
    every entry in ``terraform { required_providers { … } }`` blocks via
    ``python-hcl2``.

    **python-hcl2 quirk:** all HCL string values are wrapped in extra double
    quotes (``'"value"'``).  The ``_strip_hcl_string()`` helper removes them.

    Providers discovered in multiple files are deduplicated by name; the first
    occurrence wins.  Parse errors on individual files produce a warning but do
    not abort the collection.
    """

    def get_collector_name(self) -> str:
        return "terraform"

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

        # Deduplicated provider map: name → {source, version}
        providers: Dict[str, Dict[str, str]] = {}

        for tf_file in sorted(deployment_build_path.rglob("*.tf")):
            try:
                import hcl2

                with tf_file.open("r", encoding="utf-8") as fh:
                    data = hcl2.load(fh)
                self._extract_required_providers(data, providers)
            except Exception as exc:
                warning = f"Failed to parse {tf_file.name}: {exc}"
                self._warnings.append(warning)
                logger.warning("Failed to parse terraform file", file=str(tf_file), error=str(exc))

        for provider_name, cfg in providers.items():
            source = cfg.get("source", provider_name)
            version = cfg.get("version")
            purl = terraform_provider_to_purl(source, version)
            components.append(
                SbomComponentModel(
                    component_type="library",
                    name=provider_name,
                    version=version,
                    purl=purl,
                    properties={},
                    source_collector=self.get_collector_name(),
                )
            )

        return components

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_hcl_string(value: Any) -> Optional[str]:
        """Strip surrounding double-quotes that python-hcl2 adds to string values."""
        if not isinstance(value, str):
            return str(value) if value is not None else None
        return value.strip('"')

    def _extract_required_providers(
        self,
        data: Dict[str, Any],
        providers: Dict[str, Dict[str, str]],
    ) -> None:
        """Extract ``required_providers`` entries into *providers* (deduplicated, first-wins)."""
        for block in data.get("terraform") or []:
            if not isinstance(block, dict):
                continue
            for rp in block.get("required_providers") or []:
                if not isinstance(rp, dict):
                    continue
                for provider_name, cfg in rp.items():
                    if provider_name == "__is_block__":
                        continue
                    if not isinstance(cfg, dict):
                        continue
                    if provider_name in providers:
                        continue  # first occurrence wins
                    source = self._strip_hcl_string(cfg.get("source"))
                    version = self._strip_hcl_string(cfg.get("version"))
                    entry: Dict[str, str] = {}
                    if source:
                        entry["source"] = source
                    if version:
                        entry["version"] = version
                    providers[provider_name] = entry
