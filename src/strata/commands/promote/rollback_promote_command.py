"""Reverse a promotion (strata promote rollback)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import click

from strata.commands.promote.base_promote_command import BasePromoteCommand
from strata.controllers.promote_controller import PromoteController


class RollbackPromoteCommand(BasePromoteCommand):
    """Reverse a completed promotion using the same strategy.

    Resolves the previous version via: activity log → git merge-base → --from-version.
    Creates a rollback branch, reverts the ring lock, commits, and writes
    a ``kind: promotion-record`` with ``outcome: rolled-back``.
    """

    OPERATION = "promote_rollback"

    def __init__(
        self,
        remote: Optional[str],
        module: Optional[str],
        to: str,
        from_version: Optional[str] = None,
        dry_run: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._remote = remote
        self._module = module
        self._to = to
        self._from_version = from_version
        self._dry_run = dry_run
        self._controller: Optional[PromoteController] = None
        self._result: dict = {}

    def get_required_integrations(self) -> dict:
        return {}

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        self._controller = PromoteController()
        return True

    def _run(self) -> bool:
        if self._remote and self._module:
            self._errors.append("--remote and --module are mutually exclusive.")
            return False
        if not self._remote and not self._module:
            self._errors.append("One of --remote or --module is required.")
            return False

        target_type = "remote" if self._remote else "module"
        target_name = self._remote or self._module

        self._result = self._controller.run_rollback(
            target_type=target_type,
            target_name=target_name,
            to_ring=self._to,
            from_version=self._from_version,
            work_path=Path(str(self._work_path)),
            dry_run=self._dry_run,
        )

        if self._controller.has_errors():
            for err in self._controller.get_errors():
                self._errors.append(err)
            return False

        self._output_data = self._result
        self._render()
        return True

    def _render(self) -> None:
        r = self._result
        if self._output_format == "json":
            click.echo(json.dumps({"success": True, **r}, indent=2))
        elif self._output_format == "text":
            click.echo(r.get("branch", ""))
        elif not self._output_quiet:
            if r.get("dry_run"):
                click.echo("🔍  Dry-run — no changes made")
                click.echo(f"    ring:           {r.get('ring')}")
                click.echo(f"    current version:{' ' + str(r.get('current_version', 'unknown'))}")
                click.echo(f"    rollback to:    {r.get('rollback_to_version')}")
            else:
                click.echo("✅  Rollback committed")
                click.echo(f"    branch:    {r.get('branch')}")
                click.echo(f"    commit:    {r.get('commit_sha', 'n/a')}")
                click.echo(
                    f"    rolled back {r.get('target_name')} {r.get('rolled_back_from')} → {r.get('rolled_back_to')}"
                )
                if r.get("promotion_record"):
                    click.echo(f"    record:    {r['promotion_record']}")
                click.echo(f"\n    Next: {r.get('pr_suggestion', '')}")
