"""Command to display workspace setup progress and suggest next actions."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.controllers.guide_controller import ChecklistItem, GuideController, NextStepItem

_PLACEHOLDER_RE = re.compile(r"<[a-z][a-z_]*>")


class GuideCommand(BaseCommand):
    """Show setup progress and suggest the next action for this workspace."""

    OPERATION = "guide"

    def __init__(
        self,
        file: Optional[str] = None,
        next_step: bool = False,
        do_step: bool = False,
        ai: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._file = file
        self._next = next_step
        self._do = do_step
        self._ai = ai
        self._do_failed: bool = False
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
        return not self._do_failed

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

        if self._do:
            self._run_do_mode(next_step, ctrl)
            return

        if self._next:
            self._run_next_mode(checklist, next_step, ctrl)
            return

        if self._is_quiet():
            return

        if self._is_console_output():
            self._render_console(checklist, next_step, ctrl.workspace_name, ctrl.hints)
        else:
            self._output_data = self._render_json(
                checklist, next_step, ctrl.workspace_name, ctrl.solution_id, ctrl.is_complete
            )

        # AI guidance: explain what's blocking and suggest next action
        if self._ai and not ctrl.is_complete:
            blocking = [
                {"status": item.status, "label": item.label, "detail": item.detail}
                for item in checklist
                if item.status in ("warn", "pending")
            ]
            if blocking and next_step:
                self._run_ai_guide_assistance(
                    phase=next_step.phase,
                    phase_label=next_step.label,
                    blocking_items=blocking,
                    workspace_name=ctrl.workspace_name or "",
                )

    # ------------------------------------------------------------------
    # AI guide assistance
    # ------------------------------------------------------------------

    def _run_ai_guide_assistance(
        self,
        phase: int,
        phase_label: str,
        blocking_items: List[Dict[str, Any]],
        workspace_name: str,
    ) -> None:
        from strata.integrations.ai import find_ai_integration

        # Guide has no configuration service — try to load one
        config_svc = None
        try:
            from strata.controllers.solution_controller import SolutionController
            from strata.services.configuration_service import ConfigurationService

            sol = SolutionController(work_path=self._work_path)
            sol.load()
            profile, _ = sol.get_active_profile()
            if profile:
                config_paths = [str(p.path) for p in (profile.configfile_paths or [])]
                if config_paths:
                    config_svc = ConfigurationService(config_paths)
                    config_svc.load()
        except Exception:
            pass

        integration = find_ai_integration(config_svc)
        if integration is None or not integration.ensure_available()[0]:
            if self._is_console_output():
                click.echo("  ⚠  --ai flag set but no reachable ai_agent integration configured")
            return

        context = {"workspace": workspace_name, "work_path": str(self._work_path)}

        if self._is_console_output():
            click.echo(f"\n  🤖  AI guide assistance ({integration.integration_name}) …")

        try:
            response = integration.assist_guide(phase, phase_label, blocking_items, context)
        except Exception as exc:
            self._messages.append(f"AI guide assistance failed: {exc}")
            return

        if not isinstance(self._output_data, dict):
            self._output_data = {}
        self._output_data["ai_analysis"] = {
            "provider": response.provider,
            "model": response.model,
            "content": response.content,
        }

        if self._is_console_output():
            self._print_ai_guide(response.content)

    def _print_ai_guide(self, content: str) -> None:
        import json as _json

        click.echo(f"\n  {'─' * 48}")
        click.echo("  🤖  AI Guide Assistance")
        click.echo(f"  {'─' * 48}")
        try:
            parsed = _json.loads(content)
            click.echo(f"\n  {parsed.get('summary', '')}")
            if parsed.get("root_cause"):
                click.echo(f"  Root cause: {parsed['root_cause']}")
            if parsed.get("next_action"):
                click.echo(f"\n  Next action: {parsed['next_action']}")
            if parsed.get("steps"):
                click.echo("  Steps:")
                for i, step in enumerate(parsed["steps"], 1):
                    click.echo(f"    {i}. {step}")
            if parsed.get("hint"):
                click.echo(f"\n  💡 {parsed['hint']}")
        except (_json.JSONDecodeError, TypeError):
            click.echo(content)
        click.echo("")

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
    # Do-step mode (--do)
    # ------------------------------------------------------------------

    def _run_do_mode(self, next_step: Optional[NextStepItem], ctrl: GuideController) -> None:
        if next_step is None:
            # Workspace complete — same output as --next complete
            if not self._is_quiet():
                if self._is_console_output():
                    self._render_next_console(None, ctrl.hints)
                else:
                    self._output_data = self._render_do_json(None, [], False, None)
            return

        unresolved = _PLACEHOLDER_RE.findall(next_step.hint)
        unresolved_names = [p[1:-1] for p in unresolved]  # strip angle brackets
        executable = len(unresolved_names) == 0

        if self._is_quiet():
            if executable:
                returncode = self._execute_hint(next_step.hint)
                if returncode != 0:
                    self._do_failed = True
            return

        if executable:
            if self._is_console_output():
                click.echo(f"\n\u2192 Running: {next_step.hint}\n")
                returncode = self._execute_hint(next_step.hint)
                if returncode != 0:
                    self._do_failed = True
                    click.echo(f"\n\u26a0\ufe0f  Command exited with code {returncode}")
                click.echo("")
            else:
                returncode = self._execute_hint(next_step.hint)
                if returncode != 0:
                    self._do_failed = True
                self._output_data = self._render_do_json(next_step, [], True, returncode)
        else:
            if self._is_console_output():
                self._render_do_blocked_console(next_step, unresolved_names)
            else:
                self._output_data = self._render_do_json(next_step, unresolved_names, False, None)

    @staticmethod
    def _find_unresolved(hint: str) -> List[str]:
        """Return bare placeholder names (without angle brackets) still in hint."""
        return [m.group(0)[1:-1] for m in _PLACEHOLDER_RE.finditer(hint)]

    def _execute_hint(self, hint: str) -> int:
        """Run hint as a shell command in the workspace directory. Returns exit code."""
        try:
            result = subprocess.run(hint, shell=True, cwd=str(self._work_path))  # noqa: S602
            return result.returncode
        except Exception as e:
            click.echo(f"\n\u26a0\ufe0f  Failed to execute: {e}")
            return 1

    def _render_do_blocked_console(self, next_step: NextStepItem, unresolved: List[str]) -> None:
        placeholder_str = ", ".join(f"<{p}>" for p in unresolved)
        click.echo(f"\n\u2192 Phase {next_step.phase}: {next_step.label} \u2014 values required\n")
        for line in next_step.hint.splitlines():
            click.echo(f"   {line}")
        click.echo("")
        click.echo(f"   Fill in {placeholder_str}, then re-run with --do.")
        if next_step.see_also:
            click.echo(f"   See: {next_step.see_also}")
        click.echo("")

    def _render_do_json(
        self,
        next_step: Optional[NextStepItem],
        unresolved: List[str],
        executed: bool,
        exit_code: Optional[int],
    ) -> dict:
        if next_step is None:
            return {
                "complete": True,
                "phase": None,
                "label": None,
                "command": None,
                "executable": None,
                "unresolved": [],
                "executed": False,
                "exit_code": None,
                "see_also": None,
            }
        return {
            "complete": False,
            "phase": next_step.phase,
            "label": next_step.label,
            "command": next_step.hint,
            "executable": len(unresolved) == 0,
            "unresolved": unresolved,
            "executed": executed,
            "exit_code": exit_code,
            "see_also": next_step.see_also,
        }

    # ------------------------------------------------------------------
    # Next-step mode (--next)
    # ------------------------------------------------------------------

    def _run_next_mode(
        self,
        checklist: List[ChecklistItem],
        next_step: Optional[NextStepItem],
        ctrl: GuideController,
    ) -> None:
        if self._is_quiet():
            return
        if self._is_console_output():
            self._render_next_console(next_step, ctrl.hints)
        else:
            self._output_data = self._render_next_json(
                next_step, ctrl.workspace_name, ctrl.solution_id, ctrl.is_complete
            )

    def _render_next_console(self, next_step: Optional[NextStepItem], hints: dict) -> None:
        if next_step is None:
            complete_msg = hints.get("complete") or "All setup phases complete. Your workspace is ready to deploy."
            click.echo(f"→ {complete_msg}")
            click.echo("")
            return
        click.echo(f"\n→ Phase {next_step.phase}: {next_step.label}\n")
        for line in next_step.hint.splitlines():
            click.echo(f"   {line}")
        if next_step.see_also:
            click.echo("")
            click.echo(f"   See: {next_step.see_also}")
        click.echo("")

    def _render_next_json(
        self,
        next_step: Optional[NextStepItem],
        workspace_name: Optional[str],
        solution_id: Optional[str],
        is_complete: bool,
    ) -> dict:
        if next_step is None:
            return {
                "complete": True,
                "phase": None,
                "label": None,
                "command": None,
                "see_also": None,
            }
        return {
            "complete": False,
            "phase": next_step.phase,
            "label": next_step.label,
            "command": next_step.hint,
            "see_also": next_step.see_also,
        }

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
