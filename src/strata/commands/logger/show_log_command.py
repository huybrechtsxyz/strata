"""Command to display execution logs for the current workspace."""

from typing import Any, Dict, List, Optional

import click

from strata.commands.base_command import BaseCommand

LEVEL_COLORS = {
    "ERROR": "red",
    "CRITICAL": "red",
    "WARNING": "yellow",
    "DEBUG": "cyan",
}

# Minimum level threshold to consider entries meaningful for AI analysis
_AI_LEVELS = {"WARNING", "ERROR", "CRITICAL"}


class ShowLogCommand(BaseCommand):
    """Read and display the workspace execution logs.

    Entries can be filtered by number of lines, time window, log level,
    or execution ID.  With ``--ai``, errors and warnings are summarised by
    the configured AI provider.
    """

    OPERATION = "log_show"

    def __init__(
        self,
        lines: int = 50,
        minutes: Optional[int] = None,
        level: Optional[str] = None,
        execution_id: Optional[str] = None,
        last: bool = False,
        ai: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._lines = lines
        self._minutes = minutes
        self._level = level.upper() if level else None
        self._filter_execution_id = execution_id
        self._use_last_execution = last
        self._ai = ai
        self._log_entries: List[Dict] = []

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def _before_execute(self) -> bool:
        return super()._before_execute()

    def _execute(self) -> bool:
        # Resolve --last flag: use the last execution ID stored in the solution
        if self._use_last_execution and not self._filter_execution_id:
            self._filter_execution_id = self._solution_controller.get_solution_id() or None

        ok, entries, errors = self._solution_controller.get_logs(
            work_path=self._work_path,
            lines=self._lines,
            minutes=self._minutes,
            level=self._level,
            execution_id=self._filter_execution_id,
        )
        self._errors.extend(errors)
        if not ok:
            return False

        self._log_entries = entries
        self._output_data = {
            "total_entries": len(entries),
            "filters": {
                k: v
                for k, v in {
                    "lines": self._lines,
                    "minutes": self._minutes,
                    "level": self._level,
                    "execution_id": self._filter_execution_id,
                }.items()
                if v is not None
            },
            "log_entries": entries,
        }

        if self._ai:
            self._run_ai_log_summary()

        return True

    def _after_execute(self) -> bool:
        if not self._is_quiet() and self._is_console_output():
            click.echo("")
            click.echo("  📋  Execution Logs")

            filters = self._output_data.get("filters", {})
            if filters:
                parts = []
                if "lines" in filters:
                    parts.append(f"lines={filters['lines']}")
                if "minutes" in filters:
                    parts.append(f"last {filters['minutes']}min")
                if "level" in filters:
                    parts.append(f"level={filters['level']}")
                if "execution_id" in filters:
                    parts.append(f"execution={str(filters['execution_id'])[:8]}…")
                click.echo(f"  Filters: {' | '.join(parts)}")

            click.echo("")

            if not self._log_entries:
                click.echo("  ℹ️   No log entries found.\n")
                return super()._after_execute()

            for entry in self._log_entries:
                ts = entry.get("timestamp", "")
                lvl = (entry.get("level") or entry.get("levelname") or "INFO").upper()
                msg = entry.get("event", entry.get("message", ""))
                line = f"  [{ts}]  {lvl:<8}  {msg}"
                color = LEVEL_COLORS.get(lvl)
                if color:
                    click.secho(line, fg=color)
                else:
                    click.echo(line)

            click.echo(f"\n  Total: {len(self._log_entries)} entries\n")

        return super()._after_execute()

    # ------------------------------------------------------------------
    # AI log summary
    # ------------------------------------------------------------------

    def _run_ai_log_summary(self) -> None:
        """Run AI summarisation of errors/warnings in the fetched log entries."""
        from pathlib import Path

        from strata.integrations.ai import find_ai_integration

        # Pass only warning/error/critical entries to limit token usage
        ai_entries = [
            e for e in self._log_entries if (e.get("level") or e.get("levelname") or "INFO").upper() in _AI_LEVELS
        ]

        if not ai_entries:
            if self._is_console_output():
                click.echo("\n  \u2139\ufe0f  No warnings or errors found in the selected log window.\n")
            return

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
                click.echo("  \u26a0  --ai flag set but no reachable ai_agent integration configured")
            return

        context: Dict[str, Any] = {
            "workspace": str(self._work_path),
            "filters": self._output_data.get("filters", {}),
            "total_entries": self._output_data.get("total_entries", 0),
        }

        if self._is_console_output():
            click.echo(f"\n  \U0001f916  AI log summary ({integration.integration_name}) \u2026\n")

        work_path = Path(self._work_path) if self._work_path else None
        try:
            response = integration.summarise_execution_log(ai_entries, context, work_path=work_path)
        except Exception as exc:
            self._messages.append(f"AI log summary failed: {exc}")
            return

        self._output_data.setdefault("ai_analysis", {})["log_summary"] = {
            "provider": response.provider,
            "model": response.model,
            "content": response.content,
            "entries_analysed": len(ai_entries),
        }

        if self._is_console_output():
            self._print_ai_log_summary(response.content, len(ai_entries))

    def _print_ai_log_summary(self, content: str, entries_analysed: int) -> None:
        import json as _json

        sep = "\u2500" * 60
        click.echo(f"  {sep}")
        click.echo(f"  \U0001f916  AI Log Summary  ({entries_analysed} warning/error entries)")
        click.echo(f"  {sep}")
        try:
            parsed = _json.loads(content)
            severity = parsed.get("severity", "")
            severity_icon = {
                "low": "\U0001f7e2",
                "medium": "\U0001f7e1",
                "high": "\U0001f7e0",
                "critical": "\U0001f534",
            }.get(severity.lower(), "\u2139\ufe0f")

            click.echo(f"\n  {severity_icon}  {parsed.get('summary', '')}\n")

            groups: List[Dict[str, Any]] = parsed.get("error_groups") or []
            for grp in groups:
                count = grp.get("count", "?")
                title = grp.get("title", "?")
                desc = grp.get("description", "")
                cause = grp.get("likely_cause", "")
                suggestion = grp.get("suggestion", "")
                click.echo(f"  [{count}x] {title}")
                if desc:
                    click.echo(f"        {desc}")
                if cause:
                    click.echo(f"        Likely cause: {cause}")
                if suggestion:
                    click.echo(f"        \u2192 {suggestion}")
                click.echo("")

            noise = parsed.get("noise", "")
            if noise:
                click.echo(f"  Noise: {noise}\n")

            next_steps: List[str] = parsed.get("next_steps") or []
            if next_steps:
                click.echo("  Next steps:")
                for i, step in enumerate(next_steps, 1):
                    click.echo(f"    {i}. {step}")
        except (_json.JSONDecodeError, TypeError):
            click.echo(content)
        click.echo(f"  {sep}\n")

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        if self._is_structured_output() and self._output_format == "text":
            for entry in self._log_entries:
                ts = entry.get("timestamp", "")
                lvl = (entry.get("level") or entry.get("levelname") or "INFO").upper()
                msg = entry.get("event", entry.get("message", ""))
                click.echo(f"[{ts}] {lvl:<8} {msg}")
            for err in self._errors:
                click.echo(f"error: {err}")
            # Suppress further structured rendering in base _finalize
            self._output_format = "console"
            return super()._finalize(success=success, show_footer=False)

        return super()._finalize(success=success, show_footer=show_footer)
