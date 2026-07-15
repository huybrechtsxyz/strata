"""Command to display workspace setup progress and suggest next actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.controllers.guide_controller import ChecklistItem, GuideController, NextStepItem


class GuideCommand(BaseCommand):
    """Show setup progress and suggest the next action for this workspace."""

    OPERATION = "guide"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._file = file
        self._guide_controller: Optional[GuideController] = None

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def _initialize(self, show_header: bool = True) -> bool:
        # The guide is designed to work even before the workspace is initialized.
        # Run the parent init for side-effects (logging, execution-id) but always
        # succeed so _before_execute / _execute are never gated.
        return self._initialize_session(show_header=show_header)

    def _execute(self) -> bool:
        self._run_execution()
        return True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _run_execution(self) -> bool:
        try:
            self._guide_controller = GuideController(self._work_path)
            self._guide_controller.load()

            if self._file is not None:
                self._run_file_mode()
            else:
                self._run_workspace_mode()
            return True
        except Exception as e:
            self.logger.warning("Guide execution error", error=str(e))
            if self._is_console_output():
                click.echo(f"\n⚠️  Could not complete guide analysis: {e}")
            return True

    # ------------------------------------------------------------------
    # Workspace mode
    # ------------------------------------------------------------------

    def _run_workspace_mode(self) -> None:
        ctrl = self._guide_controller
        assert ctrl is not None
        checklist = ctrl.evaluate()
        next_step = ctrl.find_next_step()

        if self._is_quiet():
            return

        if self._is_console_output():
            self._render_console(checklist, next_step, ctrl.workspace_name, ctrl.hints)
        else:
            self._output_data = self._render_json(
                checklist, next_step, ctrl.workspace_name, ctrl.solution_id, ctrl.is_complete
            )

    # ------------------------------------------------------------------
    # File mode
    # ------------------------------------------------------------------

    def _run_file_mode(self) -> None:
        ctrl = self._guide_controller
        assert ctrl is not None

        try:
            resolved_path = ctrl.resolve_file_path(self._file)  # type: ignore[arg-type]
        except ValueError as e:
            if not self._is_quiet() and self._is_console_output():
                click.echo(f"\n⚠️  {e}")
            return

        checklist, detected_kind, detected_name = ctrl.evaluate_file(resolved_path)
        next_steps = ctrl.find_file_next_steps(detected_kind, resolved_path)

        if self._is_quiet():
            return

        if self._is_console_output():
            self._render_file_console(checklist, next_steps, resolved_path, detected_kind, ctrl.workspace_name)
        else:
            self._output_data = self._render_file_json(
                checklist,
                next_steps,
                resolved_path,
                detected_kind,
                detected_name,
                ctrl.workspace_name,
                ctrl.solution_id,
            )

    # ------------------------------------------------------------------
    # Console rendering — workspace mode
    # ------------------------------------------------------------------

    def _render_console(
        self,
        checklist: List[ChecklistItem],
        next_step: Optional[NextStepItem],
        workspace_name: Optional[str],
        hints: dict,
    ) -> None:
        if workspace_name:
            click.echo(f"\nWorkspace: {workspace_name}  ({self._work_path})")
        else:
            click.echo(f"\nWorkspace: (uninitialized)  ({self._work_path})")

        phase1 = checklist[0]
        # When workspace not initialized, show only phase 1 + next step (reduce noise)
        if phase1.status == "pending":
            click.echo("\nSetup progress:\n")
            click.echo(f"  ⬜ {phase1.label}")
            click.echo("")
            if next_step:
                self._render_next_step_console(next_step)
            return

        click.echo("\nSetup progress:\n")
        for item in checklist:
            suffix = f" ({item.detail})" if item.detail else ""
            if item.status == "ok":
                click.echo(f"  ✅ {item.label}{suffix}")
            elif item.status == "warn":
                click.echo(f"  ⚠️  {item.label}{suffix}")
            else:
                click.echo(f"  ⬜ {item.label}{suffix}")
        click.echo("")

        if next_step:
            self._render_next_step_console(next_step)
        else:
            complete_msg = hints.get("complete") or "All setup phases complete. Your workspace is ready to deploy."
            click.echo(f"→ {complete_msg}")
            click.echo("")

    def _render_next_step_console(self, next_step: NextStepItem) -> None:
        click.echo("→ Next step:")
        click.echo("")
        for line in next_step.hint.splitlines():
            click.echo(f"   {line}")
        if next_step.see_also:
            click.echo("")
            click.echo(f"   See: {next_step.see_also}")
        click.echo("")

    # ------------------------------------------------------------------
    # JSON rendering — workspace mode
    # ------------------------------------------------------------------

    def _render_json(
        self,
        checklist: List[ChecklistItem],
        next_step: Optional[NextStepItem],
        workspace_name: Optional[str],
        solution_id: Optional[str],
        complete: bool,
    ) -> dict:
        next_steps: List[Dict[str, Any]] = []
        if next_step:
            next_steps.append(
                {
                    "phase": next_step.phase,
                    "label": next_step.label,
                    "hint": next_step.hint,
                    "see_also": next_step.see_also,
                }
            )
        return {
            "workspace": {
                "name": workspace_name,
                "path": str(self._work_path),
                "solution_id": solution_id or None,
            },
            "checklist": [
                {
                    "phase": item.phase,
                    "label": item.label,
                    "status": item.status,
                    "detail": item.detail,
                }
                for item in checklist
            ],
            "next_steps": next_steps,
            "complete": complete,
        }

    # ------------------------------------------------------------------
    # File mode: console rendering
    # ------------------------------------------------------------------

    def _render_file_console(
        self,
        checklist: List[ChecklistItem],
        next_steps: List[NextStepItem],
        file_path: Path,
        kind: Optional[str],
        workspace_name: Optional[str],
    ) -> None:
        kind_str = f"kind: {kind}" if kind else "kind: unknown"
        click.echo(f"\nFile: {file_path}  ({kind_str})")
        if workspace_name:
            click.echo(f"Workspace: {workspace_name}  ({self._work_path})")

        click.echo("\nFile structure:\n")
        for item in checklist:
            if item.status == "ok":
                # For kind/apiVersion/name ok cases, render as "Key: value"
                display = self._format_file_item_ok(item)
                click.echo(f"  ✅ {display}")
            elif item.status == "warn":
                if item.detail:
                    click.echo(f"  ⚠️  {item.label}: {item.detail}")
                else:
                    click.echo(f"  ⚠️  {item.label}")
            else:
                if item.detail:
                    click.echo(f"  ⬜ {item.label} — {item.detail}")
                else:
                    click.echo(f"  ⬜ {item.label}")

        click.echo("")
        for ns in next_steps:
            click.echo(f"→ {ns.label}:")
            click.echo("")
            for line in ns.hint.splitlines():
                click.echo(f"   {line}")
            if ns.see_also:
                click.echo("")
                click.echo(f"   See: {ns.see_also}")
            click.echo("")

    @staticmethod
    def _format_file_item_ok(item: ChecklistItem) -> str:
        """Format an ok file checklist item for console (shows 'Key: value' for value phases)."""
        key_map = {
            2: "Kind",
            3: "apiVersion",
            4: "Name",
        }
        prefix = key_map.get(item.phase)
        if prefix and item.detail:
            return f"{prefix}: {item.detail}"
        return item.label

    # ------------------------------------------------------------------
    # File mode: JSON rendering
    # ------------------------------------------------------------------

    def _render_file_json(
        self,
        checklist: List[ChecklistItem],
        next_steps: List[NextStepItem],
        file_path: Path,
        kind: Optional[str],
        name: Optional[str],
        workspace_name: Optional[str],
        solution_id: Optional[str],
    ) -> dict:
        return {
            "file": {
                "path": str(file_path),
                "kind": kind,
                "name": name,
            },
            "workspace": {
                "name": workspace_name,
                "path": str(self._work_path),
                "solution_id": solution_id or None,
            },
            "checklist": [
                {
                    "phase": item.phase,
                    "label": item.label,
                    "status": item.status,
                    "detail": item.detail,
                }
                for item in checklist
            ],
            "next_steps": [
                {
                    "action": ns.label.lower(),
                    "hint": ns.hint,
                    "see_also": ns.see_also,
                }
                for ns in next_steps
            ],
        }
