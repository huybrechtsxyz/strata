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
    # Layer 4 — Remote push, PR enrichment, SIEM forwarding
    # ------------------------------------------------------------------

    def push_to_remote(self, paths: List[Path], remote_name: str = "origin") -> bool:
        """Stage, commit, and push deploy-log files to a git remote.

        Args:
            paths: List of deploy-log file paths to commit.
            remote_name: Git remote name to push to.

        Returns:
            True if push succeeded, False otherwise.
        """
        if not paths:
            return False

        from strata.integrations.factory import IntegrationFactory

        git: GitIntegration = IntegrationFactory.create_by_type("git")  # type: ignore[assignment]
        available, _ = git.ensure_available()
        if not available:
            self.logger.warning("push_to_remote_git_unavailable")
            return False

        working_dir = str(self._work_path)

        # Stage the files
        relative_paths = []
        for p in paths:
            try:
                relative_paths.append(str(p.relative_to(self._work_path)))
            except ValueError:
                relative_paths.append(str(p))

        result = git.add(working_dir, relative_paths)
        if result.returncode != 0:
            self.logger.warning("push_to_remote_add_failed", stderr=result.stderr)
            return False

        # Commit
        result = git.commit(working_dir, "chore(audit): deploy-log update [skip ci]")
        if result.returncode != 0:
            # Nothing to commit is acceptable (returncode 1 with "nothing to commit")
            if "nothing to commit" in (result.stdout or "") + (result.stderr or ""):
                self.logger.debug("push_to_remote_nothing_to_commit")
                return True
            self.logger.warning("push_to_remote_commit_failed", stderr=result.stderr)
            return False

        # Push
        result = git.push(working_dir, remote=remote_name)
        if result.returncode != 0:
            self.logger.warning("push_to_remote_push_failed", stderr=result.stderr)
            return False

        return True

    def enrich_with_pr_data(self, payload: DeployLogModel) -> DeployLogModel:
        """Best-effort PR enrichment via GitHub CLI.

        Looks up the merged PR that contains payload.commit_sha using `gh`.
        On any failure, returns payload unchanged (best-effort).
        """
        if not payload.commit_sha:
            return payload

        try:
            from strata.models.deploy_log_model import DeployLogPullRequestModel
            from strata.utils.system import run_command

            # Find PR associated with the commit
            result = run_command(
                [
                    "gh",
                    "pr",
                    "list",
                    "--state",
                    "merged",
                    "--search",
                    payload.commit_sha,
                    "--json",
                    "number,title,url,author,mergedBy,mergedAt,labels,files",
                    "--limit",
                    "1",
                ],
                cwd=str(self._work_path),
                timeout=15,
            )
            if result.returncode != 0 or not result.stdout:
                return payload

            import json as _json

            prs = _json.loads(result.stdout)
            if not prs:
                return payload

            pr = prs[0]
            payload.pull_request = DeployLogPullRequestModel(
                number=pr.get("number", 0),
                title=pr.get("title", ""),
                url=pr.get("url", ""),
                author=pr.get("author", {}).get("login") if isinstance(pr.get("author"), dict) else None,
                merged_by=pr.get("mergedBy", {}).get("login") if isinstance(pr.get("mergedBy"), dict) else None,
                merged_at=pr.get("mergedAt"),
                labels=[l.get("name", "") for l in pr.get("labels", []) if isinstance(l, dict)],
                files_changed=[f.get("path", "") for f in pr.get("files", []) if isinstance(f, dict)],
            )
        except Exception as exc:
            self.logger.debug("enrich_pr_data_failed", error=str(exc))

        return payload

    def forward_to_siem(
        self,
        payload: DeployLogModel,
        audit_config: Optional[AuditConfigModel] = None,
    ) -> None:
        """Forward a deploy-log entry to configured sinks (webhook/syslog/stdout/ndjson).

        Best-effort: failures are logged but never raised.
        """
        if not audit_config or not audit_config.sinks:
            return

        data = payload.model_dump(exclude_none=True)

        for sink in audit_config.sinks:
            if not sink.enabled:
                continue

            # Event filter
            if sink.events and "deploy_audit" not in sink.events:
                continue

            try:
                match sink.type:
                    case "stdout":
                        import sys

                        sys.stdout.write(json.dumps(data, default=str) + "\n")
                        sys.stdout.flush()
                    case "ndjson":
                        if sink.path:
                            ndjson_path = Path(sink.path)
                            ndjson_path.parent.mkdir(parents=True, exist_ok=True)
                            with open(ndjson_path, "a", encoding="utf-8") as f:
                                f.write(json.dumps(data, default=str) + "\n")
                    case "syslog":
                        if sink.address:
                            self._send_syslog(data, sink.address)
                    case "webhook":
                        if sink.url:
                            self._send_webhook(data, sink.url, sink.headers)
            except Exception as exc:
                self.logger.debug("forward_to_siem_sink_failed", sink=sink.name, error=str(exc))

    def _send_webhook(self, data: dict, url: str, headers: Optional[Dict[str, str]] = None) -> None:
        """Send payload to a webhook URL via urllib (no external dependencies)."""
        import urllib.request

        req_headers = {"Content-Type": "application/json"}
        if headers:
            req_headers.update(headers)

        body = json.dumps(data, default=str).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
        with urllib.request.urlopen(req, timeout=10):  # noqa: S310 — URL comes from user config
            pass

    def _send_syslog(self, data: dict, address: str) -> None:
        """Send payload to a syslog server via UDP."""
        import socket

        host, _, port_str = address.rpartition(":")
        port = int(port_str) if port_str else 514
        if not host:
            host = address

        message = f"<14>strata audit: {json.dumps(data, default=str)}"
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(message.encode("utf-8")[:65000], (host, port))
        finally:
            sock.close()

    def resend(
        self,
        base_path: Path,
        audit_config: Optional[AuditConfigModel] = None,
        since: Optional[str] = None,
        last: Optional[int] = None,
    ) -> Tuple[int, int]:
        """Re-forward local deploy-log records to configured sinks.

        Returns (sent_count, failed_count).
        """
        records = self.query_deploy_logs(base_path, since=since, last=last)
        sent = 0
        failed = 0
        for record in records:
            try:
                self.forward_to_siem(record, audit_config=audit_config)
                sent += 1
            except Exception:
                failed += 1
        return sent, failed
