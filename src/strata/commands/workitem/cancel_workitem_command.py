"""Cancel a pending work item."""

from __future__ import annotations

from typing import Optional

from strata.commands.base_command import BaseCommand
from strata.controllers.workitem_controller import WorkItemController
from strata.integrations.workitem.base_workitem_backend import WorkItemError
from strata.logger import get_logger

logger = get_logger(__name__)


class CancelWorkItemCommand(BaseCommand):
    OPERATION = "workitem_cancel"

    def __init__(
        self,
        item_id: str,
        reason: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._item_id = item_id
        self._reason = reason

    def get_required_integrations(self) -> dict:
        return {}

    def _initialize(self, show_header: bool = True) -> bool:
        return self._initialize_session(show_header=show_header)

    def _execute(self) -> bool:
        controller = WorkItemController.local(self._work_path)

        try:
            item = controller.cancel(self._item_id, reason=self._reason)
        except WorkItemError as exc:
            self._errors.append(str(exc))
            return False

        if self._is_console_output():
            from rich.console import Console

            console = Console()
            console.print(f"[dim]Cancelled:[/dim] {item.id}")
            if item.resolution_note:
                console.print(f"   Reason: {item.resolution_note}")
        else:
            self._output_data = item.to_dict()

        return True
