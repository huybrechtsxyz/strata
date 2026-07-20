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
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._last = last
        self._since = since
        self._stage = stage
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
