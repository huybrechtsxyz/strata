#!/usr/bin/env python3
"""
===============================================================================
Script Name   : sync_session_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Command to re-validate session config sources and re-merge
                the platform configuration.
===============================================================================
"""

from pathlib import Path
from typing import Dict, List, Optional

import click

from xyz_platform.commands.session.base_session_command import BaseSessionCommand


class SyncSessionCommand(BaseSessionCommand):
    """
    Re-validate session config sources and re-merge configuration.

    Behaviour:
    - Reports missing config sources as errors
    - Re-merges all valid sources → .xyz-platform/configuration.yaml

    With --force:
    - Removes missing config sources from session state
    - Re-merges remaining sources
    """

    def __init__(
        self,
        force: bool = False,
        work_path: Optional[str] = None,
        output: str = None,
        verbose: bool = None,
        quiet: bool = None,
    ):
        """
        Initialize the sync command.

        Args:
            force: Remove missing config sources from session state
            work_path: Root working directory
            output: Output format
            verbose: Enable verbose output
            quiet: Disable all console output
        """
        super().__init__(
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
            require_session=True,
        )
        self._force = force
        self._sync_errors: List[str] = []
        self._sync_warnings: List[str] = []
        self._source_statuses: List[Dict] = []

    def execute(self) -> bool:
        """Execute the sync command."""
        try:
            if not self._initialize(operation="session_sync"):
                self.logger.error(f"Initialization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(operation="session_sync", success=False)
                return False

            if not self._before_execute():
                self.logger.error(
                    f"Pre-execution validation failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(operation="session_sync", success=False)
                return False

            # Build pre-sync status snapshot
            for source in self._session_controller.get_config_sources():
                source_path = Path(source["path"])
                source_type = source.get("type", "file")
                exists = (
                    source_path.is_file()
                    if source_type == "file"
                    else source_path.is_dir()
                )
                self._source_statuses.append(
                    {
                        "name": source.get("name", ""),
                        "path": source["path"],
                        "type": source_type,
                        "exists": exists,
                    }
                )

            # Sync via controller
            success, self._sync_errors, self._sync_warnings = (
                self._session_controller.sync_config_sources(
                    work_path=self._work_path,
                    force=self._force,
                )
            )

            self._errors.extend(self._session_controller.get_errors())
            self._messages.extend(self._session_controller.get_messages())

            if not self._after_execute():
                self.logger.error(
                    f"Post-execution hook failed in {self.__class__.__name__}"
                )
                self._finalize(operation="session_sync", success=False)
                return False

            if not self._finalize(operation="session_sync", success=success):
                return False

            return success

        except Exception as e:
            error_msg = f"Failed to sync config sources: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(operation="session_sync", success=False)
            return False

    def _initialize(self, operation: str = None) -> bool:
        if not super()._initialize(operation=operation):
            return False
        self.logger.debug(
            "Sync command initialized",
            extra={
                "command_class": self.__class__.__name__,
                "force": self._force,
            },
        )
        return True

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        return True

    def _after_execute(self) -> bool:
        missing_count = sum(1 for s in self._source_statuses if not s["exists"])
        ok_count = sum(1 for s in self._source_statuses if s["exists"])

        self._output_data = {
            "config_sources": self._source_statuses,
            "summary": {
                "total": len(self._source_statuses),
                "ok": ok_count,
                "missing": missing_count,
                "force": self._force,
            },
            "errors": self._sync_errors,
            "warnings": self._sync_warnings,
        }

        if self._is_console_output():
            total = len(self._source_statuses)
            if total == 0:
                click.echo(
                    "\n⚠️   No config sources registered. Use 'session add --config-file/--config-path' first."
                )
            else:
                click.echo(f"\n🔄  Config sources ({total}):")
                for src in self._source_statuses:
                    icon = "✅" if src["exists"] else "❌"
                    removed = "  (removed)" if not src["exists"] and self._force else ""
                    click.echo(
                        f"    {icon}  {src['name']:<20} [{src['type']}]  {src['path']}{removed}"
                    )
                click.echo("")

                merged_config = self._work_path / ".xyz-platform" / "configuration.yaml"
                if merged_config.exists():
                    click.echo(f"    📄  Merged config: {merged_config}")
                click.echo("")

                if self._sync_warnings:
                    for w in self._sync_warnings:
                        click.echo(f"    ⚠️   {w}")
                    click.echo("")

                summary_parts = [f"OK: {ok_count}", f"Missing: {missing_count}"]
                click.echo(f"    {' | '.join(summary_parts)}")
                click.echo("")

        return super()._after_execute()

    def _finalize(
        self, operation: str = None, success: bool = None, show_footer: bool = True
    ) -> bool:
        self.logger.debug(
            "Sync command finalized",
            extra={"command_class": self.__class__.__name__},
        )
        return super()._finalize(operation=operation, success=success)
