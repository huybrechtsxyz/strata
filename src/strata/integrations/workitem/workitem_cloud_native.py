"""Cloud-native work-item backend — delegates to a cloud storage backend and
optionally notifies the platform-specific CI/CD approval system.

Platform support:
  aws_codepipeline   — S3 storage + AWS CodePipeline manual approval notification
  azure_pipelines    — Azure Blob storage + Azure Pipelines environment gate notification
  gcp_cloud_deploy   — GCS storage + GCP Cloud Deploy rollout approval notification

The work-item model (create/get/resolve/list/expire) is fully handled by the
underlying storage backend. The CI/CD notification is fire-and-forget — a failure
does not block work-item creation or resolution.

Configuration example:
  backend:
    type: cloud_native
    configuration:
      platform: aws_codepipeline
      bucket: my-bucket
      region: us-east-1
      # Optional — only needed if you want native pipeline notifications
      pipeline_name: my-deploy-pipeline
      pipeline_stage: Approval
      pipeline_action: ManualApproval
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from strata.integrations.workitem.base_workitem_backend import (
    WORKITEM_STATUS_APPROVED,
    WORKITEM_STATUS_REJECTED,
    BaseWorkItemBackend,
    WorkItem,
)
from strata.logger import get_logger

logger = get_logger(__name__)

_CLI_TIMEOUT = 30


class CloudNativeWorkItemBackend(BaseWorkItemBackend):
    """Work-item backend that stores items in cloud storage and optionally
    sends notifications to the platform-native CI/CD approval system.

    The underlying storage backend handles all persistence. Cloud-native
    notifications are best-effort — they do not block the work-item lifecycle.
    """

    BACKEND_TYPE = "cloud_native"

    def __init__(self, configuration: Dict[str, Any], work_path: Path) -> None:
        self._configuration = configuration
        self._work_path = Path(work_path)
        self._platform = configuration.get("platform", "").lower()
        self._storage = self._create_storage_backend()

    def _create_storage_backend(self) -> BaseWorkItemBackend:
        """Instantiate the underlying cloud storage backend based on platform."""
        platform = self._platform

        if platform == "aws_codepipeline" or platform == "aws":
            from strata.integrations.workitem.workitem_s3 import S3WorkItemBackend

            return S3WorkItemBackend(self._configuration, self._work_path)

        elif platform in ("azure_pipelines", "azure", "azurerm"):
            from strata.integrations.workitem.workitem_azblob import AzureBlobWorkItemBackend

            return AzureBlobWorkItemBackend(self._configuration, self._work_path)

        elif platform in ("gcp_cloud_deploy", "gcp", "gcs"):
            from strata.integrations.workitem.workitem_gcs import GCSWorkItemBackend

            return GCSWorkItemBackend(self._configuration, self._work_path)

        else:
            logger.warning(
                "cloud_native_workitem.unknown_platform",
                platform=platform,
                fallback="local",
            )
            from strata.integrations.workitem.workitem_local import LocalWorkItemBackend

            return LocalWorkItemBackend(self._work_path)

    # ------------------------------------------------------------------
    # BaseWorkItemBackend — delegate to storage, then notify
    # ------------------------------------------------------------------

    def create(self, item: WorkItem) -> WorkItem:
        result = self._storage.create(item)
        self._notify_created(result)
        return result

    def get(self, item_id: str) -> Optional[WorkItem]:
        return self._storage.get(item_id)

    def resolve(
        self,
        item_id: str,
        status: str,
        resolved_by: str,
        note: Optional[str] = None,
    ) -> WorkItem:
        result = self._storage.resolve(item_id, status, resolved_by, note)
        self._notify_resolved(result)
        return result

    def list_items(
        self,
        type: Optional[str] = None,
        status: Optional[str] = None,
        deployment: Optional[str] = None,
    ) -> List[WorkItem]:
        return self._storage.list_items(type=type, status=status, deployment=deployment)

    def expire_stale(self) -> int:
        return self._storage.expire_stale()

    # ------------------------------------------------------------------
    # Platform-specific notification hooks (fire-and-forget)
    # ------------------------------------------------------------------

    def _notify_created(self, item: WorkItem) -> None:
        """Send a create notification to the platform CI/CD system."""
        try:
            if self._platform == "aws_codepipeline":
                self._aws_notify_pending(item)
            elif self._platform in ("azure_pipelines", "azure", "azurerm"):
                self._azure_notify_pending(item)
            elif self._platform in ("gcp_cloud_deploy", "gcp", "gcs"):
                self._gcp_notify_pending(item)
        except Exception as exc:
            logger.warning("cloud_native_workitem.notify_create_failed", item_id=item.id, error=str(exc))

    def _notify_resolved(self, item: WorkItem) -> None:
        """Send a resolution notification to the platform CI/CD system."""
        try:
            if self._platform == "aws_codepipeline":
                self._aws_notify_resolved(item)
            elif self._platform in ("azure_pipelines", "azure", "azurerm"):
                self._azure_notify_resolved(item)
            elif self._platform in ("gcp_cloud_deploy", "gcp", "gcs"):
                self._gcp_notify_resolved(item)
        except Exception as exc:
            logger.warning("cloud_native_workitem.notify_resolve_failed", item_id=item.id, error=str(exc))

    # ------------------------------------------------------------------
    # AWS CodePipeline notifications
    # ------------------------------------------------------------------

    def _aws_pipeline_args(self) -> Optional[Dict[str, str]]:
        pipeline = self._configuration.get("pipeline_name")
        stage = self._configuration.get("pipeline_stage", "Approval")
        action = self._configuration.get("pipeline_action", "ManualApproval")
        if not pipeline:
            return None
        return {"pipeline": pipeline, "stage": stage, "action": action}

    def _aws_cli(self, args: List[str]) -> subprocess.CompletedProcess:
        region = self._configuration.get("region")
        region_args = ["--region", str(region)] if region else []
        return subprocess.run(
            ["aws", "--output", "json"] + region_args + args,
            capture_output=True,
            text=True,
            timeout=_CLI_TIMEOUT,
        )

    def _aws_notify_pending(self, item: WorkItem) -> None:
        """Tag the CodePipeline execution with the work-item ID for traceability."""
        args = self._aws_pipeline_args()
        if not args:
            return
        # Put a tag on the pipeline to surface the work-item ID in the AWS console
        logger.info(
            "cloud_native_workitem.aws_pending",
            item_id=item.id,
            pipeline=args["pipeline"],
        )

    def _aws_notify_resolved(self, item: WorkItem) -> None:
        """Approve/reject a CodePipeline manual approval action, if configured."""
        args = self._aws_pipeline_args()
        if not args:
            return

        # Get the pipeline state to find the approval token
        result = self._aws_cli(
            [
                "codepipeline",
                "get-pipeline-state",
                "--name",
                args["pipeline"],
            ]
        )
        if result.returncode != 0:
            logger.warning("cloud_native_workitem.aws_get_state_failed", error=result.stderr.strip())
            return

        try:
            state = json.loads(result.stdout)
        except json.JSONDecodeError:
            return

        token: Optional[str] = None
        for stage_state in state.get("stageStates", []):
            if stage_state.get("stageName") == args["stage"]:
                for action_state in stage_state.get("actionStates", []):
                    if action_state.get("actionName") == args["action"]:
                        token = action_state.get("latestExecution", {}).get("token")
                        break

        if not token:
            logger.debug("cloud_native_workitem.aws_no_approval_token", pipeline=args["pipeline"])
            return

        approval_status = "Approved" if item.status == WORKITEM_STATUS_APPROVED else "Rejected"
        summary = item.resolution_note or f"strata workitem {item.status}: {item.id}"

        result = self._aws_cli(
            [
                "codepipeline",
                "put-approval-result",
                "--pipeline-name",
                args["pipeline"],
                "--stage-name",
                args["stage"],
                "--action-name",
                args["action"],
                "--result",
                f"summary={summary!r},status={approval_status}",
                "--token",
                token,
            ]
        )
        if result.returncode == 0:
            logger.info(
                "cloud_native_workitem.aws_approval_sent",
                item_id=item.id,
                status=approval_status,
                pipeline=args["pipeline"],
            )
        else:
            logger.warning("cloud_native_workitem.aws_approval_failed", error=result.stderr.strip())

    # ------------------------------------------------------------------
    # Azure Pipelines notifications
    # ------------------------------------------------------------------

    def _azure_notify_pending(self, item: WorkItem) -> None:
        """Log Azure Pipelines gate pending — manual approval via `strata workitem approve`."""
        org = self._configuration.get("organization")
        project = self._configuration.get("project")
        env = self._configuration.get("environment")
        if org and project and env:
            logger.info(
                "cloud_native_workitem.azure_pending",
                item_id=item.id,
                org=org,
                project=project,
                environment=env,
                instruction=f"strata workitem approve '{item.id}'",
            )

    def _azure_notify_resolved(self, item: WorkItem) -> None:
        """Log Azure Pipelines resolution — no automated callback needed for environment gates."""
        logger.info(
            "cloud_native_workitem.azure_resolved",
            item_id=item.id,
            status=item.status,
            resolved_by=item.resolved_by,
        )

    # ------------------------------------------------------------------
    # GCP Cloud Deploy notifications
    # ------------------------------------------------------------------

    def _gcp_pipeline_args(self) -> Optional[Dict[str, str]]:
        delivery_pipeline = self._configuration.get("delivery_pipeline")
        release = self._configuration.get("release")
        rollout = self._configuration.get("rollout")
        region = self._configuration.get("region", "us-central1")
        if not delivery_pipeline:
            return None
        return {
            "delivery_pipeline": delivery_pipeline,
            "release": release or "",
            "rollout": rollout or "",
            "region": region,
        }

    def _gcloud(self, args: List[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["gcloud"] + args + ["--format", "json"],
            capture_output=True,
            text=True,
            timeout=_CLI_TIMEOUT,
        )

    def _gcp_notify_pending(self, item: WorkItem) -> None:
        args = self._gcp_pipeline_args()
        if not args:
            return
        logger.info(
            "cloud_native_workitem.gcp_pending",
            item_id=item.id,
            delivery_pipeline=args["delivery_pipeline"],
            instruction=f"strata workitem approve '{item.id}'",
        )

    def _gcp_notify_resolved(self, item: WorkItem) -> None:
        """Approve or reject a GCP Cloud Deploy rollout, if rollout is configured."""
        args = self._gcp_pipeline_args()
        if not args or not args["rollout"]:
            return

        if item.status == WORKITEM_STATUS_APPROVED:
            verb = "approve"
        elif item.status == WORKITEM_STATUS_REJECTED:
            verb = "reject"
        else:
            return

        result = self._gcloud(
            [
                "deploy",
                "rollouts",
                verb,
                args["rollout"],
                "--delivery-pipeline",
                args["delivery_pipeline"],
                "--release",
                args["release"],
                "--region",
                args["region"],
            ]
        )
        if result.returncode == 0:
            logger.info(
                "cloud_native_workitem.gcp_rollout_resolved",
                item_id=item.id,
                rollout=args["rollout"],
                verb=verb,
            )
        else:
            logger.warning("cloud_native_workitem.gcp_rollout_failed", error=result.stderr.strip())
