"""Work-item backend factory — resolves the correct backend from configuration.

Backend selection priority (mirrors lock backend resolution):
  configuration.backend.type → STRATA_WORKITEM_BACKEND env var → "local"

Supported types:
  local          — .strata/workitems/*.json (default, no dependencies)
  git_tag        — annotated git tags (distributed, no cloud needed)
  s3             — AWS S3 (aws CLI required)
  azblob         — Azure Blob Storage (az CLI required)
  gcs            — GCP Cloud Storage (gcloud CLI required)
  cloud_native   — Delegates to S3/AzBlob/GCS + CI/CD notification hook
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from strata.integrations.workitem.base_workitem_backend import BaseWorkItemBackend
from strata.logger import get_logger

logger = get_logger(__name__)


class WorkItemBackendFactory:
    """Creates the appropriate work-item backend from configuration."""

    @staticmethod
    def create(
        work_path: Path,
        backend_type: Optional[str] = None,
        configuration: Optional[Dict[str, Any]] = None,
    ) -> BaseWorkItemBackend:
        """Instantiate the backend matching *backend_type*.

        Resolution order:
        1. Explicit *backend_type* argument
        2. ``STRATA_WORKITEM_BACKEND`` environment variable
        3. ``"local"`` (default)
        """
        resolved_type = (backend_type or os.environ.get("STRATA_WORKITEM_BACKEND", "").strip() or "local").lower()

        cfg = configuration or {}

        if resolved_type in ("local", ""):
            from strata.integrations.workitem.workitem_local import LocalWorkItemBackend

            return LocalWorkItemBackend(work_path)

        if resolved_type == "git_tag":
            from strata.integrations.workitem.workitem_gittag import GitTagWorkItemBackend

            return GitTagWorkItemBackend(work_path)

        if resolved_type == "s3":
            from strata.integrations.workitem.workitem_s3 import S3WorkItemBackend

            return S3WorkItemBackend(cfg, work_path)

        if resolved_type in ("azblob", "azure", "azurerm"):
            from strata.integrations.workitem.workitem_azblob import AzureBlobWorkItemBackend

            return AzureBlobWorkItemBackend(cfg, work_path)

        if resolved_type in ("gcs", "gcp"):
            from strata.integrations.workitem.workitem_gcs import GCSWorkItemBackend

            return GCSWorkItemBackend(cfg, work_path)

        if resolved_type == "cloud_native":
            from strata.integrations.workitem.workitem_cloud_native import CloudNativeWorkItemBackend

            return CloudNativeWorkItemBackend(cfg, work_path)

        logger.warning(
            "workitem_factory.unknown_backend_type",
            type=resolved_type,
            fallback="local",
        )
        from strata.integrations.workitem.workitem_local import LocalWorkItemBackend

        return LocalWorkItemBackend(work_path)
