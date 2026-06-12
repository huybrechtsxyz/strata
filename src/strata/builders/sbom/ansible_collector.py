"""Collect Ansible collection and role components from requirements.yml files."""

from pathlib import Path
from typing import Any, List, Optional

import yaml

from strata.builders.sbom.base_sbom_collector import BaseSbomCollector
from strata.logger import get_logger
from strata.models.common_models import ProvisionerType
from strata.models.platform_artifact_model import PlatformArtifactModel
from strata.models.sbom_model import SbomComponentModel
from strata.utils.sbom_utils import ansible_collection_to_purl, ansible_role_to_purl

logger = get_logger(__name__)


class AnsibleCollectionCollector(BaseSbomCollector):
    """Collects Ansible collection and role components from ``requirements.yml`` files.

    Scans the deployment build path for ``requirements.yml`` files beneath any
    ansible provisioner build directory.  Supports both ``collections:`` and
    ``roles:`` sections in the standard Ansible Galaxy requirements format.

    Files that cannot be parsed produce a warning and are skipped.
    """

    def get_collector_name(self) -> str:
        return "ansible"

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

        has_ansible = any(p.provisioner == ProvisionerType.ANSIBLE for p in platform.spec.provisioners)
        if not has_ansible:
            return components

        if not deployment_build_path.exists():
            return components

        for req_file in sorted(deployment_build_path.rglob("requirements.yml")):
            components.extend(self._parse_requirements_file(req_file))

        return components

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_requirements_file(self, req_file: Path) -> List[SbomComponentModel]:
        """Parse a requirements.yml and return ``SbomComponentModel`` entries."""
        components: List[SbomComponentModel] = []
        try:
            with req_file.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except Exception as exc:
            warning = f"Failed to parse {req_file.name}: {exc}"
            self._warnings.append(warning)
            logger.warning("Failed to parse ansible requirements", file=str(req_file), error=str(exc))
            return components

        if not isinstance(data, dict):
            return components

        for entry in data.get("collections") or []:
            component = self._entry_to_component(entry, kind="collection")
            if component:
                components.append(component)

        for entry in data.get("roles") or []:
            component = self._entry_to_component(entry, kind="role")
            if component:
                components.append(component)

        return components

    def _entry_to_component(self, entry: Any, kind: str) -> Optional[SbomComponentModel]:
        """Convert a single requirements entry dict to a ``SbomComponentModel``."""
        if not isinstance(entry, dict):
            return None
        name = entry.get("name")
        if not name:
            return None
        version_raw = entry.get("version")
        version = str(version_raw) if version_raw is not None else None

        purl = (
            ansible_collection_to_purl(str(name), version)
            if kind == "collection"
            else ansible_role_to_purl(str(name), version)
        )

        return SbomComponentModel(
            component_type="library",
            name=str(name),
            version=version,
            purl=purl,
            properties={},
            source_collector=self.get_collector_name(),
        )
