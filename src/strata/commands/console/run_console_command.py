"""Interactive workspace console with guided onboarding."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

import click
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import NestedCompleter, PathCompleter, WordCompleter
from prompt_toolkit.history import FileHistory, InMemoryHistory
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from strata.commands.base_command import BaseCommand
from strata.controllers.guide_controller import ChecklistItem, GuideController
from strata.logger import get_logger

logger = get_logger(__name__)


class ConsoleCommand(BaseCommand):
    """Interactive workspace session with guided onboarding."""

    OPERATION = "console"

    def __init__(
        self,
        work_path: Optional[str] = None,
        no_color: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output="console", verbose=False, quiet=False)
        self._no_color = no_color
        self._console = Console(no_color=no_color)
        self._guide_controller: Optional[GuideController] = None
        self._session: Optional[PromptSession[str]] = None

    def get_required_integrations(self) -> dict:
        return {}

    def _initialize(self, show_header: bool = True) -> bool:
        ok = self._initialize_session(show_header=show_header)
        if not ok:
            self._console.print("[red]❌  Initialization failed[/red]")
        return True  # console works without initialized workspace

    def _execute(self) -> bool:
        self._guide_controller = GuideController(self._work_path)
        self._guide_controller.load()
        self._guide_controller.evaluate_from_workflow()

        self._session = self._create_prompt_session()

        self._render_header()
        self._render_status()
        self._render_next()
        self._console.print()

        self._repl_loop()
        return True

    # ------------------------------------------------------------------
    # REPL loop
    # ------------------------------------------------------------------

    def _repl_loop(self) -> None:
        assert self._guide_controller is not None

        while True:
            try:
                if self._session is not None:
                    user_input = self._session.prompt("strata> ").strip()
                else:
                    # Fallback for non-TTY (testing, piped input)
                    raw = input("strata> ")
                    user_input = raw.strip()
            except (KeyboardInterrupt, EOFError):
                self._console.print("\nBye!")
                return

            if not user_input:
                continue

            parts = user_input.split(maxsplit=1)
            cmd = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            if cmd in ("quit", "q", "exit"):
                self._console.print("Bye!")
                return
            elif cmd in ("status", "s"):
                self._handle_status()
            elif cmd in ("next", "n"):
                self._handle_next()
            elif cmd in ("do", "d"):
                self._handle_do()
            elif cmd in ("check", "c"):
                self._handle_check(args)
            elif cmd == "new":
                self._handle_shell_command(f"strata new {args}")
            elif cmd in ("validate", "v"):
                self._handle_shell_command(f"strata validate {args}" if args else "strata validate run")
            elif cmd in ("diagram", "g"):
                self._handle_shell_command(
                    f"strata diagram show {args}".strip() if args else "strata diagram show -f topology"
                )
            elif cmd in ("templates", "t"):
                self._handle_shell_command("strata new --list")
            elif cmd == "tools":
                self._handle_shell_command("strata tools status")
            elif cmd in ("open", "o"):
                self._handle_open(args)
            elif cmd in ("help", "?"):
                self._render_help()
            elif cmd == "clear":
                self._handle_clear()
            elif cmd == "reload":
                self._handle_reload()
            else:
                self._console.print(f"[yellow]Unknown command '{cmd}'. Type '?' for help.[/yellow]")

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def _handle_status(self) -> None:
        assert self._guide_controller is not None
        self._guide_controller.reload()
        self._guide_controller.evaluate_from_workflow()
        self._render_status()

    def _handle_next(self) -> None:
        assert self._guide_controller is not None
        self._guide_controller.reload()
        self._guide_controller.evaluate_from_workflow()
        self._render_next()

    def _handle_do(self) -> None:
        assert self._guide_controller is not None
        self._guide_controller.reload()
        self._guide_controller.evaluate_from_workflow()
        next_step = self._guide_controller.find_next_step_from_workflow()

        if next_step is None:
            self._console.print("[green]✅ All steps complete. Your workspace is ready.[/green]")
            return

        # Prefer the concrete command from the workflow; fall back to first line of hint
        command = next_step.command or next_step.hint.splitlines()[0].strip()
        if not command or command.startswith("#"):
            self._console.print(f"[yellow]→ Manual step required:[/yellow] {next_step.hint}")
            return

        self._handle_shell_command(command)

    def _handle_check(self, file_arg: str) -> None:
        assert self._guide_controller is not None
        if not file_arg:
            self._console.print("[yellow]Usage: check <file>[/yellow]")
            return

        try:
            resolved_path = self._guide_controller.resolve_file_path(file_arg)
        except ValueError as e:
            self._console.print(f"[red]⚠️  {e}[/red]")
            return

        checklist, detected_kind, _ = self._guide_controller.evaluate_file(resolved_path)
        next_steps = self._guide_controller.find_file_next_steps(detected_kind, resolved_path)

        self._render_file_checklist(checklist, resolved_path, detected_kind)
        for ns in next_steps:
            self._console.print(f"  → {ns.label}: {ns.hint}")
        self._console.print()

    def _handle_shell_command(self, command: str) -> None:
        """Execute a strata CLI command as a subprocess and show output."""
        self._console.print(f"[dim]$ {command}[/dim]")
        self._console.print()

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(self._work_path),
                capture_output=False,
                text=True,
            )
            self._console.print()
            if result.returncode != 0:
                self._console.print(f"[yellow]Exit code: {result.returncode}[/yellow]")
        except Exception as e:
            self._console.print(f"[red]Failed to execute: {e}[/red]")

        # Auto-refresh after state-changing commands
        self._auto_refresh()

    def _handle_open(self, file_arg: str) -> None:
        if not file_arg:
            self._console.print("[yellow]Usage: open <file>[/yellow]")
            return

        try:
            resolved = self._guide_controller.resolve_file_path(file_arg) if self._guide_controller else Path(file_arg)
        except ValueError:
            resolved = Path(file_arg)

        if not resolved.exists():
            self._console.print(f"[red]File not found: {resolved}[/red]")
            return

        try:
            click.launch(str(resolved))
            self._console.print(f"[dim]Opened {resolved}[/dim]")
        except Exception as e:
            self._console.print(f"[red]Could not open file: {e}[/red]")

    def _handle_clear(self) -> None:
        os.system("cls" if os.name == "nt" else "clear")

    def _handle_reload(self) -> None:
        assert self._guide_controller is not None
        self._guide_controller.reload()
        self._guide_controller.evaluate_from_workflow()
        self._console.print("[green]✅ Workspace state reloaded.[/green]")
        self._render_status()

    # ------------------------------------------------------------------
    # Auto-refresh
    # ------------------------------------------------------------------

    def _auto_refresh(self) -> None:
        """Re-evaluate checklist after a state-changing command and show delta."""
        assert self._guide_controller is not None
        old_checklist = list(self._guide_controller.checklist)
        self._guide_controller.reload()
        new_checklist = self._guide_controller.evaluate_from_workflow()

        # Show delta
        for old, new in zip(old_checklist, new_checklist, strict=False):
            if old.status != new.status:
                old_icon = _status_icon(old.status)
                new_icon = _status_icon(new.status)
                self._console.print(
                    f"  [dim][auto-refresh][/dim] Phase {new.phase}: {old_icon} → {new_icon} {new.label}"
                )

    # ------------------------------------------------------------------
    # Rich rendering
    # ------------------------------------------------------------------

    def _render_header(self) -> None:
        ctrl = self._guide_controller
        assert ctrl is not None

        ws_name = ctrl.workspace_name or "(uninitialized)"
        ok_count = sum(1 for item in ctrl.checklist if item.status == "ok")
        total = len(ctrl.checklist)

        progress_bar = _progress_bar(ok_count, total)
        header_text = (
            f"Workspace: [bold]{ws_name}[/bold]  ({self._work_path})\n{progress_bar} {ok_count}/{total} phases complete"
        )

        self._console.print()
        self._console.print(Panel(header_text, title="strata console", border_style="blue"))

    def _render_status(self) -> None:
        ctrl = self._guide_controller
        assert ctrl is not None

        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("status", width=4)
        table.add_column("label")
        table.add_column("detail", style="dim")

        for item in ctrl.checklist:
            icon = _status_icon(item.status)
            detail = item.detail or ""
            table.add_row(icon, item.label, detail)

        self._console.print()
        self._console.print(table)

    def _render_next(self) -> None:
        ctrl = self._guide_controller
        assert ctrl is not None

        next_step = ctrl.find_next_step_from_workflow()
        if next_step is None:
            self._console.print()
            self._console.print("[green]→ All setup phases complete. Your workspace is ready to deploy.[/green]")
            return

        hint_text = Text()
        hint_text.append("→ Next: ", style="bold")
        hint_text.append(next_step.hint)
        if next_step.command:
            hint_text.append(f"\n\n  $ {next_step.command}", style="cyan")
        if next_step.see_also:
            hint_text.append(f"\n  See: {next_step.see_also}", style="dim")

        self._console.print()
        self._console.print(hint_text)

    def _render_file_checklist(self, checklist: List[ChecklistItem], file_path: Path, kind: Optional[str]) -> None:
        kind_str = f"kind: {kind}" if kind else "kind: unknown"
        self._console.print(f"\n[bold]File:[/bold] {file_path}  ({kind_str})")

        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("status", width=4)
        table.add_column("label")
        table.add_column("detail", style="dim")

        for item in checklist:
            icon = _status_icon(item.status)
            table.add_row(icon, item.label, item.detail or "")

        self._console.print(table)

    def _render_help(self) -> None:
        table = Table(title="Console Commands", box=None, padding=(0, 2))
        table.add_column("Command", style="bold")
        table.add_column("Alias")
        table.add_column("Description")

        commands = [
            ("status", "s", "Show workspace checklist"),
            ("check <file>", "c", "Inspect a YAML file"),
            ("next", "n", "Show next step with hint"),
            ("do", "d", "Execute the suggested next-step command"),
            ("new <template> [name]", "", "Scaffold a file via strata new"),
            ("validate [file|glob]", "v", "Run validation"),
            ("diagram [-f NAME]", "g", "Render a workspace diagram"),
            ("templates", "t", "List available templates"),
            ("tools", "", "Check external tool availability"),
            ("open <file>", "o", "Open file in editor"),
            ("reload", "", "Reload workspace state from disk"),
            ("help", "?", "Show this help"),
            ("clear", "", "Clear terminal"),
            ("quit", "q", "Exit console"),
        ]
        for cmd, alias, desc in commands:
            table.add_row(cmd, alias, desc)

        self._console.print()
        self._console.print(table)

    # ------------------------------------------------------------------
    # Prompt session setup
    # ------------------------------------------------------------------

    def _create_prompt_session(self) -> Optional[PromptSession[str]]:
        """Create a prompt_toolkit session with history and completion. Returns None if not a TTY."""
        if not sys.stdin.isatty():
            return None

        history_path = self._work_path / ".strata" / "console-history"
        history: FileHistory | InMemoryHistory
        try:
            history_path.parent.mkdir(parents=True, exist_ok=True)
            history = FileHistory(str(history_path))
        except Exception:
            history = InMemoryHistory()

        completer = NestedCompleter.from_nested_dict(
            {
                "status": None,
                "s": None,
                "check": PathCompleter(),
                "c": PathCompleter(),
                "next": None,
                "n": None,
                "do": None,
                "d": None,
                "new": WordCompleter(
                    [
                        "configuration",
                        "environment",
                        "deployment",
                        "namespace",
                        "module",
                        "resource",
                        "provider",
                        "tenant",
                        "dns",
                        "firewall",
                        "network",
                        "workspace",
                    ]
                ),
                "validate": PathCompleter(),
                "v": PathCompleter(),
                "diagram": None,
                "g": None,
                "templates": None,
                "t": None,
                "tools": None,
                "open": PathCompleter(),
                "o": PathCompleter(),
                "reload": None,
                "help": None,
                "?": None,
                "clear": None,
                "quit": None,
                "q": None,
                "exit": None,
            }
        )

        return PromptSession(history=history, completer=completer)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _status_icon(status: str) -> str:
    if status == "ok":
        return "✅"
    elif status == "warn":
        return "⚠️ "
    return "⬜"


def _progress_bar(done: int, total: int, width: int = 20) -> str:
    filled = int(width * done / total) if total > 0 else 0
    return "█" * filled + "░" * (width - filled)
