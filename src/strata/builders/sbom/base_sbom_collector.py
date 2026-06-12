"""Abstract base class for SBOM component collectors."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List

from strata.models.platform_artifact_model import PlatformArtifactModel
from strata.models.sbom_model import SbomComponentModel


class BaseSbomCollector(ABC):
    """Abstract base for a single SBOM component-type collector.

    Each concrete subclass extracts ``SbomComponentModel`` entries for one
    category of component (images, Helm charts, Terraform providers, Ansible
    collections) from the assembled ``PlatformArtifactModel``.

    Warnings (floating tags, missing files, parse errors) are accumulated in
    ``self._warnings`` and drained by the caller via ``get_warnings()`` after
    each ``collect()`` call.
    """

    def __init__(self) -> None:
        self._warnings: List[str] = []

    @abstractmethod
    def get_collector_name(self) -> str:
        """Return the short identifier for this collector.

        Examples: ``"image"``, ``"helm"``, ``"terraform"``, ``"ansible"``
        """
        raise NotImplementedError

    @abstractmethod
    def collect(
        self,
        platform: PlatformArtifactModel,
        work_path: Path,
        deployment_build_path: Path,
    ) -> List[SbomComponentModel]:
        """Extract components from the platform artifact.

        Args:
            platform: Assembled platform artifact model.
            work_path: Workspace root path.
            deployment_build_path: Deployment-specific build directory
                (e.g. ``{build_path}/{deployment_name}-{version}/``).

        Returns:
            List of components found.  Empty list when none applicable.
        """
        raise NotImplementedError

    def get_warnings(self) -> List[str]:
        """Return accumulated warnings from the most recent ``collect()`` call."""
        return list(self._warnings)

    def _reset_warnings(self) -> None:
        """Clear the warnings list at the start of each ``collect()`` call."""
        self._warnings = []
