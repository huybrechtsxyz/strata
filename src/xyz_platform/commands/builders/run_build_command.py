#!/usr/bin/env python3
"""
===============================================================================
Script Name   : run_build_command.py
Author        : XYZ Platform Team
Version       : 1.0.0
Python Version: 3.12+
Description   : Command to execute build operations.
===============================================================================
"""

import click

from xyz_platform.builders.platform_builder import PlatformBuilder
from xyz_platform.builders.terraform_builder import TerraformBuilder
from xyz_platform.commands.builders.base_build_command import BaseBuildCommand
from xyz_platform.controllers.workspace_controller import WorkspaceController


class RunBuildCommand(BaseBuildCommand):
    """Run build pipeline (platform + terraform)."""

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
            if not self._initialize(operation="build_run"):
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(operation="build_run", success=False)
                return False

            if not self._before_execute():
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(operation="build_run", success=False)
                return False

            if not self._load_related_services():
                if self._is_console_output():
                    click.echo("\n❌  Failed to load deployment related services")
                self._finalize(operation="build_run", success=False)
                return False

            if self._dry_run and self._is_console_output():
                click.echo(
                    "\n[DRY-RUN] Validating and planning build — no files will be written"
                )

            if not self._execute_platform_build():
                if self._is_console_output():
                    click.echo("\n❌  Platform build failed")
                self._finalize(operation="build_run", success=False)
                return False

            if not self._execute_terraform_build():
                if self._is_console_output():
                    click.echo("\n❌  Terraform build failed")
                self._finalize(operation="build_run", success=False)
                return False

            if not self._after_execute():
                if self._is_console_output():
                    click.echo("\n❌  Post-execution hook failed")
                self._finalize(operation="build_run", success=False)
                return False

            self._output_data.update(
                {
                    "file": str(self._file_path),
                    "build_path": str(self._build_path),
                    "object_path": str(self._object_path),
                    "dry_run": self._dry_run,
                }
            )

            self._finalize(operation="build_run", success=True)
            return True

        except Exception as exc:
            self._errors.append(f"Failed to execute build_run: {exc}")
            self.logger.exception("build_run failed")
            self._finalize(operation="build_run", success=False)
            return False

    def _load_related_services(self) -> bool:
        """Load deployment related services from session-managed work path."""
        workspace_controller = WorkspaceController()
        _, load_success = workspace_controller.load_related_services(
            deployment_service=self._deployment_service,
            objects_path=self._work_path,
            stage_name=None,
        )
        if not load_success:
            self._errors.extend(self._deployment_service.get_validation_errors())
            return False

        data_success, data_errors = workspace_controller.load_related_service_data(
            deployment_service=self._deployment_service,
            stage_name=None,
        )
        if not data_success:
            self._errors.extend(data_errors)
            return False

        return True

    def _execute_platform_build(self) -> bool:
        builder = PlatformBuilder(verbose=self._is_verbose())

        ok, msgs = builder.before_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
        )
        self._messages.extend(msgs)
        if not ok:
            self._errors.extend(msgs)
            return False

        ok, msgs = builder.build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            configuration_service=self._configuration_service,
            dry_run=self._dry_run,
        )
        self._messages.extend(msgs)
        if not ok:
            self._errors.extend(msgs)
            return False

        # Store assembled model so terraform builder can reuse it in dry-run
        self._platform_model = builder._last_platform_model

        ok, msgs = builder.after_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
        )
        self._messages.extend(msgs)
        if not ok:
            self._errors.extend(msgs)
            return False

        return True

    def _execute_terraform_build(self) -> bool:
        builder = TerraformBuilder(verbose=self._is_verbose())

        ok, msgs = builder.before_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
        )
        self._messages.extend(msgs)
        if not ok:
            self._errors.extend(msgs)
            return False

        ok, msgs = builder.build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
            platform_model=getattr(self, "_platform_model", None),
        )
        self._messages.extend(msgs)
        if not ok:
            self._errors.extend(msgs)
            return False

        ok, msgs = builder.after_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
        )
        self._messages.extend(msgs)
        if not ok:
            self._errors.extend(msgs)
            return False

        return True
