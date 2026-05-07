"""Command to execute the platform build pipeline."""

from typing import Optional

import click

from xyz_platform.builders.platform_builder import PlatformBuilder
from xyz_platform.builders.terraform_builder import TerraformBuilder
from xyz_platform.commands.builders.base_build_command import BaseBuildCommand


class RunBuildCommand(BaseBuildCommand):
    """Run build pipeline (platform + terraform)."""

    OPERATION = "build_run"

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

            if not self._load_related_services():
                if self._is_console_output():
                    click.echo("\n❌  Failed to load deployment related services")
                self._finalize(success=False)
                return False

            if self._dry_run and self._is_console_output():
                click.echo("\n[DRY-RUN] Validating and planning build — no files will be written")

            if not self._execute_platform_build():
                if self._is_console_output():
                    click.echo("\n❌  Platform build failed")
                self._finalize(success=False)
                return False

            if not self._execute_terraform_build():
                if self._is_console_output():
                    click.echo("\n❌  Terraform build failed")
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
            self._errors.append(f"Failed to execute build_run: {exc}")
            self.logger.exception("build_run failed")
            self._finalize(success=False)
            return False

    def _load_related_services(self) -> bool:
        """Services are already loaded by BaseBuildCommand._before_execute."""
        return True

    def _execute_platform_build(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False
        builder = PlatformBuilder(
            verbose=self._is_verbose(),
            configuration_service=self._configuration_service,
        )

        ok = builder.before_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
        )
        self._messages.extend(builder.get_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        ok = builder.build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
        )
        self._messages.extend(builder.get_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        # Store assembled model so terraform builder can reuse it in dry-run
        self._platform_model = builder._last_platform_model

        ok = builder.after_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
        )
        self._messages.extend(builder.get_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        return True

    def _execute_terraform_build(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False
        builder = TerraformBuilder(verbose=self._is_verbose())

        ok = builder.before_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
        )
        self._messages.extend(builder.get_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        ok = builder.build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
            platform_model=getattr(self, "_platform_model", None),
        )
        self._messages.extend(builder.get_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        ok = builder.after_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=self._dry_run,
        )
        self._messages.extend(builder.get_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        return True
