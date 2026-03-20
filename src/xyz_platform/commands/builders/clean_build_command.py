#!/usr/bin/env python3
"""
===============================================================================
Script Name   : clean_build_command.py
Author        : XYZ Platform Team
Version       : 1.0.0
Python Version: 3.12+
Description   : Command to clean build artifacts.
===============================================================================
"""

import shutil

import click

from xyz_platform.commands.builders.base_build_command import BaseBuildCommand
from xyz_platform.controllers.workspace_controller import WorkspaceController


class CleanBuildCommand(BaseBuildCommand):
    """Clean build artifacts for a deployment."""

    def __init__(
        self,
        file: str = None,
        work_path: str = None,
        dry_run: bool = False,
        no_hooks: bool = False,
        output: str = None,
        verbose: bool = None,
        quiet: bool = None,
    ):
        super().__init__(
            file=file,
            work_path=work_path,
            no_hooks=no_hooks,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self._dry_run = dry_run

    def execute(self) -> bool:
        try:
            if not self._initialize(operation="build_clean"):
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(operation="build_clean", success=False)
                return False

            if not self._before_execute():
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(operation="build_clean", success=False)
                return False

            if not self._clean_build_output():
                if self._is_console_output():
                    click.echo("\n❌  Build cleanup failed")
                self._finalize(operation="build_clean", success=False)
                return False

            if not self._after_execute():
                if self._is_console_output():
                    click.echo("\n❌  Post-execution hook failed")
                self._finalize(operation="build_clean", success=False)
                return False

            self._output_data.update(
                {
                    "file": str(self._file_path),
                    "build_path": str(self._build_path),
                    "dry_run": self._dry_run,
                }
            )

            self._finalize(operation="build_clean", success=True)
            return True

        except Exception as exc:
            self._errors.append(f"Failed to clean build artifacts: {exc}")
            self.logger.exception("build_clean failed")
            self._finalize(operation="build_clean", success=False)
            return False

    def _clean_build_output(self) -> bool:
        workspace_controller = WorkspaceController()
        instance_path = workspace_controller.get_workspace_buildpath_instance(
            deployment_service=self._deployment_service,
            build_path=self._build_path,
        )

        if self._dry_run:
            self._messages.append(f"[DRY-RUN] Would remove: {instance_path}")
            return True

        if not instance_path.exists():
            self._messages.append(f"Build path does not exist: {instance_path}")
            return True

        try:
            shutil.rmtree(instance_path)
            self._messages.append(f"Removed build artifacts: {instance_path}")
            return True
        except Exception as exc:
            self._errors.append(f"Failed to remove build path {instance_path}: {exc}")
            return False
