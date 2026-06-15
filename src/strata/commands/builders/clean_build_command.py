"""Command to clean deployment build artifacts."""

import shutil
from typing import Optional

import click

from strata.commands.builders.base_build_command import BaseBuildCommand


class CleanBuildCommand(BaseBuildCommand):
    """Clean build artifacts for a deployment."""

    OPERATION = "build_clean"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        dry_run: bool = False,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ):
        super().__init__(
            file=file,
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self._dry_run = dry_run

    def get_required_integrations(self):
        return {}

    def execute(self) -> bool:
        try:
            if not self._initialize():
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(success=False)
                return False

            if not self._before_execute():
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(success=False)
                return False

            if not self._run_lifecycle_phase(
                "build_clean_before",
                context={"file": str(self._file_path), "dry_run": self._dry_run},
            ):
                if self._is_console_output():
                    click.echo("\n❌  Pre-clean lifecycle hook failed")
                self._finalize(success=False)
                return False

            if not self._clean_build_output():
                if self._is_console_output():
                    click.echo("\n❌  Build cleanup failed")
                self._finalize(success=False)
                return False

            if not self._run_lifecycle_phase(
                "build_clean_after",
                context={"file": str(self._file_path), "dry_run": self._dry_run},
            ):
                if self._is_console_output():
                    click.echo("\n❌  Post-clean lifecycle hook failed")
                self._finalize(success=False)
                return False

            if not self._after_execute():
                if self._is_console_output():
                    click.echo("\n❌  Post-execution hook failed")
                self._finalize(success=False)
                return False

            self._output_data.update(
                {
                    "file": str(self._file_path),
                    "build_path": str(self._build_path),
                    "dry_run": self._dry_run,
                }
            )

            self._finalize(success=True)
            return True

        except Exception as exc:
            self._errors.append(f"Failed to clean build artifacts: {exc}")
            self.logger.exception("build_clean failed")
            self._finalize(success=False)
            return False

    def _clean_build_output(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False
        instance_path = self._deployment_service.get_build_path(self._build_path)

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
