"""AuditController — orchestrates deploy-log writing (Layer 2) and audit event routing (Layer 4).

Responsibilities:
- Assemble and write deploy-log JSON to disk (_execution.json + per-stage files)
- Resolve output path from Jinja2 templates (spec.deployment.paths)
- Query existing deploy-log entries for reporting (strata audit changes)
- Best-effort PR enrichment via GitHub CLI (enrich_with_pr_data)
- Route audit events to the journal and configured sinks (forward), wrapped in a
  CloudEvents 1.0 + ECS envelope (ADR-0066)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from strata.controllers.base_controller import BaseController
from strata.models.audit_config_model import AuditConfigModel
from strata.models.deploy_log_model import (
    DeployLogModel,
    DeployLogStageFileModel,
)
from strata.utils.output_writer import OutputWriter

if TYPE_CHECKING:
    from strata.integrations.git import GitIntegration
    from strata.models.capabilities import ISiemSink
    from strata.validators.policies.base_policy import PolicyResult

# Built-in path definitions — used when spec.deployment.paths is absent
BUILTIN_PATH_DEFINITIONS: Dict[str, str] = {
    # ── Simple ──────────────────────────────────────────────────────────────
    "flat": "{{ deployment }}",
    "by-stage": "{{ deployment }}/{{ stage }}",
    # ── Time-based (default) ────────────────────────────────────────────────
    "by-execution": "{{ deployment }}/{{ timestamp }}",
    "by-date": "{{ deployment }}/{{ date }}/{{ timestamp }}",
    # ── Layer / environment ─────────────────────────────────────────────────
    "by-environment": "{{ environment }}/{{ deployment }}/{{ timestamp }}",
    "by-workspace": "{{ workspace }}/{{ deployment }}/{{ timestamp }}",
    # ── Multi-level ─────────────────────────────────────────────────────────
    "by-tenant": "{{ tenant }}/{{ deployment }}/{{ timestamp }}",
    "full": "{{ tenant }}/{{ workspace }}/{{ environment }}/{{ deployment }}/{{ timestamp }}",
}

# CloudEvents `type` reverse-DNS prefix (ADR-0066) — from apiVersion: strata.huybrechts.xyz/v1
_CE_TYPE_PREFIX = "xyz.huybrechts.strata."

# event_type -> (ECS event.kind, ECS event.category or None) — from the ADR's type-name table.
# workitem.created/resumed classified alongside deployment.completed (Outcome class,
# configuration category) — added to the closed enum in step 4, not in the ADR's original table.
_EVENT_TYPE_METADATA: Dict[str, Tuple[str, Optional[List[str]]]] = {
    "command.executed": ("event", ["process"]),
    "deployment.completed": ("event", ["configuration"]),
    "deployment.measured": ("metric", None),
    "build.completed": ("event", ["package"]),
    "validation.completed": ("event", ["configuration"]),
    "workitem.created": ("event", ["configuration"]),
    "workitem.resumed": ("event", ["configuration"]),
    "policy.violated": ("alert", ["configuration"]),
    "secret.accessed": ("event", ["iam"]),
    "lock.acquired": ("event", ["process"]),
    "lock.released": ("event", ["process"]),
    "drift.detected": ("alert", ["configuration"]),
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

        return OutputWriter.resolve_structured_output_dir(
            base_path=base_path,
            structure=structure,
            path_definitions=path_definitions,
            builtin_path_definitions=BUILTIN_PATH_DEFINITIONS,
            context=context,
        )

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

    def push_to_remote(
        self,
        paths: List[Path],
        remote_name: str = "origin",
        working_dir: Optional[Path] = None,
    ) -> bool:
        """Stage, commit, and push deploy-log files to a git remote.

        Args:
            paths:       List of deploy-log file paths to commit.
            remote_name: Git remote name to push to (default: 'origin').
            working_dir: Directory to run git operations from.  Defaults to
                         ``self._work_path``.  Pass the resolved path of a
                         registered solution repo to push from that repo's
                         working tree.

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

        wd = str(working_dir) if working_dir else str(self._work_path)
        base = working_dir if working_dir else self._work_path

        # Stage the files
        relative_paths = []
        for p in paths:
            try:
                relative_paths.append(str(p.relative_to(base)))
            except ValueError:
                relative_paths.append(str(p))

        result = git.add(wd, relative_paths)
        if result.returncode != 0:
            self.logger.warning("push_to_remote_add_failed", stderr=result.stderr)
            return False

        # Commit
        result = git.commit(wd, "chore(audit): deploy-log update [skip ci]")
        if result.returncode != 0:
            # Nothing to commit is acceptable (returncode 1 with "nothing to commit")
            if "nothing to commit" in (result.stdout or "") + (result.stderr or ""):
                self.logger.debug("push_to_remote_nothing_to_commit")
                return True
            self.logger.warning("push_to_remote_commit_failed", stderr=result.stderr)
            return False

        # Push
        result = git.push(wd, remote=remote_name)
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
                labels=[lbl.get("name", "") for lbl in pr.get("labels", []) if isinstance(lbl, dict)],
                files_changed=[f.get("path", "") for f in pr.get("files", []) if isinstance(f, dict)],
            )
        except Exception as exc:
            self.logger.debug("enrich_pr_data_failed", error=str(exc))

        return payload

    def forward(
        self,
        event_type: str,
        payload: dict,
        audit_config: Optional[AuditConfigModel] = None,
    ) -> None:
        """Route *payload* to the journal, then fan out to configured sinks (ADR-0066).

        Single routing entrypoint — replaces the deleted ``forward_to_siem()`` (which
        hardcoded the event type and only handled built-in sink types) plus the sink
        resolution previously duplicated in ``RunDeployCommand._resolve_siem_sinks()``
        / ``_forward_workitem_event()`` (problem 8). Sinks are resolved here from
        ``AuditConfigModel`` and the already-initialised integration registry — the one
        path every caller (deploy, work items, ``resend``) now goes through, which is
        also what makes ``resend`` reach integration-backed sinks for the first time
        (problem 7).

        The gate is consulted first (problem 1): if ``policy.events`` does not admit
        *event_type*, nothing is written to the journal and no sink is consulted —
        turning an event class off stops egress everywhere in one edit. A recognised
        event type not explicitly configured falls back to its class-aware default; an
        event type outside the closed set (a producer this model doesn't know about) is
        never gated off here, only validated where it's explicitly configured (sink
        filters) — see ``AuditPolicyModel.is_enabled``.

        *payload* is the plain, flat dict callers already build (a ``DeployLogModel``
        dump, a work-item dict, ...) — callers know nothing of the envelope shape, only
        enough to populate it. This method wraps it into a CloudEvents 1.0 envelope with
        ECS fields under ``data`` (problems 5, 6) before writing to the journal or
        handing it to a sink, so both receive the identical, fully-formed record.

        Best-effort: sink failures are logged (``warning``) but never raised — audit
        must never fail a deploy (ADR-0018).
        """
        cfg = audit_config or self._audit_config
        if not cfg.policy.is_enabled(event_type):
            return

        envelope = self._build_envelope(event_type, payload)

        from strata.logger import audit as journal_audit

        journal_audit(event_type, outcome=envelope["data"]["event"].get("outcome", "success"), detail=envelope)

        for integration in self._resolve_sinks(event_type, cfg):
            try:
                integration.send_event(event_type, envelope)
            except Exception as exc:
                self.logger.warning(
                    "forward_sink_failed",
                    sink=getattr(integration, "integration_name", "?"),
                    event_type=event_type,
                    error=str(exc),
                )

    def forward_policy_violation(
        self,
        result: "PolicyResult",
        execution_id: Optional[str] = None,
        deployment: Optional[str] = None,
        workspace: Optional[str] = None,
        audit_config: Optional[AuditConfigModel] = None,
    ) -> None:
        """Forward a ``policy.violated`` event for one failed ``PolicyResult`` (ADR-0066 follow-up).

        ``PolicyEngine`` (``validators/``) cannot call ``forward()`` itself — it sits
        *below* ``controllers/`` in ADR-0003's layering chain, so the trigger has to
        live at each policy-evaluating command (``validate``, ``build``, ``deploy``,
        ``check_policy``), right where it already loops over ``PolicyEngine.evaluate()``
        results. This method centralises everything *except* that trigger — payload
        shape, config resolution, and failure handling — in the one place ``forward()``
        itself lives, rather than duplicating it at all four call sites (problem 8's
        own reasoning, applied to the one part of it that can be centralised).

        Resolves ``AuditConfigModel`` from the already-populated ``ConfigurationService``
        singleton when *audit_config* is not given (matching ``BaseCommand``'s own
        ``command.executed`` / lock / drift forwarding). Never raises: a forwarding
        failure must never fail a validate/build/deploy/policy-check run.
        """
        try:
            cfg = audit_config
            if cfg is None:
                try:
                    from strata.services.configuration_service import ConfigurationService

                    config_model = ConfigurationService.get_instance().model
                    cfg = getattr(getattr(config_model, "spec", None), "audit", None)
                except Exception as e:
                    self.logger.debug(f"Failed to resolve spec.audit for policy.violated (non-fatal): {e}")

            payload = {
                "execution_id": execution_id,
                "deployment": deployment,
                "workspace": workspace,
                "policy_name": result.policy_name,
                "policy_type": result.policy_type,
                "enforcement": result.enforcement,
                "violations": result.violations,
                "success": result.passed,
            }
            self.forward("policy.violated", payload, audit_config=cfg)
        except Exception as e:
            self.logger.debug(f"Failed to forward policy.violated audit event (non-fatal): {e}")

    @staticmethod
    def _build_envelope(event_type: str, payload: dict) -> Dict[str, Any]:
        """Wrap *payload* in a CloudEvents 1.0 envelope with ECS fields under ``data`` (ADR-0066).

        CloudEvents supplies identity and timing (``id``, ``time``, ``source``,
        ``subject``); ECS supplies ``event.kind``/``category``/``action``/``outcome``,
        ``user.name`` (from ``resolve_actor()``, ADR-0066/ADR-0067), and ``labels`` for
        the correlation dimensions (``execution_id`` — problem 6). Everything
        strata-specific — the original, unmodified *payload* — lives under the
        explicitly namespaced ``data.strata`` bag.

        Best-effort field extraction throughout: *payload* may be a full
        ``DeployLogModel`` dump, a work-item dict, or (from tests / future producers)
        an arbitrary dict — every field is read via ``.get()`` and omitted if absent,
        never raising for a producer that doesn't populate every field.

        ``id`` is a fresh UUID per call, independent of ``execution_id`` — CloudEvents
        requires ``(source, id)`` together to uniquely identify *this* event, whereas
        ``execution_id`` is a *correlation* key deliberately shared across every event
        from the same command run (e.g. a deploy's own ``deployment.completed`` and a
        gate's ``workitem.created`` can share ``execution_id`` and ``source`` — using
        ``execution_id`` as ``id`` would make those two distinct events collide on
        ``(source, id)``). ``execution_id`` still carries the correlation key, in
        ``labels.execution_id`` only.
        """
        from strata.controllers.actor_controller import resolve_actor

        kind, category = _EVENT_TYPE_METADATA.get(event_type, ("event", None))

        execution_id = payload.get("execution_id") or str(uuid.uuid4())
        event_time = payload.get("timestamp") or datetime.now(timezone.utc).isoformat()
        deployment = payload.get("deployment")
        workspace = payload.get("workspace")
        success = payload.get("success")
        duration_seconds = payload.get("duration_seconds")

        event_data: Dict[str, Any] = {"kind": kind, "action": event_type.replace(".", "-")}
        if category is not None:
            event_data["category"] = category
        if success is not None:
            event_data["outcome"] = "success" if success else "failure"
        if duration_seconds is not None:
            event_data["duration"] = int(duration_seconds * 1_000_000_000)  # ECS: nanoseconds

        labels: Dict[str, Any] = {"execution_id": execution_id}
        for key in ("workspace", "environment", "deployment", "tenant"):
            value = payload.get(key)
            if value is not None:
                labels[key] = value

        return {
            "specversion": "1.0",
            "type": f"{_CE_TYPE_PREFIX}{event_type}",
            "source": f"/strata/{workspace or 'unknown'}/{deployment or 'unknown'}",
            "id": str(uuid.uuid4()),
            "time": event_time,
            "datacontenttype": "application/json",
            "subject": deployment or event_type,
            "data": {
                "event": event_data,
                "user": {"name": resolve_actor()},
                "labels": labels,
                "strata": payload,
            },
        }

    def _resolve_sinks(
        self,
        event_type: str,
        audit_config: Optional[AuditConfigModel],
    ) -> List["ISiemSink"]:
        """Resolve enabled, event-admitting sinks to their backing integration instances.

        Every sink is now an integration reference (ADR-0066) — resolved through the
        already-initialised ``IntegrationService`` registry, the same path every other
        controller uses to look up a configured integration by name.
        """
        resolved: List["ISiemSink"] = []
        if not audit_config or not audit_config.sinks:
            return resolved

        from strata.models.capabilities import ISiemSink
        from strata.services.integration_service import IntegrationService

        try:
            svc = IntegrationService.get_instance()
            if not svc.is_initialized():
                svc.initialize_integrations()
        except Exception as exc:
            self.logger.warning("audit_sink_resolution_failed", error=str(exc))
            return resolved

        for sink in audit_config.sinks:
            if not sink.enabled:
                continue
            if sink.events is not None and event_type not in sink.events:
                continue
            try:
                integration = svc.get_integration(str(sink.integration))
            except Exception as exc:
                self.logger.warning("audit_sink_integration_lookup_failed", sink=sink.name, error=str(exc))
                continue
            if integration is None:
                self.logger.warning(
                    "audit_sink_integration_not_found", sink=sink.name, integration=str(sink.integration)
                )
                continue
            if not isinstance(integration, ISiemSink):
                self.logger.warning(
                    "audit_sink_integration_not_siem", sink=sink.name, integration=str(sink.integration)
                )
                continue
            resolved.append(integration)
        return resolved

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
                self.forward("deployment.completed", record.model_dump(exclude_none=True), audit_config=audit_config)
                sent += 1
            except Exception:
                failed += 1
        return sent, failed
