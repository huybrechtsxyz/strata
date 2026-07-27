"""Command to list recent deployment executions from the deploy-log."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import click

from strata.commands.schemas.schema_base_command import SchemaBaseCommand
from strata.controllers.audit_controller import AuditController
from strata.utils.config import get_deploy_log_dir


class ChangesAuditCommand(SchemaBaseCommand):
    """Query deploy-log entries for the current workspace."""

    OPERATION = "audit_changes"

    def __init__(
        self,
        last: int = 10,
        since: Optional[str] = None,
        stage: Optional[str] = None,
        ai: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._last = last
        self._since = since
        self._stage = stage
        self._ai = ai
        self._entries: List[Any] = []

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    @classmethod
    def show_console_header(cls, work_path: Optional[str] = None) -> None:
        """Suppress the standard base-command chrome."""

    @classmethod
    def show_console_footer(cls) -> None:
        """Suppress the standard base-command chrome."""

    def _execute(self) -> bool:
        base_path = get_deploy_log_dir(self._work_path)
        controller = AuditController(work_path=self._work_path)
        self._entries = controller.query_deploy_logs(
            base_path=base_path,
            since=self._since,
            stage=self._stage,
            last=self._last,
        )
        self._output_data = {
            "entries": [e.model_dump(exclude_none=True) for e in self._entries],
            "count": len(self._entries),
        }

        if self._ai and self._entries:
            self._run_ai_audit_summary()

        return True

    def _after_execute(self) -> bool:
        if self._output_format == "ndjson":
            import json

            for entry in self._entries:
                click.echo(json.dumps(entry.model_dump(exclude_none=True), default=str))
        elif self._is_console_output():
            self._render_console()
        return super()._after_execute()

    def _render_console(self) -> None:
        if not self._entries:
            click.echo("No deploy-log entries found.")
            return

        click.echo(f"{'Timestamp':<28} {'Deployment':<20} {'Success':<9} {'Duration':<10} {'Stages'}")
        click.echo("─" * 90)
        for entry in self._entries:
            status = "✓" if entry.success else "✗"
            duration = f"{entry.duration_seconds:.1f}s"
            click.echo(f"{entry.timestamp:<28} {entry.deployment:<20} {status:<9} {duration:<10} {len(entry.stages)}")
        click.echo(f"\n{len(self._entries)} entries shown.")

    # ------------------------------------------------------------------
    # AI audit history summary
    # ------------------------------------------------------------------

    def _run_ai_audit_summary(self) -> None:
        """Run AI summarisation of the queried deployment history."""
        from pathlib import Path

        from strata.integrations.ai import find_ai_integration

        config_svc = None
        try:
            from strata.controllers.solution_controller import SolutionController
            from strata.services.configuration_service import ConfigurationService

            sol = SolutionController(work_path=self._work_path)
            sol.load()
            profile, _ = sol.get_active_profile()
            if profile:
                for cp in [str(p.path) for p in (profile.configfile_paths or [])]:
                    svc = ConfigurationService.load(cp)
                    if svc.model:
                        config_svc = svc
                        break
        except Exception:
            pass

        integration = find_ai_integration(config_svc)
        if integration is None or not integration.ensure_available()[0]:
            if self._is_console_output():
                click.echo("  ⚠  --ai flag set but no reachable ai_agent integration configured")
            return

        # Pre-compute stats client-side (more reliable than asking the LLM to do maths)
        stats = self._compute_stats()

        # Serialise entries as plain dicts for the prompt
        entry_dicts = [e.model_dump(exclude_none=True) for e in self._entries]

        context: Dict[str, Any] = {
            "workspace": str(self._work_path),
            "filters": {k: v for k, v in {"stage": self._stage, "since": self._since}.items() if v is not None},
        }

        if self._is_console_output():
            click.echo(f"\n  🤖  AI audit summary ({integration.integration_name}) …\n")

        work_path = Path(self._work_path) if self._work_path else None
        try:
            response = integration.summarise_audit_history(entry_dicts, stats, context, work_path=work_path)
        except Exception as exc:
            self._messages.append(f"AI audit summary failed: {exc}")
            return

        self._output_data.setdefault("ai_analysis", {})["audit_history"] = {
            "provider": response.provider,
            "model": response.model,
            "content": response.content,
            "entries_analysed": len(self._entries),
        }

        if self._is_console_output():
            self._print_ai_audit_summary(response.content)

    def _compute_stats(self) -> Dict[str, Any]:
        """Pre-compute deployment statistics to include in the AI prompt."""
        if not self._entries:
            return {
                "total": 0,
                "succeeded": 0,
                "failed": 0,
                "success_rate": "0%",
                "avg_duration_s": 0.0,
                "min_duration_s": 0.0,
                "max_duration_s": 0.0,
            }

        succeeded = sum(1 for e in self._entries if e.success)
        failed = len(self._entries) - succeeded
        durations = [e.duration_seconds for e in self._entries]
        rate = f"{succeeded}/{len(self._entries)} ({100 * succeeded // len(self._entries)}%)"

        return {
            "total": len(self._entries),
            "succeeded": succeeded,
            "failed": failed,
            "success_rate": rate,
            "avg_duration_s": sum(durations) / len(durations),
            "min_duration_s": min(durations),
            "max_duration_s": max(durations),
        }

    def _print_ai_audit_summary(self, content: str) -> None:
        import json as _json

        sep = "─" * 60
        click.echo(f"  {sep}")
        try:
            parsed = _json.loads(content)
            health = parsed.get("health", "")
            health_icon = {
                "healthy": "🟢",
                "degraded": "🟡",
                "failing": "🔴",
                "mixed": "🟠",
            }.get(health.lower(), "ℹ️")

            click.echo(f"  {health_icon}  {parsed.get('summary', '')}\n")

            trends = parsed.get("trends", "")
            if trends and trends.lower() != "stable.":
                click.echo(f"  Trend: {trends}\n")

            anomalies: List[str] = parsed.get("anomalies") or []
            if anomalies:
                click.echo("  Anomalies:")
                for a in anomalies:
                    click.echo(f"    ⚠  {a}")
                click.echo("")

            failing_stages: List[str] = parsed.get("failing_stages") or []
            if failing_stages:
                click.echo(f"  Recurring failing stages: {', '.join(failing_stages)}\n")

            recommendations: List[str] = parsed.get("recommendations") or []
            if recommendations:
                click.echo("  Recommendations:")
                for i, rec in enumerate(recommendations, 1):
                    click.echo(f"    {i}. {rec}")
        except (_json.JSONDecodeError, TypeError):
            click.echo(content)
        click.echo(f"  {sep}\n")
