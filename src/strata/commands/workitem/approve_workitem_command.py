"""Approve a pending work item."""

from __future__ import annotations

from typing import Optional

from strata.commands.base_command import BaseCommand
from strata.controllers.workitem_controller import WorkItemController
from strata.integrations.workitem.base_workitem_backend import WorkItemError
from strata.logger import get_logger

logger = get_logger(__name__)


class ApproveWorkItemCommand(BaseCommand):
    OPERATION = "workitem_approve"

    def __init__(
        self,
        item_id: str,
        note: Optional[str] = None,
        as_identity: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._item_id = item_id
        self._note = note
        self._as_identity = as_identity

    def get_required_integrations(self) -> dict:
        return {}

    def _initialize(self, show_header: bool = True) -> bool:
        return self._initialize_session(show_header=show_header)

    def _execute(self) -> bool:
        from strata.controllers.workitem_controller import _get_identity

        controller = WorkItemController.from_config(self._work_path)
        resolver = self._as_identity or _get_identity()
        # Tag asserted identity in audit trail per ADR-0057 §11
        if self._as_identity:
            resolver = f"{self._as_identity} [asserted]"

        try:
            item = controller.resolve(self._item_id, "approved", resolver=resolver, note=self._note)
        except WorkItemError as exc:
            self._errors.append(str(exc))
            return False

        if self._is_console_output():
            from rich.console import Console

            console = Console()
            console.print(f"[green]✅ Approved:[/green] {item.id}")
            console.print(f"   Resolved by: {item.resolved_by}")
            if item.resolution_note:
                console.print(f"   Note: {item.resolution_note}")
            console.print()
            console.print(f"[dim]Resume deploy with: strata deploy run -f {item.deployment} --resume {item.id}[/dim]")
        else:
            self._output_data = item.to_dict()

        return True
