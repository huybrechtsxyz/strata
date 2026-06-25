"""AuditController — orchestrates deploy-log writing (Layer 2).

Responsibilities:
- Assemble and write deploy-log JSON to disk (_execution.json + per-stage files)
- Resolve output path from Jinja2 templates (spec.deployment.paths)
- Query existing deploy-log entries for reporting (strata audit changes)
- PR enrichment and SIEM forwarding are stubs in this phase (activated later)
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from strata.controllers.base_controller import BaseController
from strata.models.audit_config_model import AuditConfigModel
from strata.models.deploy_log_model import (
    DeployLogModel,
    DeployLogStageFileModel,
)
from strata.utils.templater import TemplateProcessor

if TYPE_CHECKING:
    from strata.integrations.git import GitIntegration

# Built-in path definitions — used when spec.deployment.paths is absent
BUILTIN_PATH_DEFINITIONS: Dict[str, str] = {
    "flat": "{{ deployment }}",
    "by-stage": "{{ deployment }}/{{ stage }}",
    "by-execution": "{{ deployment }}/{{ timestamp }}",
    "by-tenant": "{{ tenant }}/{{ deployment }}/{{ timestamp }}",
    "full": "{{ tenant }}/{{ workspace }}/{{ deployment }}/{{ timestamp }}",
}


class AuditController(BaseController):
    """Orchestrates Layer 2 (disk) and Layer 4 (SIEM + remote) audit operations."""

    def __init__(
        self,
        work_path: Path,
        audit_config: Optional[AuditConfigModel] = None,
        git_integration: Optional["GitIntegration"] = None,
    ) -> None:
        super().__init__()
        self._work_path = work_path
        self._audit_config = audit_config or AuditConfigModel()
        self._git = git_integration

    @staticmethod
    def generate_execution_id() -> str:
        """Generate a UUID4 execution identifier."""
        return str(uuid.uuid4())

    def write_deploy_log(
        self,
        payload: DeployLogModel,
        base_path: Path,
        structure: str = "by-execution",
        path_definitions: Optional[Dict[str, str]] = None,
        file_per_stage: bool = True,
    ) -> Tuple[bool, Optional[Path]]:
        """Write deploy-log JSON to disk.

        Returns:
            (success, path_to_execution_json). On failure returns (False, None)
            and accumulates errors.
        """
        try:
            defs = path_definitions or {}
            output_dir = self._resolve_output_dir(structure, defs, base_path, payload)
            output_dir.mkdir(parents=True, exist_ok=True)

            # Always write _execution.json (decision #5)
            exec_path = self._write_execution_json(payload, output_dir)

            # Write per-stage files if configured
            if file_per_stage:
                self._write_stage_files(payload, output_dir)

            self.logger.info(
                "deploy_log_written",
                path=str(exec_path),
                execution_id=payload.execution_id,
            )
            return True, exec_path

        except Exception as e:
            self.logger.warning(
                "deploy_log_write_failed",
                error=str(e),
                execution_id=payload.execution_id,
            )
            self._errors.append(f"Deploy-log write failed: {e}")
            return False, None

    def _resolve_output_dir(
        self,
        structure: str,
        path_definitions: Dict[str, str],
        base_path: Path,
        payload: DeployLogModel,
    ) -> Path:
        """Resolve deploy-log directory from named path or inline Jinja2 template."""
        # Resolve named path → Jinja2 template string
        template = path_definitions.get(
            structure,
            BUILTIN_PATH_DEFINITIONS.get(structure, structure),
        )

        # Sanitize timestamp for filesystem (replace : with -)
        fs_timestamp = payload.timestamp.replace(":", "-") if payload.timestamp else ""

        # Build template context from payload
        context = {
            "deployment": payload.deployment,
            "workspace": payload.workspace or "",
            "environment": payload.environment or "",
            "timestamp": fs_timestamp,
            "date": payload.timestamp[:10] if payload.timestamp else "",
            "stage": "",  # stage is only set for per-stage path resolution
            "tenant": "",  # resolved from deployment labels/properties in future
        }

        # Render via Jinja2
        rendered = TemplateProcessor.render(template, context)

        # Strip empty segments (from missing optional tokens)
        segments = [s for s in rendered.split("/") if s.strip()]
        if segments:
            return base_path / Path(*segments)
        return base_path

    def _write_execution_json(self, payload: DeployLogModel, output_dir: Path) -> Path:
        """Write _execution.json — always written (decision #5)."""
        exec_path = output_dir / "_execution.json"
        data = payload.model_dump(exclude_none=True)
        exec_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        return exec_path

    def _write_stage_files(self, payload: DeployLogModel, output_dir: Path) -> List[Path]:
        """Write per-stage JSON files when file_per_stage is True."""
        paths: List[Path] = []
        for stage in payload.stages:
            stage_file = DeployLogStageFileModel(
                execution_id=payload.execution_id,
                timestamp=payload.timestamp,
                version=payload.version,
                deployment=payload.deployment,
                stage=stage,
            )
            stage_path = output_dir / f"{stage.name}.json"
            data = stage_file.model_dump(exclude_none=True)
            stage_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            paths.append(stage_path)
        return paths

    def query_deploy_logs(
        self,
        base_path: Path,
        since: Optional[str] = None,
        stage: Optional[str] = None,
        last: Optional[int] = None,
    ) -> List[DeployLogModel]:
        """File discovery: recursively find _execution.json under base_path (decision #4).

        Parses each, filters by field values, returns sorted by timestamp descending.
        """
        results: List[DeployLogModel] = []

        if not base_path.exists():
            return results

        for json_file in base_path.rglob("_execution.json"):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                entry = DeployLogModel(**data)
                results.append(entry)
            except Exception as e:
                self.logger.debug("deploy_log_parse_skip", path=str(json_file), error=str(e))
                continue

        # Apply filters
        if since:
            results = [r for r in results if r.timestamp >= since]

        if stage:
            results = [r for r in results if any(s.name == stage for s in r.stages)]

        # Sort by timestamp descending
        results.sort(key=lambda r: r.timestamp, reverse=True)

        # Apply limit
        if last is not None and last > 0:
            results = results[:last]

        return results

    # ------------------------------------------------------------------
    # Stubs — activated in later implementation steps
    # ------------------------------------------------------------------

    def enrich_with_pr_data(self, payload: DeployLogModel) -> DeployLogModel:
        """Best-effort PR lookup — stub, activated in Step 11."""
        return payload

    def forward_to_siem(self, payload: DeployLogModel) -> None:
        """Fire-and-forget SIEM forwarding — stub, activated in Step 15."""
        pass

    def push_to_remote(self, paths: List[Path], remote_name: str) -> bool:
        """Commit + push to remote — stub, activated in Step 12."""
        return False

    def resend(
        self,
        base_path: Path,
        since: Optional[str] = None,
        last: Optional[int] = None,
    ) -> Tuple[int, int]:
        """Re-forward local records to SIEM — stub until Step 15.

        Returns (sent_count, failed_count).
        """
        records = self.query_deploy_logs(base_path, since=since, last=last)
        sent = 0
        failed = 0
        for record in records:
            self.forward_to_siem(record)
            sent += 1
        return sent, failed
