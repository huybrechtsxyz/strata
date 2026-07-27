"""Expire stale work items whose timeout has passed."""

from __future__ import annotations

from typing import Optional

from strata.commands.base_command import BaseCommand
from strata.controllers.workitem_controller import WorkItemController
from strata.logger import get_logger

logger = get_logger(__name__)


class ExpireWorkItemCommand(BaseCommand):
    OPERATION = "workitem_expire"

    def __init__(
        self,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)

    def get_required_integrations(self) -> dict:
        return {}

    def _initialize(self, show_header: bool = True) -> bool:
        return self._initialize_session(show_header=show_header)

    def _execute(self) -> bool:
        controller = WorkItemController.local(self._work_path)
        count = controller.expire_stale()

        if self._is_console_output():
            from rich.console import Console

            Console().print(
                f"[green]✅ Expired {count} stale work item(s).[/green]"
                if count
                else "[dim]No stale work items found.[/dim]"
            )
        else:
            self._output_data = {"expired_count": count}

        return True
