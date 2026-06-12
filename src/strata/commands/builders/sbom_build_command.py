"""Command to (re)generate the SBOM from an existing platform.json."""

from typing import Optional

import click

from strata.builders.sbom_builder import SbomBuilder
from strata.commands.builders.base_build_command import BaseBuildCommand
from strata.services.platform_artifact_service import PlatformService


class SbomBuildCommand(BaseBuildCommand):
    """(Re)generate the SBOM from an existing ``platform.json``.

    Loads the platform artifact that was produced by a previous
    ``strata build run``, runs all SBOM collectors, and writes a fresh
    ``sbom.json`` to the same deployment build directory.  No other build
    steps are executed.
    """

    OPERATION = "build_sbom"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
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

            if not self._execute_sbom_build():
                if self._is_console_output():
                    click.echo("\n❌  SBOM build failed")
                self._finalize(success=False)
                return False

            self._output_data.update(
                {
                    "file": str(self._file_path),
                    "build_path": str(self._build_path),
                }
            )

            self._finalize(success=True)
            return True

        except Exception as exc:
            self._errors.append(f"Failed to execute build_sbom: {exc}")
            self.logger.exception("build_sbom failed")
            self._finalize(success=False)
            return False

    def _execute_sbom_build(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        # Load the existing platform.json to pass as pre-assembled model
        platform_path = self._deployment_service.get_build_path(self._build_path) / "platform.json"
        if not platform_path.exists():
            self._errors.append(f"Platform model not found at: {platform_path}. Run 'strata build run' first.")
            return False

        platform_service = PlatformService.load(str(platform_path), validate=True)
        if not platform_service.is_validated() or not platform_service.model:
            self._errors.append("Platform model validation failed")
            return False

        builder = SbomBuilder(verbose=self._is_verbose())

        ok = builder.before_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=False,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        ok = builder.build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=False,
            platform_model=platform_service.model,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        ok = builder.after_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=False,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        return True
