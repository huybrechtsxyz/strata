"""Command to validate a single platform YAML file."""

from pathlib import Path
from typing import Dict, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.validators.platform_validator import PlatformValidator


class ValidateCommand(BaseCommand):
    """Validate a single platform YAML file against its kind-specific service.

    Works both inside and outside an initialized workspace.  The ``--deep``
    flag enables Phase 2 (cross-reference) validation by loading
    ``ConfigurationService`` from the active profile's ``configfile_paths``; it
    is a no-op when the workspace is uninitialized or has no active profile.
    """

    OPERATION = "validate"
    INIT_REQUIRED = False

    def __init__(
        self,
        file_path: str,
        deep: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._file_path_raw: str = file_path
        self._deep: bool = deep
        self._resolved_file: Optional[Path] = None
        self._validator: Optional[PlatformValidator] = None
        self._detected_kind: Optional[str] = None

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def has_validation_errors(self) -> bool:
        """Return True when the validator found errors in the file."""
        return self._validator.has_errors() if self._validator else False

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

            if not self._run_execution():
                if self._is_console_output():
                    click.echo("\n❌  Execution failed")
                self._finalize(success=False)
                return False

            if not self._after_execute():
                if self._is_console_output():
                    click.echo("\n❌  Post-execution processing failed")
                self._finalize(success=False)
                return False

            # Finalize as success even when there are validation errors —
            # system-level execution succeeded; exit code difference is handled
            # by handle_command_exit() via has_validation_errors().
            self._finalize(success=True)
            return True

        except Exception as e:
            error_msg = f"Failed to validate file: {e}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False

    # ------------------------------------------------------------------
    # Lifecycle overrides
    # ------------------------------------------------------------------

    def _before_execute(self) -> bool:
        """Resolve file_path to an absolute Path and verify it exists."""
        if not super()._before_execute():
            return False

        if self._file_path_raw.startswith("@"):
            try:
                from strata.utils.system import resolve_path

                repo_map = self._solution_controller.get_repo_map()
                candidate = resolve_path(str(self._work_path), self._file_path_raw, repo_map=repo_map)
            except Exception as e:
                raise click.UsageError(f"Cannot resolve '{self._file_path_raw}': {e}") from e
        else:
            candidate = Path(self._file_path_raw)
            if not candidate.is_absolute():
                candidate = Path.cwd() / candidate
            candidate = candidate.resolve()

        if not candidate.exists():
            raise click.UsageError(f"File not found: {candidate}")

        self._resolved_file = candidate
        self.logger.debug("Resolved file path", path=str(self._resolved_file))
        return True

    def _run_execution(self) -> bool:
        """Run the validator pipeline and collect errors."""
        config_svc = self._load_configuration_service()
        # _load_configuration_service appends to self._errors on hard failure
        if self._deep and config_svc is None:
            return False  # exit code 1 — system error, not validation error

        assert self._resolved_file is not None  # guaranteed by _before_execute
        solution_repo_map = self._solution_controller.get_repo_map()
        self._validator = PlatformValidator(
            file_path=self._resolved_file,
            configuration_service=config_svc,
            repo_map=solution_repo_map,
        )

        work_path = self._work_path

        # Run phases in sequence; stop on first failure
        phases = [
            ("before_validate", self._validator.before_validate),
            ("validate", self._validator.validate),
            ("after_validate", self._validator.after_validate),
        ]
        for phase_name, phase_fn in phases:
            self.logger.debug("Running validator phase", phase=phase_name, file=str(self._resolved_file))
            result = phase_fn(work_path)
            if not result:
                self.logger.warning(
                    "Validator phase returned False",
                    phase=phase_name,
                    file=str(self._resolved_file),
                )
                break

        self._detected_kind = self._validator.detected_kind.value if self._validator.detected_kind else None
        validation_passed = not self._validator.has_errors()

        self.logger.debug(
            "Validation complete",
            file=str(self._resolved_file),
            kind=self._detected_kind,
            deep=self._deep,
            validation_passed=validation_passed,
            error_count=len(self._validator.get_errors()),
        )

        self._output_data = {
            "file": str(self._resolved_file),
            "kind": self._detected_kind,
            "deep": self._deep,
            "validation_passed": validation_passed,
            "errors": [e.to_dict() for e in self._validator.get_structured_errors()],
        }

        return True

    def _after_execute(self) -> bool:
        """Emit human-readable console output."""
        if not super()._after_execute():
            return False

        if self._is_console_output():
            click.echo(f"\n📄  File  : {self._resolved_file}")
            click.echo(f"🏷️   Kind  : {self._detected_kind or 'unknown'}")
            click.echo(f"🔬  Deep  : {'yes' if self._deep else 'no'}")

            if self.has_validation_errors():
                click.echo("\n❌  Validation FAILED")
                click.echo("    Errors:")
                for err in self._validator.get_errors() if self._validator else []:
                    click.secho(f"      - {err}", fg="red")
            else:
                click.secho("\n✅  Validation PASSED", fg="green")

            click.echo("")

        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_configuration_service(self):
        """Load ConfigurationService from active profile configfile_paths when --deep is set.

        Returns the loaded service, or None when ``--deep`` is False.
        When ``--deep`` is True and config cannot be loaded for any reason,
        appends to ``self._errors`` (system error) and returns None — the caller
        must treat that as a hard failure (exit code 1).
        """
        if not self._deep:
            return None

        try:
            from strata.services.configuration_service import ConfigurationService
            from strata.utils.system import resolve_path

            if self._solution_controller.solution is None:
                self._errors.append("--deep requires an initialized workspace. Run `strata init` or remove --deep.")
                return None

            profile, _ = self._solution_controller.get_active_profile()
            if profile is None:
                self._errors.append(
                    "--deep requires an active profile. Run `strata profile activate <name>` or remove --deep."
                )
                return None

            configfile_paths = profile.configfile_paths or []
            if not configfile_paths:
                self._errors.append(
                    "--deep requires at least one configfile path on the active profile. "
                    "Add one with `strata ref configfile add` or remove --deep."
                )
                return None

            repo_map = self._solution_controller.get_repo_map()

            resolved_paths = []
            for entry in configfile_paths:
                name = str(entry.name)
                try:
                    resolved = resolve_path(str(self._work_path), str(entry.path), repo_map=repo_map)
                except ValueError as exc:
                    self.logger.debug(f"Config source '{name}': {exc}")
                    continue
                if not resolved.exists():
                    self.logger.debug(f"Config source '{name}': not found at {resolved}")
                    continue
                resolved_paths.append(str(resolved))

            if not resolved_paths:
                self._errors.append(
                    "--deep: no configfile_paths resolved to existing files. Check refs or remove --deep."
                )
                return None

            ConfigurationService.reset()
            config_svc = ConfigurationService.get_instance()
            success, load_errors = config_svc.load_from_paths(resolved_paths)
            if not success:
                self._errors.append(f"--deep: failed to load configuration: {'; '.join(load_errors)}")
                return None

            self.logger.debug(
                "ConfigurationService loaded for deep validation",
                profile=str(profile.name),
                files=len(resolved_paths),
            )
            return config_svc

        except Exception as exc:
            self._errors.append(f"--deep: unexpected error loading configuration: {exc}")
            return None
