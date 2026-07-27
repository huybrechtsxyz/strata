"""Show a single work item in detail."""

from __future__ import annotations

from typing import Optional

from strata.commands.base_command import BaseCommand
from strata.controllers.workitem_controller import WorkItemController
from strata.logger import get_logger

logger = get_logger(__name__)


class ShowWorkItemCommand(BaseCommand):
    OPERATION = "workitem_show"

    def __init__(
        self,
        item_id: str,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._item_id = item_id

    def get_required_integrations(self) -> dict:
        return {}

    def _initialize(self, show_header: bool = True) -> bool:
        return self._initialize_session(show_header=show_header)

    def _execute(self) -> bool:
        controller = WorkItemController.from_config(self._work_path)
        item = controller.get(self._item_id)

        if item is None:
            self._errors.append(f"Work item not found: {self._item_id!r}")
            return False

        if self._is_console_output():
            self._render_detail(item)
        else:
            self._output_data = item.to_dict()

        return True

    def _render_detail(self, item) -> None:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table

        console = Console()

        # Summary table
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("field", style="bold", width=18)
        table.add_column("value")

        status_styles = {
            "pending": "[yellow]pending[/yellow]",
            "approved": "[green]approved[/green]",
            "rejected": "[red]rejected[/red]",
            "completed": "[green]completed[/green]",
            "expired": "[dim]expired[/dim]",
            "cancelled": "[dim]cancelled[/dim]",
        }

        table.add_row("ID", item.id)
        table.add_row("Type", item.type)
        table.add_row("Status", status_styles.get(item.status, item.status))
        table.add_row("Deployment", item.deployment)
        table.add_row("Commit", item.commit[:16] if item.commit else "—")
        table.add_row("Created by", item.created_by)
        table.add_row("Created at", item.created_at[:19].replace("T", " ") if item.created_at else "—")
        table.add_row("Expires at", item.expires_at[:19].replace("T", " ") if item.expires_at else "—")

        if item.resolved_by:
            table.add_row("Resolved by", item.resolved_by)
            table.add_row("Resolved at", item.resolved_at[:19].replace("T", " ") if item.resolved_at else "—")
        if item.resolution_note:
            table.add_row("Note", item.resolution_note)

        console.print(Panel(table, title=f"Work Item: {item.short_id}", border_style="blue"))

        # Context (only in verbose or if non-empty and console mode)
        if item.context:
            console.print("\n[bold]Context:[/bold]")
            import json

            console.print(f"[dim]{json.dumps(item.context, indent=2)}[/dim]")
