"""Command to display execution logs for the current workspace."""

from typing import Dict, List, Optional

import click

from strata.commands.base_command import BaseCommand

LEVEL_COLORS = {
    "ERROR": "red",
    "CRITICAL": "red",
    "WARNING": "yellow",
    "DEBUG": "cyan",
}


class ShowLogCommand(BaseCommand):
    """Read and display the workspace execution logs.

    Entries can be filtered by number of lines, time window, log level,
    or execution ID.
    """

    OPERATION = "log_show"

    def __init__(
        self,
        lines: int = 50,
        minutes: Optional[int] = None,
        level: Optional[str] = None,
        execution_id: Optional[str] = None,
        last: bool = False,
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

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        # Override text output: plain lines suitable for piping
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
