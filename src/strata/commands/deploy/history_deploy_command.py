"""Command to show deployment execution history from workspace logs."""

from datetime import datetime
from typing import Any, Dict, List, Optional

import click

from strata.commands.base_command import BaseCommand

# Deploy operations included in the history view
_DEPLOY_OPERATIONS = {"deploy_run", "deploy_destroy"}

# Human-friendly labels
_OPERATION_LABELS: Dict[str, str] = {
    "deploy_run": "deploy run",
    "deploy_destroy": "deploy destroy",
}


class HistoryDeployCommand(BaseCommand):
    """Show deployment execution history from workspace logs.

    Scans ``.strata/logs/`` for ``deploy_run`` and ``deploy_destroy``
    events, groups them by execution ID, and renders a table::

        WHEN                OPERATION             RESULT
        -------------------------------------------------------
        2026-05-06 14:32    deploy run            ✅ success
        2026-05-05 09:14    deploy destroy        ❌ failed

    Does **not** require a deployment YAML file — it reads the workspace
    log files only.

    Flags:
    - ``--lines N``    : max history entries to display (default 50)
    - ``--operation``  : filter by operation (``run`` or ``destroy``)
    - ``--verbose``    : show execution IDs
    """

    OPERATION = "deploy_history"

    def __init__(
        self,
        work_path: Optional[str] = None,
        lines: int = 50,
        operation: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ):
        super().__init__(
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self._lines = lines
        self._operation_filter = _resolve_operation_filter(operation)

    def get_required_integrations(self):
        return {}

    # -------------------------------------------------------------------------
    # Core
    # -------------------------------------------------------------------------

    def _execute(self) -> bool:
        if self._is_console_output():
            click.echo("\n📜  Deploy history (from workspace logs)…\n")

        ok, entries, errors = self._solution_controller.get_logs(
            work_path=self._work_path,
            lines=self._lines * 20,  # over-fetch; filtered below
        )
        self._errors.extend(errors)
        if not ok:
            return False

        # Filter to deploy events
        deploy_events = [
            e
            for e in entries
            if e.get("command") in _DEPLOY_OPERATIONS
            or e.get("operation") in _DEPLOY_OPERATIONS
            or (e.get("event", "").startswith("Command execution") and e.get("command", "") in _DEPLOY_OPERATIONS)
        ]

        # Apply operation filter if requested
        if self._operation_filter:
            deploy_events = [
                e
                for e in deploy_events
                if e.get("command") == self._operation_filter or e.get("operation") == self._operation_filter
            ]

        # Group by execution_id → keep last entry per execution (has success flag)
        by_exec: Dict[str, Dict[str, Any]] = {}
        for e in deploy_events:
            eid = e.get("execution_id", "")
            if eid:
                by_exec[eid] = e

        history: List[Dict[str, Any]] = []
        for eid, entry in by_exec.items():
            ts_raw = entry.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                ts = ts_raw[:16] if ts_raw else "?"

            op_key = entry.get("command", entry.get("operation", "?"))
            history.append(
                {
                    "when": ts,
                    "operation": _OPERATION_LABELS.get(op_key, op_key),
                    "operation_key": op_key,
                    "execution_id": eid,
                    "success": entry.get("success"),
                    "file": entry.get("file", ""),
                    "stage": entry.get("stage", ""),
                }
            )

        history.sort(key=lambda r: r["when"], reverse=True)
        history = history[: self._lines]

        self._output_data = {
            "mode": "history",
            "total": len(history),
            "filter": self._operation_filter,
            "entries": history,
        }

        if self._is_console_output():
            self._print_table(history)

        return True

    def _print_table(self, history: List[Dict[str, Any]]) -> None:
        if not history:
            click.echo("  (no deploy history found in workspace logs)")
            click.echo("  Tip: logs live in .strata/logs/ — older runs may have been cleaned.")
        else:
            click.echo(f"  {'WHEN':<18}  {'OPERATION':<20}  RESULT")
            click.echo("  " + "-" * 58)
            for row in history:
                success = row["success"]
                result = "✅ success" if success is True else "❌ failed" if success is False else "  —"
                extra_parts = []
                if row.get("file"):
                    extra_parts.append(row["file"])
                if row.get("stage"):
                    extra_parts.append(f"stage:{row['stage']}")
                click.echo(f"  {row['when']:<18}  {row['operation']:<20}  {result}")
                if self._is_verbose():
                    if extra_parts:
                        click.echo(f"  {'':18}  {' | '.join(extra_parts)}")
                    click.echo(f"  {'':18}  id: {row['execution_id']}")
        click.echo()

    def _is_console_output(self) -> bool:
        return self._output_format == "console"

    def _is_verbose(self) -> bool:
        return self._output_verbose


def _resolve_operation_filter(value: Optional[str]) -> Optional[str]:
    """Map a user-friendly filter value (run/destroy) to an internal operation key."""
    if value is None:
        return None
    mapping = {
        "run": "deploy_run",
        "deploy_run": "deploy_run",
        "destroy": "deploy_destroy",
        "deploy_destroy": "deploy_destroy",
    }
    return mapping.get(value.lower())
