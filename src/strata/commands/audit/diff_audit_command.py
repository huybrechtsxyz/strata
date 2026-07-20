"""Command to show configuration changes between two deployments (strata audit diff)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import click

from strata.commands.schemas.schema_base_command import SchemaBaseCommand
from strata.controllers.audit_controller import AuditController
from strata.utils.config import get_deploy_log_dir


class DiffAuditCommand(SchemaBaseCommand):
    """Show configuration changes between two deployment executions.

    Accepts two execution IDs from the deploy-log.  Retrieves the commit SHAs
    recorded in each log entry, then runs ``git diff <sha_before> <sha_after>``
    on the deployment YAML file that was executed.

    Exit codes:
      0  No changes (identical commits or empty diff)
      1  System error (git unavailable, execution IDs not found)
      3  Changes detected (treated as a validation difference for scripting)
    """

    OPERATION = "audit_diff"

    def __init__(
        self,
        from_id: str,
        to_id: str,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._from_id = from_id
        self._to_id = to_id
        self._diff_lines: List[str] = []
        self._from_entry: Any = None
        self._to_entry: Any = None

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

        # Find both entries in the deploy-log
        all_entries = controller.query_deploy_logs(base_path=base_path)
        entry_map = {e.execution_id: e for e in all_entries}

        from_entry = entry_map.get(self._from_id)
        to_entry = entry_map.get(self._to_id)

        if not from_entry:
            self._errors.append(
                f"Execution ID not found in deploy-log: {self._from_id!r}. "
                "Run 'strata audit changes' to list available IDs."
            )
            return False
        if not to_entry:
            self._errors.append(
                f"Execution ID not found in deploy-log: {self._to_id!r}. "
                "Run 'strata audit changes' to list available IDs."
            )
            return False

        self._from_entry = from_entry
        self._to_entry = to_entry

        if not from_entry.commit_sha:
            self._errors.append(f"Execution {self._from_id!r} has no commit SHA — was the deployment run outside git?")
            return False
        if not to_entry.commit_sha:
            self._errors.append(f"Execution {self._to_id!r} has no commit SHA — was the deployment run outside git?")
            return False

        if from_entry.commit_sha == to_entry.commit_sha:
            # Same commit — no diff possible
            self._diff_lines = []
            self._output_data = self._build_output(diff_text="", has_changes=False)
            return True

        # Run git diff between the two commits
        diff_text = self._run_git_diff(
            from_sha=from_entry.commit_sha,
            to_sha=to_entry.commit_sha,
            yaml_file=from_entry.file,
        )
        if diff_text is None:
            # Error already recorded in _errors
            return False

        self._diff_lines = diff_text.splitlines()
        self._output_data = self._build_output(diff_text=diff_text, has_changes=bool(diff_text.strip()))
        return True

    def _run_git_diff(self, from_sha: str, to_sha: str, yaml_file: str) -> Optional[str]:
        """Run git diff and return the output, or None on failure."""
        try:
            from strata.utils.system import run_command

            result = run_command(
                ["git", "diff", from_sha, to_sha, "--", yaml_file],
                cwd=str(self._work_path),
                timeout=30,
            )
            if result.returncode not in (0, 1):  # git diff returns 1 when differences found
                self._errors.append(
                    f"git diff failed (exit {result.returncode}): {result.stderr or result.stdout or ''}".strip()
                )
                return None
            return result.stdout or ""
        except Exception as exc:
            self._errors.append(f"git diff error: {exc}")
            return None

    def _build_output(self, diff_text: str, has_changes: bool) -> Dict[str, Any]:
        return {
            "from": {
                "execution_id": self._from_entry.execution_id,
                "timestamp": self._from_entry.timestamp,
                "commit_sha": self._from_entry.commit_sha,
                "deployment": str(self._from_entry.deployment),
            },
            "to": {
                "execution_id": self._to_entry.execution_id,
                "timestamp": self._to_entry.timestamp,
                "commit_sha": self._to_entry.commit_sha,
                "deployment": str(self._to_entry.deployment),
            },
            "file": self._from_entry.file,
            "has_changes": has_changes,
            "diff": diff_text,
        }

    def _after_execute(self) -> bool:
        if self._is_console_output():
            self._render_console()
        return super()._after_execute()

    def _render_console(self) -> None:
        if not self._from_entry or not self._to_entry:
            return

        click.echo(f"Deployment:  {self._from_entry.deployment}")
        click.echo(f"File:        {self._from_entry.file}")
        click.echo(
            f"From:  {self._from_entry.commit_sha[:12] if self._from_entry.commit_sha else 'n/a'}"
            f"  ({self._from_entry.timestamp[:19]}"
            + (f"  PR #{self._from_entry.pull_request.number}" if self._from_entry.pull_request else "")
            + ")"
        )
        click.echo(
            f"To:    {self._to_entry.commit_sha[:12] if self._to_entry.commit_sha else 'n/a'}"
            f"  ({self._to_entry.timestamp[:19]}"
            + (f"  PR #{self._to_entry.pull_request.number}" if self._to_entry.pull_request else "")
            + ")"
        )
        click.echo()

        if not self._diff_lines:
            click.echo("No configuration changes between these two deployments.")
            return

        # Colourize the unified diff if the terminal supports it
        for line in self._diff_lines:
            if line.startswith("+") and not line.startswith("+++"):
                click.echo(click.style(line, fg="green"))
            elif line.startswith("-") and not line.startswith("---"):
                click.echo(click.style(line, fg="red"))
            elif line.startswith("@@"):
                click.echo(click.style(line, fg="cyan"))
            else:
                click.echo(line)

    def has_validation_errors(self) -> bool:
        """Return True when changes are detected (exit code 3 for scripting)."""
        return bool(self._diff_lines)
