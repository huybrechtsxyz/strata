"""Collect Terraform module components from module{} blocks in build-output .tf files."""

from pathlib import Path
from typing import Any, Dict, List, Optional

from strata.builders.sbom.base_sbom_collector import BaseSbomCollector
from strata.logger import get_logger
from strata.models.platform_artifact_model import PlatformArtifactModel
from strata.models.sbom_model import SbomComponentModel
from strata.utils.sbom_utils import is_local_module_source, terraform_module_to_purl

logger = get_logger(__name__)


class TerraformModuleCollector(BaseSbomCollector):
    """Collect Terraform module components from ``module {}`` blocks in ``*.tf`` files.

    Recursively scans the deployment build path for ``.tf`` files and extracts
    every ``module "<label>" { source = … }`` block via ``python-hcl2``.

    **python-hcl2 quirk:** string values are wrapped in extra double-quotes
    (``'"value"'``).  ``_strip_hcl_string()`` removes them.

    Skips local modules (``source`` starting with ``./`` or ``../``).
    Deduplicates by source string; first occurrence wins.
    Parse errors on individual files produce a warning but do not abort.
    """

    def get_collector_name(self) -> str:
        return "terraform-module"

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

        # Deduplicated module map: source_string → {version}
        modules: Dict[str, Optional[str]] = {}

        for tf_file in sorted(deployment_build_path.rglob("*.tf")):
            try:
                import hcl2

                with tf_file.open("r", encoding="utf-8") as fh:
                    data = hcl2.load(fh)
                self._extract_modules(data, modules)
            except Exception as exc:
                warning = f"Failed to parse {tf_file.name}: {exc}"
                self._warnings.append(warning)
                logger.warning("Failed to parse terraform file", file=str(tf_file), error=str(exc))

        for source, version in modules.items():
            purl = terraform_module_to_purl(source, version)
            if purl is None:
                # Local or unsupported source — silently skip (local) or warn (unsupported)
                continue

            # Derive a display name: namespace/module from the source path
            base = source.split("?")[0].split("//")[0]
            # Strip explicit registry host prefix if present
            if base.startswith("registry.terraform.io/"):
                base = base[len("registry.terraform.io/") :]
            parts = base.split("/")
            name = "/".join(parts[:2]) if len(parts) >= 2 else base

            components.append(
                SbomComponentModel(
                    component_type="library",
                    name=name,
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

    def _extract_modules(
        self,
        data: Dict[str, Any],
        modules: Dict[str, Optional[str]],
    ) -> None:
        """Extract ``module {}`` block entries into *modules* (deduplicated, first-wins).

        python-hcl2 represents labeled blocks as::

            {"module": [{"label": [{"source": '"..."', "version": '"..."'}]}]}
        """
        for block in data.get("module") or []:
            if not isinstance(block, dict):
                continue
            for _label, cfg_list in block.items():
                if _label == "__is_block__":
                    continue
                # cfg_list may be a list-of-dicts or a plain dict depending on hcl2 version
                cfgs = cfg_list if isinstance(cfg_list, list) else [cfg_list]
                for cfg in cfgs:
                    if not isinstance(cfg, dict):
                        continue
                    source = self._strip_hcl_string(cfg.get("source"))
                    if not source:
                        continue
                    if source in modules:
                        continue  # first occurrence wins
                    version = self._strip_hcl_string(cfg.get("version"))

                    # Skip local modules — they are not publishable components
                    if is_local_module_source(source):
                        logger.debug("Skipping local terraform module", source=source)
                        continue

                    modules[source] = version
