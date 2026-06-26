"""Service for loading, saving, and querying deployment manifests.

Deployment manifests are written by the deploy command after each run and
stored according to the manifest configuration in the platform configuration
file.  When no manifest config is defined, manifests are not written.

This service provides I/O, path resolution, and query capabilities.
"""

from pathlib import Path
from typing import List, Optional, Tuple

from strata.models.configuration_model import (
    ConfigurationManifestModel,
    ConfigurationModel,
)
from strata.models.deployment_manifest_model import DeploymentManifestModel
from strata.services.base_service import BaseService
from strata.utils.output_writer import OutputWriter


class DeploymentManifestService(BaseService["DeploymentManifestModel"]):
    """Service for managing deployment manifest I/O operations."""

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None) -> None:
        super().__init__(path, data)
        self.model: Optional[DeploymentManifestModel] = None

    # ------------------------------------------------------------------
    # BaseService abstract-method implementations
    # ------------------------------------------------------------------

    def _load_data(self) -> None:
        """Allow creation without a path (for save-only usage)."""
        if self.path is None and self.data is None:
            self.logger.debug("No path or data provided — service created for saving only")
            return
        super()._load_data()

    def _get_model_class(self):
        return DeploymentManifestModel

    def _validate_dynamic(
        self,
        configuration_model: Optional[ConfigurationModel] = None,
        work_path: Optional[str] = None,
        **kwargs,
    ) -> Tuple[bool, List[str]]:
        """Deployment manifests are output artifacts — no cross-reference checks needed."""
        return True, []

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    @staticmethod
    def resolve_output_dir(
        manifest_config: ConfigurationManifestModel,
        work_path: Path,
        deployment_name: str,
        version: Optional[str] = None,
    ) -> Path:
        """Resolve the output directory for a manifest based on configuration.

        Structure: {base_path}/{deployment_name}/{version}/
        Falls back to {base_path}/{deployment_name}/ when version is not set.

        Args:
            manifest_config: The manifest configuration from the platform config.
            work_path: Workspace root path.
            deployment_name: Name of the deployment.
            version: Optional version string (from deployment meta labels).

        Returns:
            Resolved absolute path to the output directory.
        """
        base = Path(manifest_config.path)
        if not base.is_absolute():
            base = work_path / base

        return OutputWriter.resolve_versioned_output_dir(
            base_path=base,
            deployment_name=deployment_name,
            version=version,
        )

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save(self, manifest: DeploymentManifestModel, output_dir: Path) -> Path:
        """Write a deployment manifest to disk.

        Filename: ``{deployment_name}_{timestamp}.json`` where timestamp is
        derived from ``spec.started_at`` (compacted to digits).

        Args:
            manifest: The manifest to persist.
            output_dir: Directory to write into (created if missing).

        Returns:
            Path to the written file.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # Compact ISO timestamp to a filename-safe string: 20260611T140000
        ts = manifest.spec.started_at.replace("-", "").replace(":", "").replace(" ", "")
        # Take up to 15 chars: YYYYMMDDTHHMMSS
        ts_compact = ts[:15]

        filename = f"{manifest.spec.deployment_name}_{ts_compact}.json"
        path = output_dir / filename
        path.write_text(
            manifest.model_dump_json(indent=2, exclude_none=True),
            encoding="utf-8",
        )
        self.logger.info("Deployment manifest saved", path=str(path))
        return path

    def save_with_config(
        self,
        manifest: DeploymentManifestModel,
        manifest_config: ConfigurationManifestModel,
        work_path: Path,
        version: Optional[str] = None,
    ) -> Path:
        """Write a manifest using the platform manifest configuration.

        Resolves the output directory from the manifest config, writes the
        file locally.  For gitops type, the file is written locally under
        the repository's deploy_path — the actual git commit/push/tag is
        the responsibility of the caller (deploy command) via the git
        integration.

        Args:
            manifest: The manifest model to persist.
            manifest_config: Manifest storage configuration.
            work_path: Workspace root.
            version: Optional deployment version (from labels).

        Returns:
            Path to the written manifest file.
        """
        deployment_name = str(manifest.spec.deployment_name)
        output_dir = self.resolve_output_dir(
            manifest_config=manifest_config,
            work_path=work_path,
            deployment_name=deployment_name,
            version=version,
        )
        return self.save(manifest, output_dir)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    @staticmethod
    def list_manifests(deployments_dir: Path) -> List[Path]:
        """List all manifest files in the deployments directory, newest first.

        Args:
            deployments_dir: Path to ``.strata/deployments/``.

        Returns:
            Sorted list of manifest file paths (newest first by filename).
        """
        if not deployments_dir.exists():
            return []
        return sorted(deployments_dir.glob("*.json"), reverse=True)

    @classmethod
    def get_latest(cls, deployments_dir: Path, deployment_name: Optional[str] = None) -> Optional[Path]:
        """Return the most recent manifest, optionally filtered by deployment name.

        Args:
            deployments_dir: Path to ``.strata/deployments/``.
            deployment_name: If set, only consider manifests for this deployment.

        Returns:
            Path to the latest manifest, or None.
        """
        manifests = cls.list_manifests(deployments_dir)
        if deployment_name:
            manifests = [m for m in manifests if m.stem.startswith(deployment_name + "_")]
        return manifests[0] if manifests else None
