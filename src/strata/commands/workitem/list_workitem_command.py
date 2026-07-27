"""List work items."""

from __future__ import annotations

from typing import Optional

from rich.table import Table

from strata.commands.base_command import BaseCommand
from strata.controllers.workitem_controller import WorkItemController
from strata.logger import get_logger

logger = get_logger(__name__)

_STATUS_STYLE = {
    "pending": "yellow",
    "approved": "green",
    "rejected": "red",
    "completed": "green",
    "expired": "dim",
    "cancelled": "dim",
}


class ListWorkItemCommand(BaseCommand):
    OPERATION = "workitem_list"

    def __init__(
        self,
        type: Optional[str] = None,
        status: Optional[str] = None,
        deployment: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._type = type
        self._status = status
        self._deployment = deployment

    def get_required_integrations(self) -> dict:
        return {}

    def _initialize(self, show_header: bool = True) -> bool:
        return self._initialize_session(show_header=show_header)

    def _execute(self) -> bool:
        controller = WorkItemController.from_config(self._work_path)

        # Expire stale items before listing so statuses are current
        expired = controller.expire_stale()
        if expired:
            logger.debug("workitem.auto_expired", count=expired)

        items = controller.list_items(
            type=self._type,
            status=self._status,
            deployment=self._deployment,
        )

        if self._is_console_output():
            self._render_table(items)
        else:
            self._output_data = [item.to_dict() for item in items]

        return True

    def _render_table(self, items: list) -> None:
        from rich.console import Console

        console = Console()

        if not items:
            filter_parts = []
            if self._type:
                filter_parts.append(f"type={self._type}")
            if self._status:
                filter_parts.append(f"status={self._status}")
            filter_str = f" ({', '.join(filter_parts)})" if filter_parts else ""
            console.print(f"[dim]No work items found{filter_str}.[/dim]")
            return

        table = Table(show_header=True, header_style="bold")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Type")
        table.add_column("Status")
        table.add_column("Deployment")
        table.add_column("Created By")
        table.add_column("Created At")
        table.add_column("Expires At")

        for item in items:
            style = _STATUS_STYLE.get(item.status, "")
            expires = (item.expires_at or "—")[:19].replace("T", " ")
            created = (item.created_at or "—")[:19].replace("T", " ")
            table.add_row(
                item.short_id,
                item.type,
                f"[{style}]{item.status}[/{style}]" if style else item.status,
                item.deployment,
                item.created_by,
                created,
                expires,
            )

        console.print(table)
        console.print(f"\n[dim]{len(items)} work item(s)[/dim]")
