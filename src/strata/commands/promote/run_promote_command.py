"""Execute a new-style pointer promotion (strata promote <ring> <file> --promotion <name>)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import click

from strata.commands.promote.base_promote_command import BasePromoteCommand
from strata.controllers.promote_controller import PromoteController


class RunPromoteCommand(BasePromoteCommand):
    """Write a pointer ring lock file for the ADR-0011 layered promotion design.

    Resolves the promotion strategy by name, validates the version file is inside
    the strategy's versions_path, and writes ``{versions_path}/{ring}.lock.yaml``
    as a thin YAML pointer to the version file.
    """

    OPERATION = "promote_run"

    def __init__(
        self,
        ring: str,
        file: str,
        promotion: str,
        wave: Optional[int] = None,
        complete: bool = False,
        dry_run: bool = False,
        force: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._ring = ring
        self._file = file
        self._promotion = promotion
        self._wave = wave
        self._complete = complete
        self._dry_run = dry_run
        self._force = force
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
        assert self._controller is not None
        file_path = Path(self._file)
        if not file_path.is_absolute():
            file_path = Path(str(self._work_path)) / file_path

        self._result = self._controller.run_promote(
            ring=self._ring,
            version_file=file_path,
            promotion_name=self._promotion,
            wave=self._wave,
            complete=self._complete,
            force=self._force,
            dry_run=self._dry_run,
            work_path=Path(str(self._work_path)),
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
                click.echo(f"    promotion:  {r['promotion']}")
                click.echo(f"    ring:       {r['ring']}")
                click.echo(f"    version:    {Path(r['version_file']).name}")
                click.echo(f"    branch:     {r['branch']}")
                click.echo(f"    write:      {', '.join(r.get('files_to_write', []))}")
                if r.get("files_to_delete"):
                    click.echo(f"    delete:     {', '.join(r['files_to_delete'])}")
            else:
                click.echo("✅  Promoted")
                click.echo(f"    promotion:  {r['promotion']}")
                click.echo(f"    ring:       {r['ring']}")
                click.echo(f"    version:    {Path(r['version_file']).name}")
                click.echo(f"    branch:     {r['branch']}")
                click.echo(f"    commit:     {r.get('commit_sha', 'n/a')}")
                if r.get("files_written"):
                    click.echo(f"    wrote:      {', '.join(r['files_written'])}")
                if r.get("files_deleted"):
                    click.echo(f"    deleted:    {', '.join(r['files_deleted'])}")
                click.echo(f"\n    Next: {r.get('pr_suggestion', '')}")
