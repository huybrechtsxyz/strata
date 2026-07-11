"""Scan a workspace and sync discovered targets into a version-manifest file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import click

from strata.commands.versions.base_versions_command import BaseVersionsCommand
from strata.controllers.version_controller import VersionController
from strata.utils.system import resolve_path


class RefreshVersionsCommand(BaseVersionsCommand):
    """Scan a workspace and sync discovered targets into a version-manifest file.

    New targets found by the scanner are added with empty (seed) version
    strings.  Targets no longer discovered can be reported or removed with
    ``--remove-stale``.  Pass ``--dry-run`` to preview changes without writing.
    """

    OPERATION = "versions_refresh"

    def __init__(
        self,
        file: str,
        scan: Optional[str],
        remove_stale: bool = False,
        dry_run: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._file = file
        self._scan = scan
        self._remove_stale = remove_stale
        self._dry_run = dry_run
        self._controller: Optional[VersionController] = None
        self._result: dict = {}

    def get_required_integrations(self) -> dict:
        return {}

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        self._controller = VersionController()
        return True

    def _run(self) -> bool:
        file_path = Path(resolve_path(str(self._work_path), self._file))
        scan_dir_raw = self._scan or str(self._work_path)
        scan_dir = Path(scan_dir_raw)

        self._result = self._controller.refresh_manifest(
            file_path,
            scan_dir,
            remove_stale=self._remove_stale,
            dry_run=self._dry_run,
        )

        if self._controller.has_errors():
            for err in self._controller.get_errors():
                self._errors.append(err)
            return False

        self._output_data = self._result
        self._render()
        return True

    # ── output ────────────────────────────────────────────────────────────────

    def _render(self) -> None:
        added = self._result.get("added", {})
        stale = self._result.get("stale", {})
        dry_run = self._result.get("dry_run", False)
        remove_stale = self._result.get("stale_removed", False)
        file_path_str = self._result.get("file", "")

        total_added = sum(len(v) for v in added.values())
        total_stale = sum(len(v) for v in stale.values())

        if self._output_format == "json":
            click.echo(json.dumps({"success": True, "dry_run": dry_run, "added": added, "stale": stale, "stale_removed": remove_stale, "file": file_path_str}, indent=2))
        elif self._output_format == "text":
            for type_key, names in added.items():
                for name in names:
                    click.echo(f"added:{type_key}/{name}")
            for type_key, names in stale.items():
                for name in names:
                    verb = "removed" if remove_stale else "stale"
                    click.echo(f"{verb}:{type_key}/{name}")
        elif not self._output_quiet:
            if total_added == 0 and total_stale == 0:
                click.echo("✅  Manifest is already up to date — no changes needed.")
            else:
                if total_added:
                    click.echo(f"\n  ➕  {total_added} new target(s) added:")
                    for type_key, names in added.items():
                        for name in sorted(names):
                            click.echo(f"       {type_key}/{name}")
                if total_stale:
                    verb = "removed" if remove_stale else "found (use --remove-stale to delete)"
                    click.echo(f"\n  ⚠   {total_stale} stale target(s) {verb}:")
                    for type_key, names in stale.items():
                        for name in sorted(names):
                            click.echo(f"       {type_key}/{name}")
                if dry_run:
                    click.echo("\n  (dry-run — manifest not written)")
                else:
                    click.echo(f"\n✅  Updated: {file_path_str}")
